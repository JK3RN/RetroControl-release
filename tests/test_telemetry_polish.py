import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import cairo
from retrocontrol.controller import Status
from retrocontrol.diagnostics import Diagnostics
from retrocontrol.dots import geometry, draw
from retrocontrol.processes import inspect, HZ
from retrocontrol.registry import Tool, Telemetry, load_registry
from retrocontrol.telemetry import Monitor, Attention, HISTORY
from retrocontrol.theme import palette


class PolishTests(unittest.TestCase):
    def tools(self, count=2):
        return {str(n): Tool(str(n), str(n), Path('/tmp'), ('python',), ('python',)) for n in range(count)}

    def test_background_shared_scan_stopped_identity_and_long_run(self):
        tools = self.tools(12)
        statuses = {key: Status('RUNNING', dict(pid=int(key)+1000, ticks=50, boot='test'), instances=1)
                    for key in tools}
        monitor = Monitor(tools)
        counters = {s.pid: dict(s.identity, ppid=1, mem=10*2**20, cpu=0) for s in statuses.values()}
        with patch('retrocontrol.telemetry.snapshot', return_value=counters) as scan, patch('retrocontrol.telemetry.tree', side_effect=lambda root, shared: {root['pid']: dict(shared[root['pid']])}) as tree:
            for n in range(1000):
                for record in counters.values(): record['cpu'] += HZ
                monitor.update(statuses, n * 2)
            self.assertEqual(scan.call_count, 1000)
            self.assertEqual(tree.call_count, 12000)
            for sample in monitor.samples.values():
                self.assertEqual(len(sample.cpu), HISTORY)
                self.assertEqual(len(sample.mem), HISTORY)
                self.assertEqual(len(sample.previous), 1)
                self.assertAlmostEqual(sample.value[0], 50)
            before = tuple(monitor.samples['1'].cpu)
            monitor.acknowledge('1')
            self.assertEqual(tuple(monitor.samples['1'].cpu), before)
            statuses['0'] = Status('STOPPED')
            monitor.update(statuses, 2000)
            self.assertIsNone(monitor.samples['0'].value)
            self.assertEqual(tree.call_count, 12011)
            statuses['1'].identity = dict(statuses['1'].identity, ticks=60)
            counters[1001]['ticks'] = 60
            monitor.update(statuses, 2002)
            self.assertEqual(len(monitor.samples['1'].cpu), 1)
            self.assertIsNone(monitor.samples['1'].value[0])
            for key in statuses: statuses[key] = Status('STOPPED')
            monitor.update(statuses, 2004)
            self.assertEqual(scan.call_count, 1002)  # no scan when all tools stopped

    def test_unselected_peak_is_latched_with_history(self):
        tools = self.tools()
        statuses = {key: Status('RUNNING', dict(pid=int(key)+1000, ticks=50, boot='test'), instances=1)
                    for key in tools}
        monitor = Monitor(tools)
        records = {s.pid: dict(s.identity, ppid=1, cpu=0, mem=10*2**20) for s in statuses.values()}
        with patch('retrocontrol.telemetry.snapshot', return_value=records), patch('retrocontrol.telemetry.tree', side_effect=lambda root, shared: {root['pid']: dict(shared[root['pid']])}):
            monitor.update(statuses, 0)
            records[1001]['cpu'] += HZ * 2
            monitor.update(statuses, 2)
            self.assertFalse(monitor.attention['0'].latched)
            self.assertTrue(monitor.attention['1'].latched)
            monitor.update(statuses, 4)
            monitor.update(statuses, 6)
            self.assertTrue(monitor.attention['1'].latched)
            self.assertEqual(max(v or 0 for v in monitor.samples['1'].cpu), 100)
            history = tuple(monitor.samples['1'].cpu)
            monitor.acknowledge('1')
            self.assertFalse(monitor.attention['1'].latched)
            self.assertEqual(tuple(monitor.samples['1'].cpu), history)
            records[1001]['cpu'] += HZ * 2
            monitor.update(statuses, 8)
            self.assertTrue(monitor.attention['1'].latched)

    def test_real_parallel_background_processes(self):
        processes = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) for _ in range(12)]
        try:
            time.sleep(.1)
            statuses = {str(n): Status('RUNNING', inspect(proc.pid), instances=1) for n, proc in enumerate(processes)}
            monitor = Monitor(self.tools(12))
            begin = time.perf_counter()
            monitor.update(statuses, 0)
            monitor.update(statuses, 2)
            self.assertLess(time.perf_counter() - begin, 1)
            for sample in monitor.samples.values():
                self.assertEqual(len(sample.mem), 2)
                self.assertGreater(sample.value[1], 0)
            processes[1].terminate(); processes[1].wait()
            monitor.update(statuses, 2.1)
            self.assertIsNone(monitor.samples['1'].value)
            self.assertIsNotNone(monitor.samples['2'].value)
        finally:
            for proc in processes:
                if proc.poll() is None: proc.terminate()
                proc.wait(timeout=5)

    def test_attention_hysteresis_ack_rearm_and_sparse_events(self):
        events = []
        emit = lambda *args: events.append(args)
        attention = Attention()
        thresholds = Telemetry(40, 500)
        value = lambda cpu, mem=200: (cpu, mem*2**20, 2)
        attention.update('tube', value(39), thresholds, 0, emit)
        self.assertFalse(attention.latched)
        attention.update('tube', value(41), thresholds, 2, emit)
        self.assertTrue(attention.latched)
        for n in range(3, 100): attention.update('tube', value(61), thresholds, n * 2, emit)
        self.assertEqual(len(events), 1)  # no raw-sample or rising-peak writes
        attention.update('tube', value(5), thresholds, 202, emit)
        self.assertTrue(attention.active)
        attention.update('tube', value(5), thresholds, 204, emit)
        self.assertFalse(attention.active)
        self.assertTrue(attention.latched)
        self.assertEqual([event[1] for event in events], ['ATTENTION', 'PEAK', 'NORMAL'])
        self.assertEqual(events[1][2][0], 61)
        attention.acknowledge()
        self.assertFalse(attention.latched)
        attention.update('tube', value(0, 600), thresholds, 206, emit)
        self.assertTrue(attention.latched)
        attention.acknowledge()
        attention.update('tube', value(0, 610), thresholds, 208, emit)
        self.assertFalse(attention.latched)  # acknowledge once per episode
        attention.update('tube', None, thresholds, 210, emit)
        self.assertEqual(events[-1][1], 'UNAVAILABLE')
        # Chattering thresholds cannot flood the disk.
        for n in range(1000):
            attention.update('tube', value(60 if n % 3 == 0 else 0), thresholds, 212 + n * 2, emit)
        self.assertLess(len(events), 110)

    def test_registry_thresholds_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'registry.json'
            base = dict(id='tool', path='.', launch=['python'], match=['python'])
            for metadata in ([], {'cpu_attention': True}, {'cpu_attention': 0}, {'mem_attention_mib': -1}, {'cpu_attention': float('nan')}, {'cpu_attention': float('inf')}, {'oops': 42}):
                path.write_text(json.dumps({'tools': [dict(base, telemetry=metadata)]}))
                with self.assertRaises(ValueError): load_registry(path)
            path.write_text(json.dumps({'tools': [dict(base, telemetry={'cpu_attention': 42.5, 'mem_attention_mib': 500})]}))
            self.assertEqual(load_registry(path)[0].telemetry, Telemetry(42.5, 500))
            path.write_text(json.dumps({'tools': [base]}))
            self.assertEqual(load_registry(path)[0].telemetry, Telemetry())

    def test_persistence_rotation_restart_and_io_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch('retrocontrol.diagnostics.MAX_BYTES', 500):
            log = Diagnostics(directory)
            self.assertFalse(log.path.exists())
            for n in range(1000): log.emit('tube', 'ATTENTION', (42.7, 286*2**20, 3))
            log.close()
            files = list(Path(directory).glob('telemetry.log*'))
            self.assertEqual(len(files), 3)
            self.assertTrue(all(path.stat().st_size <= 500 for path in files))
            restarted = Diagnostics(directory)
            restarted.emit('tube', 'NORMAL', (5, 281*2**20, 3))
            restarted.close()
            text = (Path(directory) / 'telemetry.log').read_text()
            self.assertIn('TELEMETRY NORMAL cpu=5.0% mem=281 MiB proc=3', text)
            failed = Diagnostics(Path(directory) / 'missing')
            failed.emit('tool', 'ATTENTION', (90, 100, 1))
            self.assertTrue(failed.failed)
            failed.emit('tool', 'NORMAL', None)

    def test_dot_resolution_idle_and_two_themes(self):
        idle = geometry([0]*90, 'cpu', 270, 32, 90)
        self.assertTrue(all(point[2] for point in idle))
        active = geometry([0, 1, 2, 3, 5, 40, 100], 'cpu', 270, 32, 90)
        self.assertEqual(len({round(y, 2) for _, y, _ in active}), 7)
        self.assertGreater(active[3][1], 20)  # normal activity stays low
        self.assertLess(active[-1][1], active[3][1])
        self.assertGreater(active[-1][1], 3)  # headroom at a peak
        self.assertEqual(geometry([None]*90, 'cpu', 270, 32, 90), [])
        memory = geometry([280*2**20, 281*2**20, 282*2**20, 350*2**20], 'mem', 270, 32, 90)
        self.assertEqual(len({y for _, y, _ in memory}), 4)
        self.assertTrue(all(p[2] for p in geometry([280*2**20]*90, 'mem', 270, 32, 90)))
        for accent in ('#00ff66', '#bb88ff'):
            colors = dict(palette(), accent=accent)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 270, 32)
            draw(cairo.Context(surface), [0, 1, 3, 40, 100], 'cpu', 270, 32, colors, 90)
            surface.flush()
            self.assertTrue(any(surface.get_data()))

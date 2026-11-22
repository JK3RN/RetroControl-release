import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from retrocontrol.controller import Status
from retrocontrol.processes import inspect
from retrocontrol.telemetry import Sampler, tree, lines, HISTORY, memory_text


class TelemetryTests(unittest.TestCase):
    def test_real_load_memory_tree_and_exit(self):
        code = '''import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
print(child.pid, flush=True)
input()
data = bytearray(32 * 1024 * 1024)
end = time.monotonic() + 1.5
while time.monotonic() < end: pass
print('loaded', flush=True)
input()
child.terminate(); child.wait()
print('ended', flush=True)
input()
'''
        proc = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        foreign = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        child = None
        try:
            child = int(proc.stdout.readline())
            # Child must finish exec before the baseline RSS measurement.
            time.sleep(.15)
            root = inspect(proc.pid)
            status = Status('RUNNING', root, instances=1)
            members = tree(root)
            self.assertEqual(set(members), {proc.pid, child})
            self.assertNotIn(foreign.pid, members)
            sample = Sampler()
            sample.update('a', status, 0)
            baseline = sample.value[1]
            self.assertIsNone(sample.value[0])
            self.assertEqual(sample.value[1], sum(p['mem'] for p in tree(root).values()))
            proc.stdin.write('\n'); proc.stdin.flush()
            self.assertEqual(proc.stdout.readline().strip(), 'loaded')
            sample.update('a', status, 2)
            self.assertGreater(sample.value[0], 30)
            self.assertGreater(sample.value[1], baseline + 25 * 2**20)
            proc.stdin.write('\n'); proc.stdin.flush()
            self.assertEqual(proc.stdout.readline().strip(), 'ended')
            sample.update('a', status, 4)
            self.assertEqual(sample.value[2], 1)
            self.assertLess(sample.value[0], 10)
            proc.terminate(); proc.wait(timeout=5)
            sample.update('a', status, 4.1)
            self.assertIsNone(sample.value)
            self.assertNotIn('MiB', lines(sample))
        finally:
            for p in (proc, foreign):
                if p.poll() is None:
                    p.kill()
                p.wait()
            proc.stdin.close(); proc.stdout.close()
            if child:
                try: os.kill(child, 15)
                except ProcessLookupError: pass

    def test_identity_edges_and_reuse(self):
        root = inspect(os.getpid())
        self.assertIsNone(tree(dict(root, ticks=root['ticks'] + 1)))
        # Reparented/reused descendants must fail the second identity/edge check.
        parent = dict(root, ppid=1, cpu=10, mem=20)
        child = dict(parent, pid=999999, ppid=root['pid'], ticks=root['ticks'] + 1)
        from pathlib import Path
        with patch('retrocontrol.telemetry.Path.iterdir', return_value=iter([Path(str(root['pid'])), Path('999999')])), patch('retrocontrol.telemetry.read', side_effect=[parent, child, parent, dict(child, ppid=1), parent]):
            self.assertEqual(set(tree(root)), {root['pid']})

    def test_history_switch_states_sampling_and_restart(self):
        root = inspect(os.getpid())
        status = Status('RUNNING', root, instances=1)
        sample = Sampler()
        record = dict(root, cpu=10, mem=2**20, ppid=1)
        with patch('retrocontrol.telemetry.tree', return_value={root['pid']: record}) as scan:
            for n in range(500):
                sample.update('a', status, n * 2)
            self.assertEqual(len(sample.cpu), HISTORY)
            self.assertEqual(len(sample.mem), HISTORY)
            sample.update('a', status, 998.5)
            self.assertEqual(scan.call_count, 500)
            sample.update('b', status, 1000)
            self.assertEqual(len(sample.cpu), 1)
            self.assertIsNone(sample.value[0])
            for state in (Status(), Status('ERROR'), Status('RUNNING', root, instances=2)):
                sample.update('b', state, 1002)
                self.assertIsNone(sample.value)
                self.assertEqual(len(sample.cpu), 0)
                self.assertEqual(lines(sample), 'CPU       —\nMEM       —\nPROC      —')
            sample.update('b', status, 1004)
            sample.update('b', Status('RUNNING', dict(root, ticks=root['ticks'] + 1), instances=1), 1006)
            self.assertEqual(len(sample.cpu), 1)
        self.assertEqual(memory_text(113 * 2**20), '113 MiB')
        self.assertEqual(memory_text(2**30), '1.0 GiB')


if __name__ == '__main__':
    unittest.main()

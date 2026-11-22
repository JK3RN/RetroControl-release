import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from retrocontrol.controller import Controller
from retrocontrol.processes import alive
from retrocontrol.registry import Tool, load_registry


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / 'instrument.py'
        self.script.write_text('import time\ntime.sleep(60)\n')
        self.tool = Tool('one', 'Instrument', self.root,
                         (sys.executable, str(self.script)), ('instrument.py',))
        self.control = Controller([self.tool], self.root / 'state')
        self.external = []

    def tearDown(self):
        for proc in self.external:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
        for key in self.control.tools:
            status = self.control.refresh()[key]
            if status.identity and alive(status.identity):
                os.kill(status.pid, signal.SIGTERM)
        time.sleep(.15)
        for worker in self.control.launchers:
            worker.wait(timeout=5)
        self.control.close()
        self.temp.cleanup()

    def wait_stopped(self, key='one'):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.control.refresh()
            if self.control.statuses[key].state != 'RUNNING':
                return
            time.sleep(.05)
        self.fail('Process remained running')

    def test_start_stop_restart_and_reopen(self):
        self.assertTrue(self.control.start('one'))
        first = self.control.statuses['one'].pid
        self.assertEqual(self.control.statuses['one'].state, 'RUNNING')
        self.assertTrue(self.control.start('one'))
        self.assertEqual(first, self.control.statuses['one'].pid)
        self.assertTrue(self.control.restart('one'))
        self.assertNotEqual(first, self.control.statuses['one'].pid)
        self.assertTrue(self.control.stop('one'))
        self.assertEqual(self.control.statuses['one'].state, 'STOPPED')
        self.assertTrue(self.control.stop('one'))
        self.control.close()
        self.control = Controller([self.tool], self.root / 'state')
        self.assertEqual(self.control.statuses['one'].state, 'STOPPED')

    def test_external_death_and_abnormal_exit(self):
        self.control.start('one')
        os.kill(self.control.statuses['one'].pid, signal.SIGKILL)
        self.wait_stopped()
        time.sleep(.1)
        self.control.refresh()
        self.assertEqual(self.control.statuses['one'].state, 'ERROR')
        self.assertEqual(self.control.statuses['one'].exit_code, -signal.SIGKILL)
        self.assertTrue(any('Process ended' in e for e in self.control.events))

    def test_standalone_discovery_and_stop(self):
        proc = subprocess.Popen(self.tool.launch, cwd=self.root, start_new_session=True)
        self.external.append(proc)
        time.sleep(.1)
        self.control.refresh()
        self.assertEqual(self.control.statuses['one'].pid, proc.pid)
        self.assertTrue(self.control.stop('one'))
        self.assertIsNone(self.control.statuses['one'].exit_code)

    def test_two_tools_survive_controller_exit(self):
        second_script = self.root / 'second.py'
        second_script.write_text('import time\ntime.sleep(60)\n')
        second = Tool('two', 'Second', self.root,
                      (sys.executable, str(second_script)), ('second.py',))
        self.control.close()
        self.control = Controller([self.tool, second], self.root / 'state')
        self.assertTrue(self.control.start('one'))
        self.assertTrue(self.control.start('two'))
        identities = [s.identity for s in self.control.statuses.values()]
        self.control.close()
        self.assertTrue(all(alive(i) for i in identities))
        self.control = Controller([self.tool, second], self.root / 'state')
        self.assertEqual([s.state for s in self.control.statuses.values()], ['RUNNING', 'RUNNING'])
        self.assertTrue(self.control.stop('one'))
        self.assertTrue(self.control.stop('two'))

    def test_tools_survive_actual_controller_process_exit(self):
        self.control.close()
        program = """import json, sys, time
from pathlib import Path
from retrocontrol.registry import Tool
from retrocontrol.controller import Controller
root = Path(sys.argv[1])
tool = Tool('one', 'Instrument', root, (sys.executable, str(root / 'instrument.py')), ('instrument.py',))
control = Controller([tool], root / 'state')
assert control.start('one')
print(json.dumps(control.statuses['one'].identity), flush=True)
time.sleep(60)
"""
        parent = subprocess.Popen([sys.executable, '-c', program, str(self.root)],
                                  stdout=subprocess.PIPE, text=True)
        try:
            identity = json.loads(parent.stdout.readline())
            parent.kill()
            parent.wait(timeout=5)
            self.assertTrue(alive(identity))
            self.control = Controller([self.tool], self.root / 'state')
            self.assertEqual(self.control.statuses['one'].pid, identity['pid'])
            self.assertTrue(self.control.stop('one'))
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)
            parent.stdout.close()

    def test_invalid_path_missing_command_and_immediate_exits(self):
        bad = [Tool('path', 'Missing path', self.root / 'missing', ('true',), ('true',)),
               Tool('empty', 'Empty command', self.root, (), ('never',)),
               Tool('cmd', 'Missing binary', self.root, ('/not/a/program',), ('never',)),
               Tool('fast', 'Fast exit', self.root, (sys.executable, '-c', 'raise SystemExit(7)'), ('never',)),
               Tool('zero', 'Fast clean exit', self.root, (sys.executable, '-c', 'pass'), ('never',))]
        self.control.close()
        self.control = Controller(bad, self.root / 'state')
        for tool in bad:
            with self.subTest(tool=tool.id):
                self.assertFalse(self.control.start(tool.id))
                self.assertEqual(self.control.statuses[tool.id].state, 'ERROR')

    def test_restart_failure(self):
        self.assertTrue(self.control.start('one'))
        self.script.unlink()
        self.assertFalse(self.control.restart('one'))
        self.assertEqual(self.control.statuses['one'].state, 'ERROR')

    def test_multiple_instances_are_not_stopped(self):
        for _ in range(2):
            proc = subprocess.Popen(self.tool.launch, cwd=self.root, start_new_session=True)
            self.external.append(proc)
        time.sleep(.1)
        self.control.refresh()
        self.assertEqual(self.control.statuses['one'].instances, 2)
        self.assertFalse(self.control.stop('one'))
        self.assertTrue(all(p.poll() is None for p in self.external))

    def test_event_history_bound_and_lock(self):
        for i in range(200):
            self.control.event('CONTROL', str(i))
        self.assertEqual(len(self.control.events), 100)
        with self.assertRaises(RuntimeError):
            Controller([self.tool], self.root / 'state')

    def test_pid_reuse_guard(self):
        self.control.start('one')
        identity = dict(self.control.statuses['one'].identity)
        identity['ticks'] += 1
        self.assertFalse(alive(identity))

    def test_session_adapter(self):
        # A launcher daemonizes and exits, as Servo Voice does; stop is a CLI.
        launcher = self.root / 'launcher.py'
        launcher.write_text("import pathlib, subprocess, sys, os, signal\np = pathlib.Path('daemon.pid')\nif len(sys.argv) > 1:\n os.kill(int(p.read_text()), signal.SIGTERM)\nelse:\n c = subprocess.Popen([sys.executable, 'instrument.py'], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n p.write_text(str(c.pid))\n")
        tool = Tool('one', 'Session', self.root, (sys.executable, str(launcher)),
                    ('instrument.py',), 'session', (sys.executable, str(launcher), 'quit'))
        self.control.close()
        self.control = Controller([tool], self.root / 'state')
        self.assertTrue(self.control.start('one'))
        self.assertTrue(self.control.stop('one'))
        self.assertEqual(self.control.statuses['one'].state, 'STOPPED')

    def test_registry_validation_and_relative_paths(self):
        registry = self.root / 'registry.json'
        registry.write_text(json.dumps({'tools': [{'id': 'x', 'path': '.', 'launch': [], 'match': ['test']}]}))
        self.assertEqual(load_registry(registry)[0].path, self.root)
        registry.write_text(json.dumps({'tools': [{'id': '../x', 'path': '.', 'match': ['test']}]}))
        with self.assertRaises(ValueError):
            load_registry(registry)


if __name__ == '__main__':
    unittest.main()

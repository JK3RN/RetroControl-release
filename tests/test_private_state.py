import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from retrocontrol.controller import Controller
from retrocontrol.registry import load_registry


class PrivateStateTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / 'state'

    def test_shared_directory(self):
        self.state.mkdir()
        self.state.chmod(0o777)
        with self.assertRaises(ValueError):
            Controller([], self.state)
        self.assertFalse((self.state / 'control.lock').exists())

    def test_linked_directory(self):
        self.state.symlink_to(self.root)
        with self.assertRaises(ValueError):
            Controller([], self.state)

    def test_linked_file(self):
        self.state.mkdir(mode=0o700)
        target = self.root / 'target'
        target.touch()
        for link in (os.link, os.symlink):
            lock = self.state / 'control.lock'
            link(target, lock)
            with self.assertRaises(ValueError):
                Controller([], self.state)
            lock.unlink()

    def test_missing_application(self):
        registry = self.root / 'registry.json'
        template = Path(__file__).resolve().parents[1] / 'registry.json'
        registry.write_text(template.read_text().replace('~/Applications/my-editor', './missing'))
        c = Controller(load_registry(registry), self.state)
        self.addCleanup(c.close)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual(c.statuses['editor'].state, 'STOPPED')
        self.assertFalse(c.start('editor'))
        self.assertEqual(c.statuses['editor'].state, 'ERROR')
        self.assertIn('directory missing', c.statuses['editor'].detail)

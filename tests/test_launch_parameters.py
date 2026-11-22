"""Regression tests for argv substitution and optional native OPEN."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from retrocontrol.controller import Controller
from retrocontrol.registry import LaunchParameter, Tool, load_registry
from retrocontrol.theme import palette, css


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / 'seed_tool.py'
        self.script.write_text("import json, pathlib, sys, time\npathlib.Path('arguments.json').write_text(json.dumps(sys.argv[1:]))\ntime.sleep(60)\n")
        self.tool = Tool('seed', 'Seed Tool', self.root,
                         (sys.executable, str(self.script), 'cosmic laboratory'), ('seed_tool.py',),
                         launch_parameters=(LaunchParameter('seed', 'Seed', 2, 'cosmic laboratory'),))
        self.control = Controller([self.tool], self.root / 'state')

    def tearDown(self):
        self.control.stop('seed')
        for worker in self.control.launchers:
            worker.wait(timeout=5)
        self.control.close()
        self.temp.cleanup()

    def arguments(self):
        return json.loads((self.root / 'arguments.json').read_text())

    def test_default_changed_seed_restart_and_receipt_recovery(self):
        self.assertTrue(self.control.start('seed'))
        self.assertEqual(self.arguments(), ['cosmic laboratory'])
        changed = 'moon & stars; $(touch never-created) "quoted" ä'
        self.assertTrue(self.control.restart('seed', {'seed': changed}))
        self.assertEqual(self.arguments(), [changed])
        self.assertFalse((self.root / 'never-created').exists())
        self.control.close()
        self.control = Controller([self.tool], self.root / 'state')
        self.assertEqual(self.control.statuses['seed'].state, 'RUNNING')
        self.assertTrue(self.control.stop('seed'))

    def test_invalid_parameters_do_not_stop_running_tool(self):
        self.control.start('seed')
        pid = self.control.statuses['seed'].pid
        for values in ({'seed': ''}, {'seed': '  '}, {'seed': '\0'}, {'seed': 42}, {'unknown': 'x'}):
            self.assertFalse(self.control.restart('seed', values))
            self.assertEqual(self.control.statuses['seed'].pid, pid)
            self.assertEqual(self.control.statuses['seed'].state, 'RUNNING')

    def test_unsupported_open_is_silent(self):
        events = list(self.control.events)
        with patch.object(self.control, 'command') as command:
            self.assertFalse(self.control.open('seed'))
            command.assert_not_called()
        self.assertEqual(list(self.control.events), events)
        self.assertFalse(self.tool.supports_open)

    def test_native_open_uses_registered_command(self):
        self.control.start('seed')
        native = (sys.executable, '-c', "from pathlib import Path; Path('opened').write_text('native')")
        self.control.tools['seed'] = replace(self.tool, open=native)
        self.assertTrue(self.control.tools['seed'].supports_open)
        self.assertTrue(self.control.open('seed'))
        self.assertEqual((self.root / 'opened').read_text(), 'native')

    def test_invalid_registry_parameter_metadata(self):
        base = {'id': 'seed', 'label': 'Seed', 'argv_index': 1, 'default': 'default'}
        registry = self.root / 'registry.json'
        invalid = [dict(base, type='number'), dict(base, argv_index=0),
                   dict(base, argv_index=True), dict(base, argv_index=9),
                   dict(base, default='different'), dict(base, label=''),
                   dict(base, id='../seed')]
        for metadata in invalid:
            registry.write_text(json.dumps({'tools': [{'id': 'view', 'path': '.', 'launch': ['app', 'default'],
                                                       'match': ['app'], 'launch_parameters': [metadata]}]}))
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                load_registry(registry)
        registry.write_text('[]')
        with self.assertRaises(ValueError):
            load_registry(registry)

    def test_theme_changes_and_gtk_css_parsing(self):
        state = self.root / 'xdg-state'
        theme = state / 'omarchy/current/theme/colors.toml'
        theme.parent.mkdir(parents=True)
        with patch.dict(os.environ, {'XDG_STATE_HOME': str(state), 'XDG_CONFIG_HOME': str(self.root / 'config')}):
            theme.write_text('background = "#111111"\ncolor3 = "#ccbbaa"\ncolor4 = "#88aabb"\n')
            first = palette()
            self.assertEqual(first['warning'], '#ccbbaa')
            theme.write_text('background = "#222222"\ncolor3 = "#aabbcc"\ncolor4 = "#bbaa88"\n')
            second = palette()
            self.assertNotEqual(first, second)
            import gi
            gi.require_version('Gtk', '4.0')
            from gi.repository import Gtk
            provider = Gtk.CssProvider()
            errors = []
            provider.connect('parsing-error', lambda _, section, error: errors.append(str(error)))
            provider.load_from_data(css(second).encode())
            self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()

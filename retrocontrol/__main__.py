import argparse
import json
from pathlib import Path
import sys
from .controller import Controller
from .registry import load_registry


def main():
    parser = argparse.ArgumentParser(description='RetroTools control instrument')
    parser.add_argument('--registry', type=Path, default=Path(__file__).resolve().parent.parent / 'registry.json')
    parser.add_argument('--state-dir', type=Path, help='Override private receipt directory')
    parser.add_argument('--status', action='store_true', help='Print real process states as JSON; no UI')
    args = parser.parse_args()
    control = None
    try:
        control = Controller(load_registry(args.registry), args.state_dir)
        if args.status:
            print(json.dumps({key: {'state': status.state, 'pid': status.pid,
                                    'uptime': status.elapsed, 'exit_code': status.exit_code,
                                    'detail': status.detail}
                              for key, status in control.statuses.items()}, indent=2))
            control.close()
            return 0
        from .ui import Application, Gtk, Gdk
        if not Gtk.init_check() or Gdk.Display.get_default() is None:
            raise RuntimeError('No GTK display available; start rctl in your desktop session')
        app = Application(control)
        return app.run([sys.argv[0]])
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        if control:
            control.close()
        print(f'rctl: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

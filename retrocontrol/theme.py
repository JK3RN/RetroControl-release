"""Semantic colors from the same Omarchy palette paths as RetroView."""
from pathlib import Path
import os
import re
import tomllib


def palette():
    colors = dict(background='#12191c', foreground='#e8eeee', accent='#00FF66',
                  warning='#FFCC00', critical='#e58a8a', secondary='#FFCC00')
    for base in (Path(os.getenv('XDG_STATE_HOME', Path.home() / '.local/state')),
                 Path(os.getenv('XDG_CONFIG_HOME', Path.home() / '.config'))):
        path = base / 'omarchy/current/theme/colors.toml'
        try:
            data = tomllib.loads(path.read_text())
            for key, fallback in (('background', 'color0'), ('foreground', 'color7'),
                                  ('accent', 'color4'), ('warning', 'color3'), ('critical', 'color1'),
                                  ('secondary', 'color3')):
                named = {'accent': 'blue', 'warning': 'yellow', 'critical': 'red', 'secondary': 'yellow'}.get(key, key)
                value = data.get(key, data.get(fallback, data.get(named, colors[key])))
                if isinstance(value, str) and re.fullmatch('#[0-9a-fA-F]{6}', value):
                    colors[key] = value
            return colors
        except (OSError, ValueError):
            continue
    return colors


def css(colors):
    c = colors
    return f'''
    .retrocontrol {{ background: {c['background']}; color: {c['foreground']}; }}
    .retrocontrol * {{ font-family: monospace; font-size: 13px; color: {c['foreground']}; }}
    .retrocontrol .dim {{ opacity: 0.72; }}
    .retrocontrol .attention {{ color: {c['secondary']}; font-size: 11px; }}
    .retrocontrol .attention-idle {{ color: {c['foreground']}; opacity: 0.25; font-size: 11px; }}
    .retrocontrol .shortcut {{ color: {c['secondary']}; }}
    .retrocontrol .section {{ color: {c['secondary']}; font-size: 11px; letter-spacing: 1px; }}
    .retrocontrol .tool-name {{ font-weight: bold; }}
    .retrocontrol .tool-state {{ font-size: 11px; }}
    .retrocontrol .stopped {{ opacity: 0.70; }}
    .retrocontrol .ambiguous {{ color: {c['warning']}; }}
    .retrocontrol .heading {{ color: {c['accent']}; font-weight: bold; }}
    .retrocontrol .running {{ color: {c['accent']}; }}
    .retrocontrol .error {{ color: {c['critical']}; }}
    .retrocontrol .bay {{ border: 1px solid alpha({c['foreground']}, 0.24); padding: 10px; }}
    .retrocontrol button {{ background: transparent; background-image: none; border-radius: 0;
        border: 1px solid alpha({c['foreground']}, 0.35); box-shadow: none; padding: 6px 10px; }}
    .retrocontrol button:hover {{ background: alpha({c['secondary']}, 0.12); }}
    .retrocontrol row {{ border-left: 2px solid transparent; }}
    .retrocontrol row:selected {{ background: alpha({c['secondary']}, 0.08); border-left-color: {c['secondary']}; }}
    .retrocontrol row:focus-visible {{ outline: 1px solid {c['secondary']}; outline-offset: -1px; }}
    .retrocontrol button:focus-visible {{ outline: 1px solid {c['secondary']}; outline-offset: 2px; }}
    .retrocontrol entry {{ background: transparent; border-radius: 0; border: 1px solid alpha({c['foreground']}, 0.4); padding: 8px; }}
    .retrocontrol entry:focus-within {{ border-color: {c['secondary']}; }}
    .retrocontrol button:disabled {{ opacity: 0.4; }}
    .retrocontrol list, .retrocontrol row, .retrocontrol textview,
    .retrocontrol textview text {{ background: transparent; }}
    .retrocontrol row {{ padding: 8px 4px; }}
    .retrocontrol separator {{ background: alpha({c['foreground']}, 0.25); min-height: 1px; }}
    '''

"""Small validated registry; commands are argv arrays, never shell strings."""
from dataclasses import dataclass
from pathlib import Path
import json
import math
import os
import re


@dataclass(frozen=True)
class LaunchParameter:
    id: str
    label: str
    argv_index: int
    default: str
    type: str = 'text'


@dataclass(frozen=True)
class Telemetry:
    cpu_attention: float = 80.0
    mem_attention_mib: float = 1024.0


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    path: Path
    launch: tuple[str, ...]
    match: tuple[str, ...]
    adapter: str = 'process'
    stop: tuple[str, ...] = ()
    open: tuple[str, ...] = ()
    log_path: str = ''
    launch_parameters: tuple[LaunchParameter, ...] = ()

    telemetry: Telemetry = Telemetry()

    @property
    def supports_open(self):
        return bool(self.open)

    def launch_command(self, values=None):
        values = {} if values is None else values
        if not isinstance(values, dict) or set(values) - {p.id for p in self.launch_parameters}:
            raise ValueError('Unknown launch parameter')
        argv = list(self.launch)
        for parameter in self.launch_parameters:
            value = values.get(parameter.id, parameter.default)
            if not isinstance(value, str) or not value.strip() or '\0' in value:
                raise ValueError(f'{parameter.label}: enter a non-empty text value')
            argv[parameter.argv_index] = value
        return tuple(argv)


def load_registry(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError('Registry must be an object')
    entries = data.get('tools')
    if not isinstance(entries, list):
        raise ValueError('Registry requires a tools array')
    tools = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Tool entry must be an object')
        key = entry.get('id', '')
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', key) or key in seen:
            raise ValueError('Tool IDs must be unique and contain letters, digits, _ or -')
        seen.add(key)
        for field in ('launch', 'match', 'stop', 'open'):
            value = entry.get(field, [])
            if not isinstance(value, list) or any(not isinstance(x, str) or not x or '\0' in x for x in value):
                raise ValueError(f'{key}: {field} must be an argv array')
        if not entry.get('match'):
            raise ValueError(f'{key}: explicit process match required')
        if entry.get('adapter', 'process') not in ('process', 'session', 'phosphor-json'):
            raise ValueError(f'{key}: unsupported adapter')
        for field in ('name', 'path', 'log_path'):
            if not isinstance(entry.get(field, ''), str):
                raise ValueError(f'{key}: {field} must be a string')
        if not entry.get('path'):
            raise ValueError(f'{key}: path required')
        parameters = entry.get('launch_parameters', [])
        if not isinstance(parameters, list):
            raise ValueError(f'{key}: launch_parameters must be an array')
        parsed = []
        ids, indices = set(), set()
        for parameter in parameters:
            if not isinstance(parameter, dict):
                raise ValueError(f'{key}: invalid launch parameter')
            pid, index = parameter.get('id'), parameter.get('argv_index')
            if (not isinstance(pid, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', pid)
                    or pid in ids or type(index) is not int or index < 1
                    or index >= len(entry.get('launch', [])) or index in indices):
                raise ValueError(f'{key}: parameter requires a unique ID and argument index >= 1')
            if parameter.get('type', 'text') != 'text':
                raise ValueError(f'{key}: only text launch parameters are supported')
            default = parameter.get('default', entry['launch'][index])
            label = parameter.get('label', pid)
            if (not isinstance(default, str) or not default.strip() or '\0' in default
                    or default != entry['launch'][index] or not isinstance(label, str) or not label.strip()):
                raise ValueError(f'{key}: invalid parameter label/default; default must match launch argument')
            ids.add(pid)
            indices.add(index)
            parsed.append(LaunchParameter(pid, label, index, default))
        telemetry = entry.get('telemetry', {})
        if not isinstance(telemetry, dict) or set(telemetry) - {'cpu_attention', 'mem_attention_mib'}:
            raise ValueError(f'{key}: invalid telemetry metadata')
        for name, value in telemetry.items():
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{key}: {name} must be a finite positive number')
        thresholds = Telemetry(**telemetry)
        root = (path.parent / Path(entry['path']).expanduser()).resolve()
        state = os.getenv('XDG_STATE_HOME', str(Path.home() / '.local/state'))
        log = entry.get('log_path', '').replace('${XDG_STATE_HOME}', state)
        tools.append(Tool(key, entry.get('name') or key, root,
                          tuple(entry.get('launch', [])), tuple(entry['match']),
                          entry.get('adapter', 'process'), tuple(entry.get('stop', [])),
                          tuple(entry.get('open', [])), log, tuple(parsed), thresholds))
    return tools

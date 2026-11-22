"""Registry-independent lifecycle actions and bounded control events."""
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import fcntl
import json
import os
import stat
import signal
import subprocess
import sys
import time
import threading
from .processes import alive, discover, uptime


@dataclass
class Status:
    state: str = 'STOPPED'
    identity: dict | None = None
    exit_code: int | None = None
    detail: str = ''
    instances: int = 0

    @property
    def pid(self):
        return self.identity['pid'] if self.identity else None

    @property
    def elapsed(self):
        return uptime(self.identity) if self.identity else None


def private_state(path):
    path = path.absolute()
    if '..' in path.parts or path == Path('/'):
        raise ValueError('Choose a private state directory')
    uid = os.getuid()
    for directory in (*reversed(path.parents), path):
        try:
            info = directory.lstat()
        except FileNotFoundError:
            directory.mkdir(mode=0o700, exist_ok=True)
            info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid):
            raise ValueError('Unsafe state directory owner or type')
        if directory == path:
            if info.st_uid != uid or info.st_mode & 0o077:
                raise ValueError('State directory must be private (0700)')
        elif info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise ValueError('Unsafe writable state directory ancestor')
    for entry in path.iterdir():
        try:
            info = entry.lstat()
        except FileNotFoundError:
            continue  # A detached worker can atomically replace a receipt.
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid
                or info.st_nlink != 1 or info.st_mode & 0o022):
            raise ValueError('Unsafe file in state directory')
    return path


class Controller:
    def __init__(self, tools, state_dir=None):
        self.tools = {tool.id: tool for tool in tools}
        self.root = Path(state_dir or Path(os.getenv('XDG_STATE_HOME', Path.home() / '.local/state')) / 'retrocontrol')
        self.root = private_state(self.root)
        self.lock = self.root.joinpath('control.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError('RetroControl already controls this state directory')
        self.events = deque(maxlen=100)
        self.statuses = {key: Status() for key in self.tools}
        self.errors = {}
        self.stopping = set()
        self.launchers = []
        self.event('CONTROL', 'RetroControl started')
        self.refresh()

    def close(self):
        # Deliberately leave workers and tools alive.
        self.lock.close()
        for worker in self.launchers:
            threading.Thread(target=worker.wait, daemon=True).start()
        self.launchers = []

    def event(self, key, message):
        self.events.append(f'{time.strftime("%H:%M:%S")}  [{key.upper()}] {message}')

    def receipt(self, key):
        return self.root / f'{key}.json'

    def signature(self, key):
        tool = self.tools[key]
        return json.dumps([str(tool.path), tool.launch, tool.adapter])

    def read_receipt(self, key):
        try:
            data = json.loads(self.receipt(key).read_text())
            if not isinstance(data, dict) or data.get('tool') != self.signature(key):
                return {}
            identity = data.get('identity')
            if identity is not None and (not isinstance(identity, dict) or
                    not all(isinstance(identity.get(k), int) for k in ('pid', 'ticks')) or
                    not isinstance(identity.get('boot'), str)):
                return {}
            return data
        except (OSError, ValueError):
            return {}

    def refresh(self):
        self.launchers = [p for p in self.launchers if p.poll() is None]
        for key, tool in self.tools.items():
            previous = self.statuses[key]
            receipt = self.read_receipt(key)
            identity = receipt.get('identity')
            found = discover(tool)
            if alive(identity) and all(p['pid'] != identity['pid'] for p in found):
                found.insert(0, identity)
            if found and identity and not alive(identity) and all(p['pid'] != identity['pid'] for p in found):
                # A new standalone instance replaces the previous run's receipt.
                self.receipt(key).unlink(missing_ok=True)
                receipt = {}
                identity = None
            error = self.errors.get(key) or receipt.get('error')
            code = receipt.get('exit_code')
            try:
                intent = json.loads(self.root.joinpath(f'{key}.stop').read_text())
                expected_stop = bool(identity and intent == identity)
            except (OSError, ValueError):
                expected_stop = False
            if found:
                selected = next((p for p in found if identity and p['pid'] == identity['pid']), found[0])
                detail = error or ''
                if tool.adapter == 'phosphor-json':
                    if identity and selected['pid'] == identity['pid']:
                        detail = 'READY' if receipt.get('ready') else 'STARTING / waiting for first present'
                        if receipt.get('status_dropped', 0):
                            detail += f" / status_dropped={receipt['status_dropped']}"
                    else:
                        detail = 'RUNNING / readiness unknown (standalone)'
                status = Status('RUNNING', selected, None, 'Multiple instances: actions disabled' if len(found) > 1 else detail, len(found))
            elif error:
                status = Status('ERROR', exit_code=code, detail=error)
            elif code is not None and code != 0 and key not in self.stopping and not expected_stop:
                status = Status('ERROR', exit_code=code, detail='Process exited abnormally')
            else:
                status = Status('STOPPED', exit_code=code)
            if previous.state == 'RUNNING' and status.state != 'RUNNING':
                expected = key in self.stopping or expected_stop
                if not expected and code is None:
                    status.detail = 'External process ended; exit code unavailable'
                self.event(key, 'Tool stopped' if expected else f'Process ended: {status.state}, exit={code if code is not None else "unknown"}')
            elif previous.state != 'RUNNING' and status.state == 'RUNNING':
                self.event(key, f'Tool running (PID {status.pid})')
            self.statuses[key] = status
        return self.statuses

    def fail(self, key, exc):
        self.errors[key] = str(exc)
        self.event(key, f'Action failed: {exc}')
        self.refresh()
        return False

    def command(self, tool, argv):
        result = subprocess.run(argv, cwd=tool.path, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=12, start_new_session=True)
        if result.returncode:
            raise RuntimeError(f'Command failed (exit {result.returncode})')

    def start(self, key, parameters=None):
        tool = self.tools[key]
        self.refresh()
        if self.statuses[key].state == 'RUNNING':
            return True
        self.errors.pop(key, None)
        self.stopping.discard(key)
        try:
            launch = tool.launch_command(parameters)
            if not tool.path.is_dir():
                raise RuntimeError(f'Tool directory missing: {tool.path}')
            if not tool.launch:
                raise RuntimeError('Launch command missing')
            self.receipt(key).unlink(missing_ok=True)
            self.root.joinpath(f'{key}.stop').unlink(missing_ok=True)
            if tool.adapter == 'session':
                self.command(tool, launch)
            else:
                project = Path(__file__).resolve().parent.parent
                env = dict(os.environ, PYTHONPATH=str(project))
                child = subprocess.Popen([sys.executable, '-m', 'retrocontrol.worker',
                                          str(self.receipt(key)), self.signature(key), str(tool.path), *launch],
                                         cwd=project, env=env, start_new_session=True,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, close_fds=True)
                self.launchers.append(child)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                time.sleep(.05)
                self.refresh()
                if self.statuses[key].state == 'ERROR':
                    raise RuntimeError(self.statuses[key].detail)
                if self.statuses[key].state == 'RUNNING':
                    time.sleep(.2)
                    self.refresh()
                    if self.statuses[key].state != 'RUNNING':
                        raise RuntimeError('Tool exited immediately after launch')
                    self.event(key, 'Start completed')
                    return True
                if self.read_receipt(key).get('exit_code') is not None:
                    raise RuntimeError('Tool exited immediately after launch')
            raise RuntimeError('Launch did not produce a running tool within 3s')
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return self.fail(key, exc)

    def stop(self, key):
        tool = self.tools[key]
        self.refresh()
        status = self.statuses[key]
        if status.state != 'RUNNING':
            self.event(key, 'Already stopped')
            return True
        if status.instances > 1:
            return self.fail(key, RuntimeError('Multiple instances; stop individually outside RetroControl'))
        self.stopping.add(key)
        try:
            identity = status.identity
            if not alive(identity):
                self.refresh()
                return True
            self.root.joinpath(f'{key}.stop').write_text(json.dumps(identity))
            if tool.stop:
                self.command(tool, tool.stop)
            else:
                # Individual main process: tool owns cleanup of its children.
                os.kill(identity['pid'], signal.SIGTERM)
            deadline = time.monotonic() + 5
            while alive(identity) and time.monotonic() < deadline:
                time.sleep(.05)
            if alive(identity):
                raise RuntimeError('Tool did not stop after SIGTERM; no forced kill performed')
            time.sleep(.1)  # let receipt writer reap the process
            self.refresh()
            return True
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.stopping.discard(key)
            self.root.joinpath(f'{key}.stop').unlink(missing_ok=True)
            return self.fail(key, exc)

    def restart(self, key, parameters=None):
        try:
            self.tools[key].launch_command(parameters)
        except ValueError as exc:
            return self.fail(key, exc)
        self.event(key, 'Restart requested')
        return self.stop(key) and self.start(key, parameters)

    def open(self, key):
        tool = self.tools[key]
        if not tool.supports_open:
            return False
        self.refresh()
        if self.statuses[key].state != 'RUNNING':
            self.event(key, 'OPEN requires a running tool')
            return False
        try:
            self.command(tool, tool.open)
            self.event(key, 'OPEN requested')
            return True
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return self.fail(key, exc)

"""Detached receipt writer. Survives the control UI and reaps its tool."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
from .processes import inspect


def write(path, value):
    target = Path(path)
    temp = target.with_suffix(f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value))
    temp.chmod(0o600)
    temp.replace(target)


def run_phosphor(receipt, signature, cwd, argv):
    """Drain both channels continuously; readiness never gates process supervision."""
    log_path = Path(receipt).with_suffix('.stderr.log')
    child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    value = {'tool': signature, 'identity': inspect(child.pid), 'exit_code': None,
             'ready': False, 'status_dropped': 0}
    write(receipt, value)
    pending = b''
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ, 'status')
        selector.register(child.stderr, selectors.EVENT_READ, 'stderr')
        while selector.get_map():
            for key, _ in selector.select(.5):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == 'stderr':
                    # Bound diagnostics independently of app telemetry, keep one backup.
                    try:
                        if log_path.exists() and log_path.stat().st_size + len(chunk) > 262144:
                            log_path.replace(log_path.with_suffix('.log.1'))
                        fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                        with os.fdopen(fd, 'ab') as output:
                            output.write(chunk)
                    except OSError:
                        pass  # A diagnostic failure must never stop draining the pipe.
                else:
                    pending += chunk
                    lines = pending.split(b'\n')
                    pending = lines.pop()
                    if len(pending) > 16384:
                        pending = b''
                    changed = False
                    for line in lines:
                        if len(line) > 16384:
                            continue
                        try:
                            record = json.loads(line)
                            if (not isinstance(record, dict) or record.get('schema') != 1
                                    or record.get('program') != 'phosphor-field'
                                    or not isinstance(record.get('data'), dict)):
                                continue
                            if record.get('event') == 'ready':
                                value['ready'] = True
                                changed = True
                            dropped = record['data'].get('status_dropped')
                            if type(dropped) is int and dropped >= 0 and dropped != value['status_dropped']:
                                value['status_dropped'] = dropped
                                changed = True
                        except (ValueError, UnicodeError):
                            continue
                    if changed:
                        write(receipt, value)
    child.stdout.close()
    child.stderr.close()
    value['exit_code'] = child.wait()
    write(receipt, value)


def main():
    receipt, signature, cwd, *argv = sys.argv[1:]
    try:
        if json.loads(signature)[2] == 'phosphor-json':
            run_phosphor(receipt, signature, cwd, argv)
            return
        child = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        identity = inspect(child.pid)
        write(receipt, {'tool': signature, 'identity': identity, 'exit_code': None})
        code = child.wait()
        write(receipt, {'tool': signature, 'identity': identity, 'exit_code': code})
    except Exception as exc:
        write(receipt, {'tool': signature, 'identity': None, 'exit_code': None, 'error': str(exc)})


if __name__ == '__main__':
    main()

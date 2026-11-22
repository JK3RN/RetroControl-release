"""Linux process identities include boot ID and start ticks to avoid PID reuse."""
from pathlib import Path
import os
import shutil

BOOT = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
HZ = os.sysconf('SC_CLK_TCK')


def inspect(pid):
    try:
        root = Path('/proc') / str(pid)
        if root.stat().st_uid != os.getuid():
            return None
        stat = root.joinpath('stat').read_text().rsplit(')', 1)[1].split()
        if stat[0] in ('Z', 'X'):
            return None
        argv = [x.decode(errors='replace') for x in root.joinpath('cmdline').read_bytes().split(b'\0') if x]
        return {'pid': int(pid), 'ticks': int(stat[19]), 'boot': BOOT,
                'argv': argv, 'pgid': int(stat[2])}
    except (OSError, ValueError, IndexError):
        return None


def alive(identity):
    current = inspect(identity['pid']) if identity else None
    return bool(current and all(current[k] == identity.get(k) for k in ('pid', 'ticks', 'boot')))


def discover(tool):
    found = []
    for root in Path('/proc').iterdir():
        if not root.name.isdigit():
            continue
        proc = inspect(int(root.name))
        if not proc:
            continue
        # Exact argv/basename tokens plus installation directory. No substring pgrep.
        tokens = set(proc['argv']) | {Path(x).name for x in proc['argv']}
        program_tokens = set(proc['argv'][:2]) | {Path(x).name for x in proc['argv'][:2]}
        if tool.match[0] not in program_tokens or not all(x in tokens for x in tool.match):
            continue
        try:
            cwd = root.joinpath('cwd').resolve(strict=True)
            paths = [(cwd / x).resolve() for x in proc['argv'] if '/' in x]
            installed = shutil.which(tool.launch[0]) if tool.launch and '/' not in tool.launch[0] else None
            known_launcher = Path(installed).resolve() if installed else None
            if cwd == tool.path or any(p.is_relative_to(tool.path) for p in paths) or (known_launcher and known_launcher in paths):
                found.append(proc)
        except OSError:
            continue
    return sorted(found, key=lambda p: p['ticks'])


def uptime(identity):
    return max(0, int(float(Path('/proc/uptime').read_text().split()[0]) - identity['ticks'] / HZ))

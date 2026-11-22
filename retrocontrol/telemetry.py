"""Read-only telemetry for a verified root and its current same-user descendants."""
from collections import deque
from pathlib import Path
import os
import time
from .processes import BOOT, HZ

INTERVAL = 2.0
HISTORY = 90
PAGE = os.sysconf('SC_PAGE_SIZE')


def read(pid):
    try:
        root = Path('/proc') / str(pid)
        if root.stat().st_uid != os.getuid():
            return None
        fields = root.joinpath('stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] in ('Z', 'X'):
            return None
        return dict(pid=int(pid), ticks=int(fields[19]), boot=BOOT,
                    ppid=int(fields[1]), cpu=int(fields[11]) + int(fields[12]),
                    mem=max(0, int(fields[21])) * PAGE)
    except (OSError, ValueError, IndexError):
        return None


def identity(proc):
    return tuple(proc[k] for k in ('pid', 'ticks', 'boot'))


def snapshot():
    found = {}
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            proc = read(int(path.name))
            if proc:
                found[proc['pid']] = proc
    return found


def tree(root, processes=None):
    """Snapshot ancestry, then recheck every edge and identity before counting.

    No name, process group or session heuristics; reparented children are excluded.
    A changed root invalidates the entire sample.
    """
    if not root:
        return None
    snapshot_data = snapshot() if processes is None else processes
    first = snapshot_data.get(root['pid'])
    if not first or identity(first) != identity(root):
        return None
    children = {}
    for proc in snapshot_data.values():
        children.setdefault(proc['ppid'], []).append(proc)
    verified = {}
    pending = [first]
    while pending:
        old = pending.pop()
        current = read(old['pid'])
        if not current or identity(current) != identity(old) or current['ppid'] != old['ppid']:
            continue
        if old['pid'] != root['pid']:
            parent = verified.get(current['ppid'])
            live_parent = read(current['ppid']) if parent else None
            if not live_parent or identity(live_parent) != identity(parent):
                continue
        verified[current['pid']] = current
        pending.extend(children.get(current['pid'], []))
    last = read(root['pid'])
    if not last or identity(last) != identity(root) or root['pid'] not in verified:
        return None
    return verified


class Sampler:
    """One tool, bounded history and per-process CPU deltas."""
    def __init__(self):
        self.key = None
        self.previous = {}
        self.last = None
        self.value = None
        self.cpu = deque(maxlen=HISTORY)
        self.mem = deque(maxlen=HISTORY)

    def reset(self):
        self.previous.clear()
        self.last = None
        self.value = None
        self.cpu.clear()
        self.mem.clear()

    def update(self, key, status, now=None, processes=None):
        token = (key, identity(status.identity)) if status.identity else (key, None)
        if token != self.key:
            self.reset()
            self.key = token
        if status.state != 'RUNNING' or status.instances != 1 or not status.identity:
            self.reset()
            return
        now = time.monotonic() if now is None else now
        if self.last is not None and now - self.last < INTERVAL:
            # Invalidate stale data immediately when the root has ended/reused its PID.
            current = read(status.pid)
            if not current or identity(current) != identity(status.identity):
                self.reset()
            return
        processes = tree(status.identity, processes) if processes is not None else tree(status.identity)
        if not processes:
            self.reset()
            return
        counters = {identity(p): p['cpu'] for p in processes.values()}
        cpu = None
        if self.last is not None:
            delta = sum(max(0, ticks - self.previous.get(token, ticks)) for token, ticks in counters.items())
            cpu = delta / HZ / (now - self.last) * 100
        memory = sum(p['mem'] for p in processes.values())
        self.value = (cpu, memory, len(processes))
        self.cpu.append(cpu)
        self.mem.append(memory)
        self.previous = counters
        self.last = now


def memory_text(value):
    return f'{value / 2**30:.1f} GiB' if value >= 2**30 else f'{value / 2**20:.0f} MiB'


def lines(sampler):
    if sampler.value is None:
        return 'CPU       —\nMEM       —\nPROC      —'
    cpu, mem, count = sampler.value
    cpu_text = '—' if cpu is None else f'{cpu:.1f} %'
    return f'CPU       {cpu_text}\nMEM       {memory_text(mem)}\nPROC      {count}'


class Attention:
    """Episode hysteresis and a separate user-acknowledged latch."""
    def __init__(self):
        self.latched = False
        self.active = False
        self.normal_count = 0
        self.peak = None
        self.last_logged = -float('inf')
        self.logging_episode = False

    def acknowledge(self):
        self.latched = False

    def update(self, key, value, thresholds, now, emit):
        if value is None:
            self.finish(key, now, emit, 'UNAVAILABLE')
            return
        cpu, mem, count = value
        high = (cpu is not None and cpu >= thresholds.cpu_attention) or mem >= thresholds.mem_attention_mib * 2**20
        normal = (cpu is None or cpu < thresholds.cpu_attention * .8) and mem < thresholds.mem_attention_mib * 2**20 * .8
        if high and not self.active:
            self.active = True
            self.latched = True
            self.peak = value
            # At most one persisted episode per minute and tool, even with flapping.
            self.logging_episode = now - self.last_logged >= 60
            if self.logging_episode:
                emit(key, 'ATTENTION', value)
                self.last_logged = now
        if self.active:
            old = self.peak
            self.peak = (max(cpu or 0, old[0] or 0), max(mem, old[1]), max(count, old[2]))
            self.normal_count = self.normal_count + 1 if normal else 0
            if self.normal_count >= 2:
                self.finish(key, now, emit, 'NORMAL', value)

    def finish(self, key, now, emit, reason, value=None):
        if self.active and self.logging_episode:
            # One summary per episode, never a write for every increasing sample.
            emit(key, 'PEAK', self.peak)
            emit(key, reason, value)
        self.active = False
        self.normal_count = 0
        self.peak = None
        self.logging_episode = False


class Monitor:
    """All registry tools; one shared snapshot per due sampling tick."""
    def __init__(self, tools, emit=lambda *args: None):
        self.tools = tools
        self.samples = {key: Sampler() for key in tools}
        self.attention = {key: Attention() for key in tools}
        self.emit = emit
        self.last = None

    def close(self):
        for key, attention in self.attention.items():
            attention.finish(key, time.monotonic(), self.emit, 'SESSION_END')

    def acknowledge(self, key):
        self.attention[key].acknowledge()

    def update(self, statuses, now=None):
        now = time.monotonic() if now is None else now
        due = self.last is None or now - self.last >= INTERVAL
        eligible = {key for key, status in statuses.items()
                    if status.state == 'RUNNING' and status.instances == 1 and status.identity}
        shared = snapshot() if due and eligible else None
        for key, sample in self.samples.items():
            status = statuses[key]
            if key not in eligible:
                sample.update(key, status, now)
                self.attention[key].update(key, None, self.tools[key].telemetry, now, self.emit)
                continue
            if not due:
                current = read(status.pid)
                if not current or identity(current) != identity(status.identity):
                    sample.reset()
                    self.attention[key].update(key, None, self.tools[key].telemetry, now, self.emit)
                continue
            previous_key = sample.key
            token = (key, identity(status.identity))
            if previous_key != token:
                self.attention[key].finish(key, now, self.emit, 'UNAVAILABLE')
            sample.update(key, status, now, shared)
            self.attention[key].update(key, sample.value, self.tools[key].telemetry, now, self.emit)
        if due:
            self.last = now

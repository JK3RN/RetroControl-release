"""Small Cairo dots with continuous vertical resolution; no filled charts."""
import math


def geometry(values, metric, width, height, slots):
    values = list(values)[-slots:]
    real = [v for v in values if v is not None]
    if metric == 'cpu':
        # Nonlinear scale makes low CPU changes readable; 100% stays below top.
        top = max(100, max(real, default=0) * 1.25)
        low = 0
        transform = lambda v: math.log1p(max(0, v)) / math.log1p(top)
    else:
        # At least 64 MiB vertical span, with headroom; focus on memory changes.
        minimum, maximum = min(real, default=0), max(real, default=0)
        span = max(64 * 2**20, (maximum - minimum) * 1.5)
        low = max(0, minimum - span * .25)
        top = max(low + span, maximum * 1.1)
        transform = lambda v: (v - low) / max(1, top - low)
    result = []
    offset = slots - len(values)
    for index, value in enumerate(values):
        if value is None:
            continue  # no fabricated prehistory
        level = min(1, max(0, transform(value)))
        idle = value < .5 if metric == 'cpu' else (not real or max(real) - min(real) < 2**20)
        x = 3 + (index + offset) * max(0, width - 6) / max(1, slots - 1)
        y = height - 3 - level * max(0, height - 6)
        result.append((x, y, idle))
    return result


def draw(context, values, metric, width, height, colors, slots):
    for x, y, idle in geometry(values, metric, width, height, slots):
        color = colors['foreground'] if idle else colors['accent']
        rgb = tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
        context.set_source_rgba(*rgb, .25 if idle else .85)
        context.arc(x, y, .85, 0, math.tau)
        context.fill()

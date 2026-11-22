"""Sparse, bounded RetroControl diagnostics; never write into tool logs."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from .telemetry import memory_text

MAX_BYTES = 128 * 1024
BACKUPS = 2


class Diagnostics:
    def __init__(self, root):
        self.handler = None
        self.failed = False
        self.path = Path(root) / 'telemetry.log'
        # Lazy open: nominal monitoring does not create/write a file.

    def emit(self, key, event, value):
        if self.failed:
            return
        try:
            if self.handler is None:
                self.handler = RotatingFileHandler(self.path, maxBytes=MAX_BYTES,
                                                   backupCount=BACKUPS, encoding='utf-8', delay=True)
                self.handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', '%Y-%m-%dT%H:%M:%S%z'))
                # handleError normally hides IO errors or writes to stderr.
                # Disable persistence once on error instead, keeping the UI alive.
                self.handler.handleError = self.disable
            details = 'values=unavailable'
            if value is not None:
                cpu, mem, count = value
                details = f'cpu={cpu:.1f}%' if cpu is not None else 'cpu=unknown'
                details += f' mem={memory_text(mem)} proc={count}'
            record = logging.LogRecord('retrocontrol.telemetry', logging.INFO, '', 0,
                                       f'[{key[:64].upper()}] TELEMETRY {event} {details}', (), None)
            self.handler.emit(record)
        except (OSError, ValueError):
            self.disable()

    def disable(self, *_):
        self.failed = True
        self.close()

    def close(self):
        if self.handler:
            self.handler.close()
            self.handler = None

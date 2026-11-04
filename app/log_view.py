"""Bounded application log history for the settings page."""
import logging
from collections import deque


class LogBuffer(logging.Handler):
    def __init__(self):
        super().__init__()
        self.entries = deque(maxlen=1000)
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    def emit(self, record):
        self.entries.append(self.format(record)[-8000:])

    def snapshot(self):
        self.acquire()
        try:
            return list(self.entries)
        finally:
            self.release()


buffer = LogBuffer()


def install_log_buffer():
    for name in (None, "uvicorn", "uvicorn.access"):
        logger = logging.getLogger(name)
        if buffer not in logger.handlers:
            logger.addHandler(buffer)

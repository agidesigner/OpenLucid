"""In-memory log buffer for exporting recent log entries."""
from __future__ import annotations

import logging
from collections import deque

NOISY_LOGGERS = {"httpcore", "httpx", "uvicorn.access", "watchfiles"}


class BufferedLogHandler(logging.Handler):
    """Keeps the most recent log records in a memory ring buffer."""

    def __init__(self, maxlen: int = 200) -> None:
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.split(".")[0] in NOISY_LOGGERS:
            return
        self.buffer.append(self.format(record))

    def get_recent(self, n: int = 100) -> list[str]:
        return list(self.buffer)[-n:]


_handler: BufferedLogHandler | None = None


def get_log_handler() -> BufferedLogHandler:
    global _handler
    if _handler is None:
        _handler = BufferedLogHandler()
        _handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s")
        )
    return _handler

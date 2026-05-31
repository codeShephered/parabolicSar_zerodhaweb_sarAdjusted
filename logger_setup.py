"""logger_setup.py — rotating file log + SSE broadcast queue."""
import json, logging, queue
from datetime import datetime
from logging.handlers import RotatingFileHandler
import config

LOG_QUEUE: queue.Queue = queue.Queue(maxsize=2000)
_memory_log: list[dict] = []


class _QueueHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        entry = {
            "ts":      datetime.now().strftime("%H:%M:%S"),
            "level":   record.levelname,
            "module":  record.name.split(".")[-1],
            "message": self.format(record),
        }
        _memory_log.append(entry)
        if len(_memory_log) > config.MAX_MEMORY_LOGS:
            _memory_log.pop(0)
        try:
            LOG_QUEUE.put_nowait(json.dumps(entry))
        except queue.Full:
            pass


def setup_logger(name: str = "trading") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, config.LOG_LEVEL, logging.DEBUG))
    fmt = logging.Formatter(
        "%(asctime)s  [%(levelname)-7s]  [%(name)s]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = RotatingFileHandler(config.LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    qh = _QueueHandler()
    qh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.addHandler(qh)
    return logger


def get_module_logger(module: str) -> logging.Logger:
    return logging.getLogger(f"trading.{module}")


def get_log_entries() -> list[dict]:
    return list(_memory_log)

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import UTC, datetime
from pathlib import Path


class JsonFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        return json.dumps(entry, ensure_ascii=False)


_LOGGING_INITIALIZED = False


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> None:
    """Configure structured logging with console + optional file output.

    전역 guard로 중복 호출 완전 차단 — 이전엔 콘솔 핸들러 존재만 체크해서
    pytest가 app.main을 여러 번 import할 때 파일 핸들러가 누적되는 문제 있었음.
    """
    global _LOGGING_INITIALIZED
    if _LOGGING_INITIALIZED:
        return
    _LOGGING_INITIALIZED = True

    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(console)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "app.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

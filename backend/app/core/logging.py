import json
import logging
import os
from contextlib import suppress
from datetime import UTC, datetime

_LOGGER = logging.getLogger("kanglin.request")


class _RequestJsonHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # No default handleError/traceback fallback can expose a LogRecord.
        os.write(2, (record.getMessage() + "\n").encode("utf-8"))


def configure_logging() -> None:
    _LOGGER.propagate = False
    _LOGGER.setLevel(logging.INFO)
    if not _LOGGER.handlers:
        _LOGGER.addHandler(_RequestJsonHandler())


def emit_request_event(
    *, request_id: str, method: str, route_template: str, status_code: int,
    duration_ms: int, error_code: str | None, retryable: bool,
) -> None:
    configure_logging()
    event = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "level": "INFO", "event": "request", "request_id": request_id,
        "method": method, "route_template": route_template, "status_code": status_code,
        "duration_ms": duration_ms, "error_code": error_code, "retryable": retryable,
    }
    _LOGGER.info(json.dumps(event, ensure_ascii=True, separators=(",", ":")))


def observation_failed() -> None:
    # An unavailable diagnostic sink must not alter a committed response.
    with suppress(OSError):
        os.write(2, b'{"level":"ERROR","event":"OBSERVABILITY_EVENT_FAILED"}\n')


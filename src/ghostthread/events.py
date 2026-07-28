"""In-process event bus for the live UI.

The pipeline emits an event at every node boundary and every side effect. The
API exposes them over SSE (`GET /events`) so the dashboard can show sub-agents
spawning and actions landing in real time, instead of waiting for the run's
final report.

Deliberately in-process: one engine instance serves the demo, and a ring
buffer of the last 1000 events is plenty for a reconnecting browser tab.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from typing import Any

_LOCK = threading.Lock()
_BUFFER: deque[dict[str, Any]] = deque(maxlen=1000)
_SEQ = itertools.count(1)


def emit(event_type: str, **data: Any) -> None:
    """Publish one event. Safe to call from any thread."""
    with _LOCK:
        _BUFFER.append(
            {
                "seq": next(_SEQ),
                "type": event_type,
                "ts": time.time(),
                "data": data,
            }
        )


def events_since(seq: int) -> list[dict[str, Any]]:
    with _LOCK:
        return [e for e in _BUFFER if e["seq"] > seq]


def last_seq() -> int:
    with _LOCK:
        return _BUFFER[-1]["seq"] if _BUFFER else 0

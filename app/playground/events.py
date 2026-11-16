"""Thread-safe run event log, consumed over Server-Sent Events.

Agents run in worker threads and emit events here; the browser (or any other
client on any device) reads them in order. Events are kept so a client that
reconnects, or a second device, can replay a run from any sequence number.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Iterator

from .models import now_iso

MAX_EVENTS: int = 4000


class RunLog:
    def __init__(self, run_id: str) -> None:
        self.run_id: str = run_id
        self._events: list[dict[str, Any]] = []
        self._cond: threading.Condition = threading.Condition()
        self._closed: bool = False
        self.cancel_flag: threading.Event = threading.Event()

    def emit(self, type_: str, agent: str | None = None, message: str = "", **data: Any) -> dict[str, Any] | None:
        with self._cond:
            if len(self._events) >= MAX_EVENTS:
                return None
            self._events.append({
                "seq": len(self._events) + 1,
                "at": now_iso(),
                "type": type_,
                "agent": agent,
                "message": message,
                "data": data,
            })
            self._cond.notify_all()
            return self._events[-1]

    def load(self, events: list[dict[str, Any]]) -> None:
        """Restore a stored history (used when a run is opened from another device)."""
        with self._cond:
            self._events = list(events)[:MAX_EVENTS]
            self._closed = True

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def reopen(self) -> None:
        """Resume emitting on a finished run (follow-ups keep one continuous history)."""
        with self._cond:
            self._closed = False
            self.cancel_flag.clear()

    @property
    def closed(self) -> bool:
        return self._closed

    def snapshot(self, after: int = 0) -> list[dict[str, Any]]:
        with self._cond:
            return [e for e in self._events if e["seq"] > after]

    def all(self) -> list[dict[str, Any]]:
        return self.snapshot(0)

    def cancel(self) -> None:
        self.cancel_flag.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_flag.is_set()

    def stream(self, after: int = 0, heartbeat: float = 15.0) -> Iterator[str]:
        """Yield SSE frames. Ends once the run is closed and drained."""
        cursor = after
        while True:
            with self._cond:
                pending = [e for e in self._events if e["seq"] > cursor]
                if not pending and not self._closed:
                    self._cond.wait(timeout=heartbeat)
                    pending = [e for e in self._events if e["seq"] > cursor]
                closed = self._closed
            if pending:
                for event in pending:
                    cursor = event["seq"]
                    yield f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            elif closed:
                yield "event: end\ndata: {}\n\n"
                return
            else:
                yield ": keep-alive\n\n"

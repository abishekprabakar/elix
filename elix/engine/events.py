"""In-process publish and subscribe for the server's SSE stream.

Publishing never blocks the engine. Each subscriber gets its own bounded queue;
a slow subscriber drops its oldest events instead of slowing down payments.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any, Optional

KINDS = ("audit", "work_item", "journal", "policy", "period", "scenario", "eval")


class Subscription:
    def __init__(self, bus: "EventBus", loop: Optional[asyncio.AbstractEventLoop], maxsize: int = 1000):
        self.bus = bus
        self.loop = loop
        self.queue: "queue.Queue[tuple[str, Any]]" = queue.Queue(maxsize=maxsize)
        self.async_queue: Optional[asyncio.Queue] = asyncio.Queue(maxsize=maxsize) if loop else None

    def deliver(self, event: tuple[str, Any]) -> None:
        if self.loop is not None and self.async_queue is not None:
            try:
                self.loop.call_soon_threadsafe(self._put_async, event)
            except RuntimeError:  # loop closed
                self.bus.unsubscribe(self)
            return
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(event)

    def _put_async(self, event: tuple[str, Any]) -> None:
        assert self.async_queue is not None
        if self.async_queue.full():
            self.async_queue.get_nowait()
        self.async_queue.put_nowait(event)

    def close(self) -> None:
        self.bus.unsubscribe(self)


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscription] = []
        self._lock = threading.Lock()

    def subscribe(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> Subscription:
        sub = Subscription(self, loop)
        with self._lock:
            self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def publish(self, kind: str, payload: Any) -> None:
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            sub.deliver((kind, payload))

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

"""Server state: the current Elix instance, the shared event bus, and the demo reset."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Optional

from elix.engine import Elix
from elix.engine.clock import Clock
from elix.engine.events import EventBus


class ServerState:
    def __init__(self, db_path: Path, registry: Any, *, live_clock: bool = True, seed_if_missing: bool = True):
        self.db_path = Path(db_path)
        self.registry = registry
        self.live_clock = live_clock
        self.bus = EventBus()
        self._swap = threading.RLock()
        self.eval_thread: Optional[threading.Thread] = None
        self.eval_status: dict[str, Any] = {"status": "idle", "progress": 0.0}
        if seed_if_missing and not self.db_path.exists():
            self._seed_into(self.db_path)
        self._elix = self._open()

    # ------------------------------------------------------------ the engine

    @property
    def elix(self) -> Elix:
        with self._swap:
            return self._elix

    def _open(self) -> Elix:
        from elix.engine.store import Store
        from elix.world.config import TODAY

        probe = Store(self.db_path)
        now = probe.get_meta("clock") or TODAY
        probe.close()
        clock = Clock(now, live=self.live_clock)
        if hasattr(self.registry, "set_clock"):
            self.registry.set_clock(clock)
        try:
            from elix.agents import sessions

            sessions.set_clock(clock)
            sessions.set_listener(lambda session_id, role: self.bus.publish(
                "agent", {"session_id": session_id, "role": role}))
        except Exception:  # agents are optional for the engine and API
            pass
        return Elix(self.db_path, self.registry, clock=clock, bus=self.bus)

    # ------------------------------------------------------------ demo reset

    def _seed_into(self, path: Path) -> None:
        from elix.world.seed import seed_world

        if getattr(self.registry, "kind", "real") == "real" and getattr(self.registry, "mode", "offline") == "offline":
            from elix.rails import reset_offline_state

            reset_offline_state(getattr(self.registry, "vendor_base_url", None))
        result = seed_world(path, self.registry)
        result.elix.close()

    def reset(self) -> None:
        """Reseed the world into a side file, then swap it in."""
        staging = self.db_path.with_name(self.db_path.name + ".next")
        self._seed_into(staging)
        with self._swap:
            self._elix.close()
            for suffix in ("-wal", "-shm"):
                stale = Path(f"{self.db_path}{suffix}")
                if stale.exists():
                    stale.unlink()
            os.replace(staging, self.db_path)
            self._elix = self._open()
        self.bus.publish("scenario", {"id": "reset", "status": "done", "message": "Demo data was reset."})

    def close(self) -> None:
        with self._swap:
            self._elix.close()

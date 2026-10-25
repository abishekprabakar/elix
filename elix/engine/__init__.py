"""The Elix engine. Other components use `Elix` (the facade) and `open_elix`, nothing else."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from elix.engine.facade import Elix

__all__ = ["Elix", "open_elix"]


def open_elix(db_path: Optional[str | Path] = None, registry: Optional[Any] = None, **kwargs: Any) -> Elix:
    """Open Elix on the default database (ELIX_DATA_DIR/elix.db) with the default rail registry."""
    from elix.engine.settings import default_db_path

    if registry is None:
        from elix.rails import make_registry

        registry = make_registry()
    return Elix(db_path or default_db_path(), registry, **kwargs)

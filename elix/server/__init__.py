"""HTTP API, SSE stream and console hosting. `create_app` builds the FastAPI app."""

from elix.server.app import create_app

__all__ = ["create_app"]

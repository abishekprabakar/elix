"""python -m elix.vendors [--port 8402] [--host 127.0.0.1] [--mode offline|live]

Runs the demo x402 vendor servers in the foreground.
"""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from elix.vendors.app import create_app

    p = argparse.ArgumentParser(prog="python -m elix.vendors")
    p.add_argument("--port", type=int, default=8402)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--mode", default=None, choices=["offline", "live"])
    args = p.parse_args(argv)
    app = create_app(mode=args.mode)
    print(f"Elix demo x402 vendors on http://{args.host}:{args.port} ({app.state.mode} mode). Data is synthetic.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Take sharp screenshots for the site from a private, freshly seeded demo, plus the matching hero values.

The canonical console screenshots in console/screenshots are 1440x900 at a device scale of 1. This script
runs the same console screenshot script at a device scale of 2 against its own demo server, so the images
stay crisp on retina screens, and it records the hero transaction from the same database so the tx refs on
the page match the ones in the screenshots.

Run from the repo root (needs a built console in console/dist):
  python site/capture.py

It seeds a world in a temporary data directory on free ports, runs the demo scenarios through the API, has
Dev Okafor approve the Stormvault spend (the payee hold still waits for Priya), takes the four screens the
site uses in light and dark into site/assets/source/, writes site/assets/source/capture.json, and stops the
server. Nothing outside site/assets/source is written.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
OUT = SITE / "assets" / "source"
SCREENS = "today,reconcile,item_payee_hold,audit_why"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base: str, method: str, path: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read() or b"{}")


def main() -> int:
    if not (ROOT / "console" / "dist" / "index.html").exists():
        print("console/dist is missing. Build the console first (cd console && npm run build).", file=sys.stderr)
        return 1
    data_dir = Path(tempfile.mkdtemp(prefix="elix-site-capture-"))
    port, vport = free_port(), free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "ELIX_DATA_DIR": str(data_dir), "ELIX_RAIL_MODE": "offline"}
    env.pop("ANTHROPIC_API_KEY", None)
    log = open(data_dir / "demo.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "elix", "demo", "--port", str(port), "--vendor-port", str(vport)],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(240):
            try:
                urllib.request.urlopen(base + "/healthz", timeout=2)
                break
            except Exception:
                if proc.poll() is not None:
                    print((data_dir / "demo.log").read_text(), file=sys.stderr)
                    return 1
                time.sleep(0.5)
        else:
            print("demo server did not start", file=sys.stderr)
            return 1
        print(f"demo on {base}, data in {data_dir}")

        for sid in ("learn-policies", "closed-period-fix", "retry-double-pay", "self-approval", "cross-rail-double-bill"):
            r = call(base, "POST", f"/api/scenarios/{sid}/run", "tok_priya")
            print(f"scenario {sid}: {r.get('message', '')[:100]}")
        r = call(base, "POST", "/api/scenarios/payee-change/run", "tok_priya")
        print(f"scenario payee-change: {r.get('message', '')[:100]}")
        storm = next((i for i in r.get("result_ids", []) if i.startswith("WI-")), None)
        if storm:
            call(base, "POST", f"/api/work-items/{storm}/approve", "tok_dev", {"note": "Texas backfill is in the Q4 data plan."})
            print(f"Dev approved the spend on {storm}; the payee hold waits for Priya")

        tmp_out = data_dir / "shots"
        cmd = ["node", str(ROOT / "console" / "scripts" / "shots.mjs"), "--base", base, "--scale", "2",
               "--out", str(tmp_out), "--only", SCREENS]
        res = subprocess.run(cmd, cwd=ROOT / "console", capture_output=True, text=True, timeout=600)
        print(res.stdout.strip()[-800:])
        if res.returncode != 0:
            print(res.stderr[-2000:], file=sys.stderr)
            return 1

        sys.path.insert(0, str(SITE))
        import build  # noqa: E402

        values = build.world_from_engine(data_dir)
        if not values:
            print("could not read the hero transaction from the capture world", file=sys.stderr)
            return 1
        OUT.mkdir(parents=True, exist_ok=True)
        for png in tmp_out.glob("*.png"):
            shutil.copy2(png, OUT / png.name)
        record = {
            "console_dist_sha256": build.console_build_hash(),
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "screens": SCREENS.split(","),
            "world": values,
        }
        (OUT / "capture.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"wrote {len(list(tmp_out.glob('*.png')))} screenshots and capture.json to {OUT.relative_to(ROOT)}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build the Elix marketing page into one self-contained file.

Reads site/src/index.html, resolves its placeholders and writes site/dist/index.html.

Placeholders
  {{img:NAME}}          data URI of an optimized WebP built from a console screenshot (see IMAGES)
  {{imgsize:NAME}}      width="W" height="H" attributes for that image
  {{eval:KEY}}          a number from eval/report.json, optionally {{eval:KEY|pct}} or |verified
  {{eval_table:categories}}  table rows for the per category results in eval/report.json
  {{world:KEY}}         a value from the seeded Kestrel world (data/elix.db through the engine)
  {{guilloche:NAME}}    SVG path data for the security pattern
  {{repo_url}}          the GitHub repository URL

The build fails when a placeholder has no real value: a missing console screenshot, a missing
eval report or key, or anything left unresolved. Pass --draft to build anyway with clearly
temporary placeholder images and values while the other components are still being built.

Usage (from the repo root)
  python site/build.py            final build, fails loudly on anything missing
  python site/build.py --draft    allowed to use placeholders
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
SRC = SITE / "src" / "index.html"
DIST = SITE / "dist" / "index.html"
ASSETS = SITE / "assets"
PLACEHOLDER_DIR = ASSETS / "placeholders"
SHOTS = ROOT / "console" / "screenshots"
REPORT = ROOT / "eval" / "report.json"
MAX_BYTES = 8 * 1024 * 1024
DEFAULT_REPO_URL = "https://github.com/abishekprabakar/elix"

# Named page images. src is the console screenshot name without extension (stable names from
# specs/09-acceptance.md). crop is (left, top, right, bottom) as fractions of the source so it does not
# depend on the device pixel ratio the console screenshots were taken at. width is the output width.
IMAGES: dict[str, dict] = {}
for _theme in ("light", "dark"):
    # The whole Today screen under the hero.
    IMAGES[f"today_{_theme}"] = {"src": f"today_{_theme}", "crop": None, "width": 2400, "quality": 78}
    # The LUM-2026-10A card on the Reconcile screen: pay now, dispute, the approve button and the three way match.
    # Cropped inside the card's own border so the page's frame is the only one.
    IMAGES[f"reconcile_{_theme}"] = {"src": f"reconcile_{_theme}", "crop": (0.177, 0.428, 0.964, 0.962), "width": 2400, "quality": 78}
    # The payee hold item: title, hold pill, approvals strip and the failing checks, beside the rule list.
    IMAGES[f"payee_hold_{_theme}"] = {"src": f"item_payee_hold_{_theme}", "crop": (0.162, 0.33, 0.645, 0.93), "width": 1300, "quality": 80}
    # Narrower crops for phones, where a whole screen would be too small to read.
    IMAGES[f"today_phone_{_theme}"] = {"src": f"today_{_theme}", "crop": (0.163, 0.07, 0.71, 0.42), "width": 1100, "quality": 80}
    IMAGES[f"reconcile_phone_{_theme}"] = {"src": f"reconcile_{_theme}", "crop": (0.177, 0.428, 0.585, 0.648), "width": 1100, "quality": 80}
    IMAGES[f"audit_why_phone_{_theme}"] = {"src": f"audit_why_{_theme}", "crop": (0.177, 0.37, 0.53, 0.63), "width": 1000, "quality": 80}
    # The Why report card for LUM-2026-10A: the question, the answer and the first steps of the trail.
    IMAGES[f"audit_why_{_theme}"] = {"src": f"audit_why_{_theme}", "crop": (0.175, 0.2, 0.966, 0.70), "width": 2400, "quality": 78}

# Imagery for the v2 page, read from files under site/assets. crop is fractions of the source.
# radar_* are renders of real NOAA NEXRAD Level 2 data (KFTG, Denver, April 30, 2015, from the MetPy test data),
# sat_* are crops of a real USGS Landsat scene (the rasterio test image). Both are public domain.
# Photos dropped into site/assets/photos/ as photo_1.jpg ... photo_8.jpg replace the matching hero tile.
FILE_IMAGES: dict[str, dict] = {
    "radar_a": {"file": "real/radar_close.png", "crop": (0.12, 0.08, 0.72, 0.68), "width": 520, "quality": 82},
    "radar_b": {"file": "real/radar_wide.png", "crop": (0.2, 0.18, 0.62, 0.6), "width": 360, "quality": 82},
    "sat_a": {"file": "real/landsat_full.png", "crop": (0.354, 0.056, 0.632, 0.362), "width": 440, "quality": 84},
    "sat_b": {"file": "real/landsat_full.png", "crop": (0.19, 0.39, 0.44, 0.67), "width": 400, "quality": 84},
    "sat_c": {"file": "real/landsat_full.png", "crop": (0.657, 0.209, 0.91, 0.487), "width": 400, "quality": 84},
    "poster": {"file": "video/poster.jpg", "crop": None, "width": 1440, "quality": 80},
    # Hero purchase photos supplied by the founder (site/assets/photos). Cropped to the card's 1 by 1.15 shape.
    "buy_gpu": {"file": "photos/gpu.jpg", "crop": (0.174, 0.0, 0.826, 1.0), "width": 456, "quality": 84},
    "buy_sat": {"file": "photos/satellite.jpg", "crop": (0.083, 0.0, 0.917, 1.0), "width": 820, "quality": 80},
    "buy_radar": {"file": "photos/radar_tower.jpg", "crop": (0.065, 0.0, 0.935, 1.0), "width": 820, "quality": 82},
}
for _i in range(1, 9):
    FILE_IMAGES[f"photo_{_i}"] = {"file": f"photos/photo_{_i}.jpg", "crop": None, "width": 640, "quality": 80, "optional": True}
IMAGES.update(FILE_IMAGES)

# Optional sharper copies of the same screens, taken by site/capture.py with the console's own screenshot script
# at a device scale of 2 against a private demo world. capture.json records the console build they show
# (sha256 of console/dist/index.html, whose asset names change with any UI change) and the hero values from
# that same world. They are used only while the console build still matches, so a stale capture never wins.
HIRES = ASSETS / "source"
CONSOLE_DIST = ROOT / "console" / "dist" / "index.html"


def console_build_hash() -> str | None:
    return hashlib.sha256(CONSOLE_DIST.read_bytes()).hexdigest() if CONSOLE_DIST.exists() else None


def capture_meta() -> dict | None:
    """The capture record when the sharp screenshots still match the console build, else None."""
    meta_path = HIRES / "capture.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except ValueError:
        return None
    current = console_build_hash()
    if current is not None and meta.get("console_dist_sha256") != current:
        return None
    return meta

CATEGORY_LABELS = {
    "DUPLICATE_RETRY": "Duplicate retry",
    "PAYEE_CHANGED": "Payee changed",
    "PRICE_ABOVE_CONTRACT": "Price above contract",
    "AMOUNT_MISMATCH": "Amount differs from request",
    "OVER_TXN_CAP": "Over the transaction cap",
    "BUDGET_EXCEEDED": "Over the monthly budget",
    "SPLIT_PURCHASE": "Split purchase",
    "UNKNOWN_VENDOR": "Unknown vendor",
    "MISSING_PURPOSE": "Missing purpose",
    "MEMO_INSTRUCTIONS": "Instructions in a memo",
    "SELF_APPROVAL": "Agent approving itself",
    "INVOICE_DOUBLE_BILL": "Invoice double bill across rails",
    "INVOICE_OVERBILL": "Invoice above the meter",
    "INVOICE_RATE": "Invoice rate off contract",
}
CATEGORY_ORDER = list(CATEGORY_LABELS)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)(?::([^}|]+?))?(?:\|([a-z_]+))?\s*\}\}")


class BuildError(Exception):
    pass


class Build:
    def __init__(self, draft: bool, repo_url: str | None):
        self.draft = draft
        self.repo_url_arg = repo_url
        self.problems: list[str] = []      # each one fails a final build
        self.notes: list[str] = []         # informational
        self.placeholder_images: set[str] = set()
        self.real_images: set[str] = set()
        self.image_meta: dict[str, dict] = {}
        self.report: dict | None = None
        self.report_error: str | None = None
        self.world: dict | None = None
        self.world_source = "none"

    # ------------------------------------------------------------------ problems
    def problem(self, msg: str) -> None:
        self.problems.append(msg)

    # ------------------------------------------------------------------ images
    def _source_for(self, src: str) -> Path | None:
        canonical = None
        for ext in (".png", ".webp", ".jpg", ".jpeg"):
            p = SHOTS / f"{src}{ext}"
            if p.exists() and p.stat().st_size > 0:
                canonical = p
                break
        if canonical is None:
            return None
        hi = HIRES / f"{src}.png"
        if hi.exists() and hi.stat().st_size > 0 and capture_meta() is not None:
            return hi
        return canonical

    def _placeholder(self, src: str) -> Path:
        from PIL import Image, ImageDraw, ImageFont

        PLACEHOLDER_DIR.mkdir(parents=True, exist_ok=True)
        out = PLACEHOLDER_DIR / f"{src}.png"
        if out.exists():
            return out
        dark = src.endswith("_dark")
        bg, panel, rule, ink = (
            ((15, 20, 22), (22, 29, 32), (42, 53, 56), (118, 131, 138))
            if dark
            else ((241, 244, 241), (255, 255, 255), (207, 216, 211), (124, 135, 148))
        )
        w, h = 1440, 900
        im = Image.new("RGB", (w, h), bg)
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, w, 56], fill=panel)
        d.line([0, 56, w, 56], fill=rule)
        d.rectangle([0, 57, 200, h - 33], fill=panel)
        d.line([200, 57, 200, h - 33], fill=rule)
        d.line([0, h - 33, w, h - 33], fill=rule)
        for i in range(9):
            d.rounded_rectangle([20, 84 + i * 36, 150, 100 + i * 36], radius=3, fill=rule)
        for r in range(3):
            d.rounded_rectangle([232, 96 + r * 250, w - 32, 320 + r * 250], radius=8, outline=rule, fill=panel)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
            small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        except OSError:
            font = small = ImageFont.load_default()
        label = f"Placeholder for console screenshot {src}"
        d.rectangle([w // 2 - 420, h // 2 - 50, w // 2 + 420, h // 2 + 50], fill=bg)
        d.text((w // 2, h // 2 - 10), label, fill=ink, font=font, anchor="mm")
        d.text((w // 2, h // 2 + 28), "site/build.py swaps in console/screenshots when it exists", fill=ink, font=small, anchor="mm")
        im.save(out)
        return out

    def image(self, name: str) -> dict:
        if name in self.image_meta:
            return self.image_meta[name]
        if name not in IMAGES:
            raise BuildError(f"unknown image name {name!r}; add it to IMAGES in site/build.py")
        spec = IMAGES[name]
        if "file" in spec:
            fpath = ASSETS / spec["file"]
            if not fpath.exists():
                raise BuildError(f"image {name}: site/assets/{spec['file']} does not exist")
            source, placeholder = fpath, False
            self.real_images.add(name)
            meta = self._optimize(name, source, spec, placeholder)
            self.image_meta[name] = meta
            return meta
        source = self._source_for(spec["src"])
        placeholder = source is None
        if placeholder:
            self.placeholder_images.add(name)
            self.problem(f"image {name}: console/screenshots/{spec['src']}.png does not exist yet, using a placeholder")
            source = self._placeholder(spec["src"])
        else:
            self.real_images.add(name)
        meta = self._optimize(name, source, spec, placeholder)
        self.image_meta[name] = meta
        return meta

    def _optimize(self, name: str, source: Path, spec: dict, placeholder: bool) -> dict:
        from PIL import Image

        ASSETS.mkdir(parents=True, exist_ok=True)
        out = ASSETS / f"{name}.webp"
        sidecar = ASSETS / f"{name}.json"
        raw = source.read_bytes()
        key = hashlib.sha256(raw + json.dumps(spec, sort_keys=True).encode()).hexdigest()
        if out.exists() and sidecar.exists():
            try:
                meta = json.loads(sidecar.read_text())
                if meta.get("key") == key:
                    meta["data"] = out.read_bytes()
                    return meta
            except (ValueError, OSError):
                pass
        im = Image.open(io.BytesIO(raw))
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        if im.mode == "RGBA":
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[3])
            im = bg
        if spec.get("crop"):
            l, t, r, b = spec["crop"]
            W, H = im.size
            im = im.crop((round(l * W), round(t * H), round(r * W), round(b * H)))
        target = spec["width"]
        if im.width > target:
            im = im.resize((target, round(im.height * target / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=spec.get("quality", 80), method=6)
        data = buf.getvalue()
        out.write_bytes(data)
        meta = {
            "key": key,
            "name": name,
            "source": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
            "placeholder": placeholder,
            "width": im.width,
            "height": im.height,
            "bytes": len(data),
        }
        sidecar.write_text(json.dumps(meta, indent=2) + "\n")
        meta["data"] = data
        return meta

    def img_uri(self, name: str) -> str:
        meta = self.image(name)
        return "data:image/webp;base64," + base64.b64encode(meta["data"]).decode("ascii")

    def img_size(self, name: str) -> str:
        meta = self.image(name)
        return f'width="{meta["width"]}" height="{meta["height"]}"'

    # ------------------------------------------------------------------ eval report
    def load_report(self, path: Path = REPORT) -> None:
        global REPORT
        REPORT = path
        if not REPORT.exists():
            self.report_error = "eval/report.json does not exist yet (run python -m elix eval)"
            return
        try:
            self.report = json.loads(REPORT.read_text())
        except ValueError as e:
            self.report_error = f"eval/report.json is not valid JSON: {e}"

    @staticmethod
    def _lookup(obj, key: str):
        cur = obj
        found = True
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                found = False
                break
        if found:
            return True, cur
        leaf = key.split(".")[-1]

        def walk(o):
            if isinstance(o, dict):
                if leaf in o:
                    return True, o[leaf]
                for v in o.values():
                    ok, val = walk(v)
                    if ok:
                        return ok, val
            return False, None

        return walk(obj)

    def categories(self) -> list[dict]:
        rep = self.report or {}
        raw = None
        for k in ("categories", "per_category", "by_category", "category_table", "category_results"):
            ok, val = self._lookup(rep, k)
            if ok and val:
                raw = val
                break
        if raw is None:
            raise BuildError("eval report has no per category table")
        rows: list[dict] = []
        items = raw.items() if isinstance(raw, dict) else [(None, r) for r in raw]
        for k, v in items:
            if not isinstance(v, dict):
                continue
            cat = k or v.get("category") or v.get("name") or v.get("planted")
            def first(*names):
                for n in names:
                    if n in v and v[n] is not None:
                        return v[n]
                return None
            rows.append({
                "category": str(cat),
                "planted": first("planted", "total", "count", "anomalies", "n"),
                "caught": first("caught", "anomalies_caught"),
                "not_auto_paid": first("not_auto_paid", "anomalies_not_auto_paid"),
                "auto_paid": first("auto_paid", "anomalies_auto_paid"),
            })
        order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        rows.sort(key=lambda r: order.get(r["category"], 99))
        return rows

    @staticmethod
    def fmt(value, how: str | None) -> str:
        if how == "pct":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BuildError(f"cannot format {value!r} as a percent")
            x = value * 100 if value <= 1.0 else value
            if abs(x - round(x)) < 1e-9:
                return f"{round(x):.0f}%"
            return f"{x:.1f}%"
        if how == "verified":
            return "Verified" if bool(value) else "Broken"
        if how == "date":
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%B %-d, %Y")
            except ValueError:
                return str(value)
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, int):
            return f"{value:,}"
        if isinstance(value, float):
            return f"{int(value):,}" if value.is_integer() else f"{value:,.2f}"
        if value is None:
            raise BuildError("value is null")
        return str(value)

    def eval_value(self, key: str, how: str | None) -> str | None:
        if self.report is None:
            self.problem(f"eval:{key}: {self.report_error}")
            return None
        try:
            if key == "category_count":
                ok, total = self._lookup(self.report, "categories_total")
                return str(total if ok and isinstance(total, int) else len(self.categories()))
            if key == "run_note":
                return self.run_note()
            ok, val = self._lookup(self.report, key)
            if not ok:
                self.problem(f"eval:{key}: key not found in eval/report.json")
                return None
            return html.escape(self.fmt(val, how))
        except BuildError as e:
            self.problem(f"eval:{key}: {e}")
            return None

    def run_note(self) -> str:
        rep = self.report or {}
        _, fast = self._lookup(rep, "fast")
        _, seed = self._lookup(rep, "seed")
        ok_gen, gen = self._lookup(rep, "generated_at")
        ok_rt, rt = self._lookup(rep, "runtime_seconds")
        if fast:
            self.problem("eval: report.json comes from a --fast run; publish numbers from a default run")
            rail = "the in-memory fake rails"
        else:
            rail = "the offline x402 rail, which signs every payload"
        s = f"This run used seed {seed} and {rail}." if seed is not None else f"This run used {rail}."
        if ok_gen and gen:
            s += f" It finished on {self.fmt(gen, 'date')}"
            s += f" in {rt:.0f} seconds." if ok_rt and isinstance(rt, (int, float)) else "."
        return html.escape(s) + " Rerun it with <code>python -m elix eval</code>."

    def eval_table(self, name: str) -> str | None:
        if name != "categories":
            self.problem(f"eval_table:{name}: unknown table")
            return None
        if self.report is None:
            self.problem(f"eval_table:{name}: {self.report_error}")
            return None
        try:
            rows = self.categories()
        except BuildError as e:
            self.problem(f"eval_table:{name}: {e}")
            return None
        out = []
        for r in rows:
            label = CATEGORY_LABELS.get(r["category"], r["category"].replace("_", " ").capitalize())
            planted = r["planted"]
            caught = r["caught"]
            auto = r["auto_paid"]
            if planted is None or auto is None:
                self.problem(f"eval_table: category {r['category']} lacks planted or auto paid counts")
                continue
            caught_cls = ' class="miss"' if caught is not None and caught < planted else ""
            auto_cls = ' class="zero"' if auto == 0 else ' class="miss"'
            out.append(
                f"              <tr><td>{html.escape(label)}</td><td>{planted:,}</td>"
                f"<td{caught_cls}>{'' if caught is None else f'{caught:,}'}</td><td{auto_cls}>{auto:,}</td></tr>"
            )
        return "\n".join(out)

    # ------------------------------------------------------------------ world values
    def load_world(self) -> None:
        values = dict(WORLD_EXAMPLE)
        source = "example"
        try:
            sys.path.insert(0, str(ROOT))
            from elix.rails.keys import vendor_address  # noqa: WPS433

            addr = vendor_address("ven_lumen", 0)
            values["lumen_pay_to_short"] = html.escape(f"{addr[:6]}…{addr[-4:]}")
            source = "keys"
        except Exception as e:  # pragma: no cover, depends on the rails builder
            self.notes.append(f"world: could not compute the Lumen payout address ({e})")
        meta = capture_meta()
        hires_used = meta is not None and all(
            (src := self._source_for(spec["src"])) is not None and src.parent == HIRES for spec in IMAGES.values()
        )
        engine_values = None
        if (HIRES / "capture.json").exists() and meta is None:
            self.notes.append("images: site/assets/source predates the current console build, so the 1x console "
                              "screenshots are used. Run python site/capture.py for sharp ones.")
        if hires_used and meta.get("world"):
            # The sharp screenshots came from capture.py's own demo world, so take the hero values from the
            # same world. Then every tx ref and id on the page matches the screenshots.
            engine_values = dict(meta["world"])
            engine_values["_source"] = "capture"
            self.notes.append("world: hero values from site/assets/source/capture.json, the world the screenshots show")
        else:
            try:
                engine_values = world_from_engine()
            except Exception as e:  # the engine is built concurrently; fall back to examples
                self.notes.append(f"world: engine snapshot unavailable ({type(e).__name__}: {e})")
        if engine_values:
            from_capture = engine_values.pop("_source", None) == "capture"
            note = engine_values.pop("provenance_note", None)
            if note:
                self.problem(f"world: {note}; seed with python -m elix seed (offline rails) and rebuild")
            values.update(engine_values)
            source = "capture" if from_capture else "engine"
        else:
            self.notes.append("world: hero transaction values are the spec examples, not from the seeded world")
        values["provenance_comment"] = {
            "engine": "Hero values come from the seeded Kestrel world in data/elix.db, read through the engine at build time.",
            "capture": "Hero values come from the same seeded Kestrel world as the screenshots (site/capture.py), read through the engine.",
            "keys": "Example hero values from specs/07. The Lumen pay to address is the real deterministic vendor address. Run python -m elix seed and rebuild for real values.",
            "example": "Example hero values from specs/07. Run python -m elix seed and rebuild for real values.",
        }[source]
        self.world = values
        self.world_source = source

    def world_value(self, key: str) -> str | None:
        if self.world is None:
            self.load_world()
        if key not in self.world:
            self.problem(f"world:{key}: unknown world value")
            return None
        return self.world[key]

    # ------------------------------------------------------------------ repo url
    def repo_url(self) -> str:
        if getattr(self, "_repo_url", None):
            return self._repo_url
        self._repo_url = self._find_repo_url()
        return self._repo_url

    def _find_repo_url(self) -> str:
        if self.repo_url_arg:
            return self.repo_url_arg
        env = os.environ.get("ELIX_REPO_URL")
        if env:
            return env
        try:
            out = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5)
            url = out.stdout.strip()
            if out.returncode == 0 and url:
                m = re.match(r"git@github\.com:(.+?)(?:\.git)?$", url)
                if m:
                    return f"https://github.com/{m.group(1)}"
                return re.sub(r"\.git$", "", url)
        except (OSError, subprocess.SubprocessError):
            pass
        self.notes.append(f"repo_url: no ELIX_REPO_URL or git remote, using {DEFAULT_REPO_URL}")
        return DEFAULT_REPO_URL

    # ------------------------------------------------------------------ resolve
    def resolve(self, text: str) -> str:
        draft_marks = 0

        def sub(m: re.Match) -> str:
            nonlocal draft_marks
            kind, arg, how = m.group(1), (m.group(2) or "").strip(), m.group(3)
            val: str | None
            if kind == "img":
                val = self.img_uri(arg)
            elif kind == "imgsize":
                val = self.img_size(arg)
            elif kind == "eval":
                val = self.eval_value(arg, how)
            elif kind == "eval_table":
                val = self.eval_table(arg)
            elif kind == "world":
                val = self.world_value(arg)
            elif kind == "guilloche":
                val = guilloche(arg)
            elif kind == "video":
                vpath = ASSETS / "video" / f"{arg}.mp4"
                val = "data:video/mp4;base64," + base64.b64encode(vpath.read_bytes()).decode("ascii") if vpath.exists() else None
                if val is None:
                    self.problem(f"video {arg}: site/assets/video/{arg}.mp4 does not exist")
            elif kind == "tile":
                # {{tile:N|fallback}} uses photo_N when the founder added it, otherwise the named real-data image
                photo = f"photo_{arg}"
                use = photo if (ASSETS / IMAGES[photo]["file"]).exists() else how
                val = self.img_uri(use)
            elif kind == "tilesize":
                photo = f"photo_{arg}"
                use = photo if (ASSETS / IMAGES[photo]["file"]).exists() else how
                val = self.img_size(use)
            elif kind == "repo_url":
                val = html.escape(self.repo_url(), quote=True)
            else:
                self.problem(f"unknown placeholder kind {kind!r} in {m.group(0)}")
                val = None
            if val is None:
                draft_marks += 1
                if kind == "eval_table":
                    return '              <tr><td colspan="4">Draft. Run python -m elix eval, then rebuild.</td></tr>'
                return f"[{arg or kind}]"
            return val

        out = PLACEHOLDER_RE.sub(sub, text)
        leftover = re.findall(r"\{\{[^{}]*\}\}", out)
        for lo in leftover:
            self.problem(f"unresolved placeholder {lo}")
        return out


# ---------------------------------------------------------------------- world snapshot

WORLD_EXAMPLE = {
    # Example values. These follow specs/07 so draft builds read correctly; a build with the seeded
    # world replaces every one of them with values read from data/elix.db.
    "hero_sub": "October 9, 2026, replayed from the Kestrel Labs demo",
    "hero_task_ref": "hail-v7",
    "hero_purpose": "Retrain hail-v7 on the new Nebraska scenes",
    "lumen_pay_to_short": "0x4b1d…77c0",
    "hero_checks_sentence": "Nine checks passed. Covered by pol-002, learned from 61 approvals by Dev Okafor.",
    "hero_checks_meta": "Policy <code>pol-002</code>",
    "hero_tx_short": "sim-a91c…",
    "hero_je_label": "Journal entry",
}


def world_from_engine(data_dir: Path | None = None) -> dict | None:
    """Read the hero transaction from the seeded world, on a copy of the database so nothing is written.

    Returns None when there is no seeded database yet."""
    data_dir = data_dir or Path(os.environ.get("ELIX_DATA_DIR") or (ROOT / "data"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    db = data_dir / "elix.db"
    if not db.exists():
        return None
    sys.path.insert(0, str(ROOT))
    from elix.engine import open_elix  # noqa: WPS433
    from elix.rails import make_fake_registry  # noqa: WPS433

    tmp = Path(tempfile.mkdtemp(prefix="elix-site-"))
    try:
        # The sqlite backup API includes pages still in the WAL file, which a plain file copy would miss.
        import sqlite3

        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp / "elix.db")
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        ex = open_elix(db_path=str(tmp / "elix.db"), registry=make_fake_registry())
        return _hero_values(ex)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _g(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _hero_values(ex) -> dict | None:
    """The Oct 9 Forge spot purchase that the cross rail moment depends on (specs/03)."""
    items = ex.work_items(kind="purchase", agent="agent:forge", vendor="ven_lumen", period="2026-10")
    hero = None
    for it in items:
        if (
            _g(it, "amount_cents") == 126000
            and str(_g(it, "created_at", "")).startswith("2026-10-09")
            and _g(it, "receipt") is not None
        ):
            hero = it
            break
    if hero is None:
        return None
    detail = ex.work_item(_g(hero, "id"))
    item = _g(detail, "item", hero)
    decision = _g(item, "decision")
    checks = list(_g(decision, "checks", []) or [])
    receipt = _g(item, "receipt")
    jes = list(_g(detail, "journal_entries", []) or [])
    approver = str(_g(item, "approved_by") or "")
    names = {}
    try:
        for a in _g(ex.world(), "actors", []) or []:
            names[_g(a, "id")] = _g(a, "name")
    except Exception:
        pass
    approver_name = names.get(approver) or approver.split(":")[-1].capitalize()
    words = {n: w for n, w in enumerate(
        "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen".split())}
    risk = [c for c in checks if _g(c, "rule_id") != "R12"]
    passed = [c for c in risk if _g(c, "passed")]
    if len(passed) == len(risk):
        lead = f"{words.get(len(passed), str(len(passed)))} checks passed."
    else:
        lead = f"{len(passed)} of {len(risk)} checks passed."
    amount = f"${_g(item, 'amount_cents') / 100:,.2f}"
    if approver.startswith("system:"):
        policy_id = (_g(item, "data") or {}).get("policy_id")
        tail = f" Auto approved under {policy_id}." if policy_id else " Auto approved by policy."
        meta = f"Policy <code>{html.escape(policy_id)}</code>" if policy_id else "Approved by policy code"
    else:
        tail = f" No policy lets Forge spend {amount} without review, so {approver_name} approved it."
        rules = sorted(str(_g(c, "rule_id")) for c in passed)
        meta = (f"<code>{html.escape(rules[0])}</code> to <code>{html.escape(rules[-1])}</code> passed"
                if rules else "Approved by a person")
    created = str(_g(item, "created_at"))
    date = datetime.fromisoformat(created.replace("Z", "+00:00"))
    tx = str(_g(receipt, "tx_ref", ""))
    pay_to = str(_g(receipt, "payee", "") or "")
    out = {
        "hero_sub": f"<code>{html.escape(str(_g(item, 'id')))}</code> on {date.strftime('%B %-d, %Y')}, replayed from the demo ledger",
        "hero_task_ref": html.escape(str(_g(item, "task_ref") or "")),
        "hero_purpose": html.escape(str(_g(item, "purpose") or "")),
        "hero_checks_sentence": html.escape(lead + tail),
        "hero_checks_meta": meta,
        "hero_tx_short": html.escape(tx[:13] + "…" if len(tx) > 16 else tx),
    }
    if pay_to.startswith("0x") and len(pay_to) == 42:
        out["lumen_pay_to_short"] = html.escape(f"{pay_to[:6]}…{pay_to[-4:]}")
    if jes:
        out["hero_je_label"] = f"<code>{html.escape(str(_g(jes[0], 'id')))}</code>"
    if not tx.startswith("sim-"):
        out["provenance_note"] = f"receipt tx_ref {tx[:12]} is not an offline x402 settlement"
    return out


# ---------------------------------------------------------------------- guilloche

def _wave(W: float, amp: float, cy: float, phase: float, periods: int, n: int) -> str:
    def f(x: float) -> float:
        return cy + amp * math.sin(2 * math.pi * periods * x / W + phase)

    xs = [W * i / n for i in range(-1, n + 2)]
    pts = [(x, f(x)) for x in xs]
    d = f"M{pts[1][0]:.1f} {pts[1][1]:.1f}"
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f}"
    return d


def guilloche(name: str) -> str:
    """Interlaced sine braids, the engraved line pattern on check safety paper. Tiles seamlessly in x."""
    if name == "band":
        W, H, n1, n2, n = 240, 160, 9, 7, 24
    elif name == "strip":
        W, H, n1, n2, n = 72, 12, 5, 3, 12
    else:
        raise BuildError(f"unknown guilloche {name!r}")
    paths = [_wave(W, H * 0.46, H / 2, 2 * math.pi * k / n1, 1, n) for k in range(n1)]
    paths += [_wave(W, H * 0.2, H / 2, 2 * math.pi * k / n2 + 0.4, 2, n) for k in range(n2)]
    return "".join(paths)


# ---------------------------------------------------------------------- checks

ALLOWED_CAPS = {
    "HTTP", "USDC", "EIP", "ACH", "MCP", "SDK", "API", "AI", "GPU", "GPUs", "ML", "GTM", "JE", "LUM",
    "WI", "LLP", "OK", "US", "USD", "AWS", "ID", "CLI", "MC", "PT", "NOAA", "NEXRAD", "USGS", "MIT", "NASA", "APIs",
}


class _TextScan(HTMLParser):
    CODEISH_TAGS = {"code", "pre", "kbd", "samp", "style", "script"}
    CODEISH_CLASSES = {"proto", "query", "tools", "rid", "code"}
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
            "use", "rect", "circle", "path", "line", "stop"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[bool] = []
        self.prose: list[str] = []
        self.attrs_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("alt", "aria-label", "title", "placeholder"):
            if a.get(k):
                self.attrs_text.append(a[k])
        if tag in self.VOID:
            return
        classes = set((a.get("class") or "").split())
        codeish = tag in self.CODEISH_TAGS or bool(classes & self.CODEISH_CLASSES) or (self.stack[-1] if self.stack else False)
        self.stack.append(codeish)

    def handle_startendtag(self, tag, attrs):
        a = dict(attrs)
        for k in ("alt", "aria-label", "title", "placeholder"):
            if a.get(k):
                self.attrs_text.append(a[k])

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.stack and self.stack[-1]:
            return
        if data.strip():
            self.prose.append(data)


def check_output(text: str) -> list[str]:
    errs: list[str] = []
    low = text.lower()
    for tag in ("<!doctype", "<html", "<head", "<body"):
        if re.search(re.escape(tag) + r"[\s>]", low):
            errs.append(f"page contract: the page must not contain {tag}>")
    if not text.lstrip().startswith("<title>"):
        errs.append("page contract: <title> must come first")
    if text.count("<style") != 1:
        errs.append("page contract: exactly one <style> block expected")
    if len(re.findall(r"<script\b", text)) != 1:
        errs.append("page contract: exactly one inline <script> expected")
    for m in re.finditer(r"<script\b[^>]*\bsrc\s*=", text, re.I):
        errs.append("external script found")
    for m in re.finditer(r"<link\b[^>]*>", text, re.I):
        href = re.search(r'href\s*=\s*"([^"]*)"', m.group(0))
        if href and not re.match(r"https://fonts\.(googleapis|gstatic)\.com(/|$)", href.group(1)):
            errs.append(f"external stylesheet or link not allowed: {href.group(1)}")
    for m in re.finditer(r'<(img|source|video|audio|iframe|embed|object|track)\b[^>]*\b(src|srcset|data|poster)\s*=\s*"(?!data:)([^"]+)"', text, re.I):
        errs.append(f"non inline media reference: {m.group(3)[:80]}")
    style = text[text.find("<style"):text.find("</style>")]
    if re.search(r"url\(\s*['\"]?(?!data:|#)", style):
        errs.append("CSS url() must be a data URI or a fragment")
    if "@import" in style:
        errs.append("CSS @import not allowed")
    script = text[text.find("<script"):text.rfind("</script>")]
    for pat, what in (
        (r"\bfetch\s*\(", "fetch"), (r"XMLHttpRequest", "XMLHttpRequest"), (r"\bWebSocket\b", "WebSocket"),
        (r"\bwindow\.open\s*\(", "window.open"), (r"\balert\s*\(", "alert"), (r"\bconfirm\s*\(", "confirm"),
        (r"\bprompt\s*\(", "prompt"), (r"\bprint\s*\(", "print"), (r"sendBeacon", "sendBeacon"),
    ):
        if re.search(pat, script):
            errs.append(f"script uses {what}, which the artifact frame refuses or which reaches another host")
    if re.search(r"<iframe\b", text, re.I):
        errs.append("iframes are not allowed")
    if re.search(r"<a\b[^>]*\bdownload\b", text, re.I):
        errs.append("<a download> is not allowed")
    if re.search(r'<form\b[^>]*\baction\s*=', text, re.I):
        errs.append("forms must not have an action")
    for m in re.finditer(r'<a\b[^>]*href="https?://[^"]*"[^>]*>', text):
        if 'target="_blank"' not in m.group(0):
            errs.append(f"external link must open in a new tab: {m.group(0)[:90]}")

    # copy rules from specs/00
    for ch, name in (("\u2014", "em dash"), ("\u2013", "en dash")):
        if ch in text:
            i = text.index(ch)
            errs.append(f"copy: {name} found near {text[max(0, i - 40):i + 20]!r}")
    scan = _TextScan()
    scan.feed(text)
    prose = " ".join(scan.prose)
    every = prose + " " + " ".join(scan.attrs_text)
    for arrow in ("→", "←", "↗", "⟶", "->", "=>", "»"):
        if arrow in every:
            errs.append(f"copy: arrow {arrow!r} in visible text")
    for m in re.finditer(r"[^\s]*:(?!//)[^\s]*", every):
        tok = m.group(0)
        if re.fullmatch(r"\d{1,2}:\d{2}", tok):
            continue
        errs.append(f"copy: colon in prose {tok!r}")
    for m in re.finditer(r"\b[A-Z][A-Z]+s?\b", every):
        word = m.group(0)
        if word not in ALLOWED_CAPS:
            errs.append(f"copy: all caps word {word!r}")
    for word in ("revolutioniz", "seamless", "unlock", "supercharg", "game-changer", "game changer", "leverage", "empower"):
        if word in every.lower():
            errs.append(f"copy: filler word {word!r}")
    return errs


# ---------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--draft", action="store_true", help="allow placeholder images and values")
    ap.add_argument("--repo-url", help="GitHub URL for the source links (default ELIX_REPO_URL, git remote, then a default)")
    ap.add_argument("--out", default=str(DIST), help="output path (default site/dist/index.html)")
    ap.add_argument("--report", default=str(REPORT), help="eval report to read (default eval/report.json)")
    args = ap.parse_args(argv)

    b = Build(draft=args.draft, repo_url=args.repo_url)
    b.load_report(Path(args.report))
    src = SRC.read_text(encoding="utf-8")
    try:
        out = b.resolve(src)
    except BuildError as e:
        print(f"build failed: {e}", file=sys.stderr)
        return 2

    contract = check_output(out)
    size = len(out.encode("utf-8"))
    if size > MAX_BYTES:
        contract.append(f"page is {size / 1e6:.2f} MB, over the 8 MB limit")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"source      {SRC.relative_to(ROOT)}")
    print(f"images      {len(b.real_images)} real, {len(b.placeholder_images)} placeholder")
    for name in sorted(b.image_meta):
        m = b.image_meta[name]
        tag = "placeholder" if m["placeholder"] else "real"
        print(f"  {name:<20} {m['width']}x{m['height']}  {m['bytes'] / 1024:7.1f} KB  {tag}  {m['source']}")
    print(f"eval        {REPORT if b.report is not None else b.report_error}")
    print(f"world       {b.world_source}")
    for n in b.notes:
        print(f"note        {n}")

    if contract:
        for c in contract:
            print(f"error       {c}", file=sys.stderr)
        print("build failed: the page breaks the artifact contract or the copy rules", file=sys.stderr)
        return 1
    if b.problems and not args.draft:
        for p in b.problems:
            print(f"missing     {p}", file=sys.stderr)
        print(f"build failed: {len(b.problems)} placeholder(s) without real values. Use --draft to build anyway.", file=sys.stderr)
        return 1
    for p in b.problems:
        print(f"draft       {p}")

    out_path.write_text(out, encoding="utf-8")
    kind = "DRAFT" if b.problems else "final"
    print(f"wrote       {out_path.relative_to(ROOT) if out_path.is_relative_to(ROOT) else out_path}  {size / 1024:.0f} KB  ({kind})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

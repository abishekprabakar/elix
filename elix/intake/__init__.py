"""PDF invoice intake. Bills arrive in every format, and Elix accepts a PDF the same way it accepts structured data.

`extract(pdf)` reads a PDF into a proposed invoice. With ANTHROPIC_API_KEY set (or a client passed in) Claude reads
it; otherwise, or when the Claude call fails, the offline text parser does, and the result says which one did. The
proposal then goes to `Elix.intake_invoice`, where deterministic checks decide whether it becomes a VendorInvoice.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Optional

from elix.contracts import ExtractedInvoice
from elix.intake import claude, pdf_text
from elix.intake.pdf_text import ParseError

MAX_BYTES = 10 * 1024 * 1024

__all__ = ["extract", "ParseError", "mode", "sha256", "MAX_BYTES", "samples", "sample_path"]


def mode(client: Any = None) -> str:
    """claude when a client is given or ANTHROPIC_API_KEY is set, else offline."""
    return "claude" if client is not None or os.environ.get("ANTHROPIC_API_KEY") else "offline"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def page_count(data: bytes) -> int:
    try:
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return len(pdf.pages)
    except Exception:
        return 0


SAMPLES_DIR = Path(__file__).resolve().parents[2] / "docs" / "samples"
SAMPLE_INFO = {
    "lumen-LUM-2026-10A.pdf": (
        "Lumen LUM-2026-10A, clean layout",
        "Lumen's invoice for October 1 to 15. On a fresh world it reconciles exactly like the double bill scenario."),
    "lumen-LUM-2026-10A-messy.pdf": (
        "Lumen LUM-2026-10A, messy layout",
        "The same invoice from another billing system, with a ruled grid, item codes and day month year dates."),
    "aws-AWS-2026-10-arithmetic-error.pdf": (
        "AWS-2026-10 with an arithmetic error",
        "The storage line says $974.60, but 41.2 TB months at $23.00 is $947.60."),
    "halcyon-HDL-50311-unknown-vendor.pdf": (
        "Halcyon HDL-50311 from an unknown vendor",
        "Halcyon Data Labs is not in Kestrel's vendor master."),
}


def samples() -> list[dict[str, Any]]:
    """The synthetic sample PDFs in docs/samples, in a fixed order."""
    out = []
    for name, (title, description) in SAMPLE_INFO.items():
        path = SAMPLES_DIR / name
        if path.is_file():
            out.append({"name": name, "title": title, "description": description, "bytes": path.stat().st_size})
    return out


def sample_path(name: str) -> Optional[Path]:
    if name not in SAMPLE_INFO:
        return None
    path = SAMPLES_DIR / name
    return path if path.is_file() else None


def check_pdf(data: bytes) -> None:
    if not data:
        raise ParseError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ParseError("The PDF is larger than 10 MB.")
    if not data.lstrip()[:5].startswith(b"%PDF-"):
        raise ParseError("The file is not a PDF.")


def extract(data: bytes, filename: str = "", client: Any = None, force_offline: bool = False) -> ExtractedInvoice:
    """Propose an invoice from a PDF. Claude when available, the offline parser otherwise, labeled either way."""
    check_pdf(data)
    fallback: Optional[str] = None
    if not force_offline and mode(client) == "claude":
        try:
            return claude.extract(data, client=client, pages=page_count(data))
        except claude.ClaudeExtractionError as exc:
            fallback = (f"The Claude call failed ({str(exc)[:160]}), so the offline PDF parser read this invoice "
                        f"instead.")
    result = pdf_text.parse(data, filename)
    if fallback:
        result = result.model_copy(update={"notes": [fallback, *result.notes]})
    return result

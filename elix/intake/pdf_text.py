"""The offline invoice parser. Text extraction with pdfplumber, then labels, tables and a right to left line reader.

It handles the two layouts real invoices tend to use. A ruled table is read cell by cell, mapping columns by their
headers (Qty, Item code, Description, Rate, Line total, and the usual synonyms). A table drawn with whitespace is
read line by line from the right, where quantity, unit price and amount sit. Header fields are found by their labels
wherever they are on the page. The vendor is the largest text on the first page, the letterhead.

The result is only a proposal. elix.engine.intake checks every value it returns.
"""

from __future__ import annotations

import io
import re
from typing import Optional

from elix.contracts import ExtractedInvoice, ExtractedLine
from elix.engine.intake import parse_amount, parse_date

LABEL = "Offline PDF parser"

DATE = (r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
        r"|\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4})")
RANGE = re.compile(rf"({DATE})\s*(?:to|through|thru|until|-|–)\s*({DATE})", re.IGNORECASE)
ONE_DATE = re.compile(DATE)
MONEY = re.compile(r"\(?-?\$?\s?\d[\d,]*(?:\.\d+)?\)?(?:\s?USD)?", re.IGNORECASE)
NUMBER_TOKEN = re.compile(r"^\(?-?\$?\d[\d,]*(?:\.\d+)?\)?$")

NUMBER_LABELS = re.compile(
    r"\b(?:invoice\s*(?:number|no\.?|num\.?|#|id)|inv\.?\s*(?:no\.?|#|number))\s*[:#]?\s*"
    r"((?=[A-Z0-9\-/_.]*\d)[A-Z0-9][A-Z0-9\-/_.]{2,40})", re.IGNORECASE)
ISSUE_LABELS = re.compile(r"\b(?:invoice\s+date|date\s+issued|issue\s+date|date\s+of\s+issue|issued\s+on|bill\s+date)"
                          r"\s*[:#]?\s*", re.IGNORECASE)
DUE_LABELS = re.compile(r"\b(?:due\s+date|payment\s+due|due\s+on|due\s+by|pay\s+by)\s*[:#]?\s*", re.IGNORECASE)
PERIOD_LABELS = re.compile(r"\b(?:billing\s+period|service\s+period|period\s+of\s+service|usage\s+period|"
                           r"statement\s+period)\s*[:#]?\s*", re.IGNORECASE)
TOTAL_LABELS = [re.compile(p, re.IGNORECASE) for p in (
    r"\bamount\s+due\b", r"\btotal\s+due\b", r"\bbalance\s+due\b", r"\binvoice\s+total\b", r"^\s*total\b")]
VENDOR_LABELS = re.compile(r"^\s*(?:from|vendor|supplier|remit\s+to|bill\s+from)\s*[:#]?\s+(.+)$", re.IGNORECASE)
STOP_ROW = re.compile(r"^\s*(?:sub\s*total|subtotal|total|tax|sales\s+tax|vat|net|amount\s+due|balance|payment)\b",
                      re.IGNORECASE)

HEADERS = {
    "quantity": ("qty", "quantity", "hours", "hrs", "units", "count"),
    "rate": ("unit price", "rate", "price", "unit cost", "cost per"),
    "amount": ("line total", "amount", "extended", "total", "subtotal"),
    "description": ("description", "item", "service", "details", "product"),
    "sku": ("item code", "sku", "product code", "code", "part"),
    "window": ("service window", "service period", "usage window", "period", "dates", "window"),
}


class ParseError(Exception):
    pass


# ---------------------------------------------------------------- normalizing what the page says


def norm_number(text: str) -> str:
    value = parse_amount(text or "")
    return "" if value is None else format(value, "f")


def norm_date(text: str) -> str:
    value = parse_date(text or "")
    return value.isoformat() if value else ""


def find_range(text: str) -> tuple[str, str, Optional[re.Match]]:
    m = RANGE.search(text or "")
    if not m:
        return "", "", None
    start, end = norm_date(m.group(1)), norm_date(m.group(2))
    return start, end, m if start and end else None


def first_date_after(pattern: re.Pattern, lines: list[str]) -> str:
    for line in lines:
        for m in pattern.finditer(line):
            d = ONE_DATE.search(line[m.end():])
            if d and line[m.end():][:d.start()].strip(" :#") == "":
                found = norm_date(d.group(0))
                if found:
                    return found
    return ""


def first_money_after(lines: list[str]) -> str:
    for pattern in TOTAL_LABELS:
        for line in lines:
            m = pattern.search(line)
            if not m:
                continue
            rest = line[m.end():]
            for token in MONEY.finditer(rest):
                value = norm_number(token.group(0))
                if value and ("." in token.group(0) or "$" in token.group(0)):
                    return value
    return ""


# ---------------------------------------------------------------- reading the PDF


def _letterhead(page) -> str:
    """The largest text on the page, which on an invoice is the vendor's name."""
    words = page.extract_words(extra_attrs=["size"], keep_blank_chars=False, use_text_flow=False)
    if not words:
        return ""
    biggest = max(round(w["size"], 1) for w in words)
    top = [w for w in words if round(w["size"], 1) >= biggest - 0.5]
    first = min(top, key=lambda w: (round(w["top"]), w["x0"]))
    row = sorted((w for w in top if abs(w["top"] - first["top"]) < 3), key=lambda w: w["x0"])
    name = " ".join(w["text"] for w in row).strip()
    return re.sub(r"\s+(?:tax\s+)?invoice$", "", name, flags=re.IGNORECASE)


def _header_map(row: list[str]) -> Optional[dict[str, int]]:
    cells = [re.sub(r"\s+", " ", (c or "")).strip().lower() for c in row]
    found: dict[str, int] = {}
    for role in ("sku", "window", "rate", "quantity", "amount", "description"):
        for index, cell in enumerate(cells):
            if index in found.values() or not cell:
                continue
            if any(cell == word or cell.startswith(word + " ") or cell.startswith(word + " (") or word in cell
                   for word in HEADERS[role]):
                found[role] = index
                break
    if {"quantity", "rate", "amount", "description"} <= set(found):
        return found
    return None


def lines_from_tables(tables: list[list[list[Optional[str]]]]) -> list[ExtractedLine]:
    out: list[ExtractedLine] = []
    for table in tables:
        if not table:
            continue
        columns = _header_map(table[0])
        if columns is None:
            continue
        for row in table[1:]:
            cell = lambda role: re.sub(r"\s+", " ", (row[columns[role]] or "")).strip() \
                if role in columns and columns[role] < len(row) else ""
            amount = norm_number(cell("amount"))
            description = cell("description")
            if not amount or not description or STOP_ROW.match(description):
                continue
            start, end, _ = find_range(cell("window") or description)
            out.append(ExtractedLine(description=description, sku=cell("sku"), quantity=norm_number(cell("quantity")),
                                     unit_price=norm_number(cell("rate")), amount=amount, usage_start=start,
                                     usage_end=end))
    return out


def lines_from_text(lines: list[str]) -> list[ExtractedLine]:
    """Rows of a whitespace table. Quantity, unit price and amount are the last three numbers on the row."""
    start = next((i for i, line in enumerate(lines)
                  if re.search(r"\bdescription\b", line, re.IGNORECASE)
                  and re.search(r"\b(?:amount|line total|total)\b", line, re.IGNORECASE)), None)
    if start is None:
        return []
    out: list[ExtractedLine] = []
    for line in lines[start + 1:]:
        if STOP_ROW.match(line):
            break
        tokens = line.split()
        tail = []
        while tokens and NUMBER_TOKEN.match(tokens[-1]) and len(tail) < 3:
            tail.insert(0, tokens.pop())
        if len(tail) < 3:
            if out and line.strip() and not any(NUMBER_TOKEN.match(t) for t in line.split()):
                out[-1] = out[-1].model_copy(update={"description": f"{out[-1].description} {line.strip()}"})
            continue
        left = " ".join(tokens)
        begin, end, match = find_range(left)
        description = (left[:match.start()] + left[match.end():]).strip(" ,") if match else left.strip(" ,")
        quantity, rate, amount = tail
        out.append(ExtractedLine(description=re.sub(r"\s+", " ", description), quantity=norm_number(quantity),
                                 unit_price=norm_number(rate), amount=norm_number(amount), usage_start=begin,
                                 usage_end=end))
    return out


def parse(data: bytes, filename: str = "") -> ExtractedInvoice:
    """Read an invoice PDF without a model. Raises ParseError if the file is not a readable PDF with text."""
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:  # pdfminer raises a handful of types for broken files
        raise ParseError(f"The file is not a PDF Elix can read. {exc.__class__.__name__}.") from exc
    with pdf:
        pages = len(pdf.pages)
        if not pages:
            raise ParseError("The PDF has no pages.")
        text_lines: list[str] = []
        tables: list[list[list[Optional[str]]]] = []
        for page in pdf.pages[:5]:
            text_lines += [line.strip() for line in (page.extract_text() or "").splitlines() if line.strip()]
            tables += page.extract_tables() or []
        letterhead = _letterhead(pdf.pages[0])
    if not text_lines:
        raise ParseError("The PDF has no text layer. Scanned invoices need the Claude extractor.")

    notes: list[str] = []
    vendor = letterhead
    if not vendor:
        for line in text_lines:
            m = VENDOR_LABELS.match(line)
            if m:
                vendor = m.group(1).split(",")[0].strip()
                break
    number = ""
    for line in text_lines:
        m = NUMBER_LABELS.search(line)
        if m:
            number = m.group(1).rstrip(".,")
            break
    period_start = period_end = ""
    for line in text_lines:
        m = PERIOD_LABELS.search(line)
        if m:
            period_start, period_end, _ = find_range(line[m.end():])
            if period_start:
                break
    lines = lines_from_tables(tables)
    if lines:
        notes.append(f"Read {len(lines)} {'line' if len(lines) == 1 else 'lines'} from a ruled table.")
    else:
        lines = lines_from_text(text_lines)
        if lines:
            notes.append(f"Read {len(lines)} {'line' if len(lines) == 1 else 'lines'} from the text layout.")
    joined = " ".join(text_lines)
    currency = "USD" if re.search(r"\bUSD\b|\$", joined) else ""
    m = re.search(r"\b(EUR|GBP|CAD|AUD|JPY|CHF)\b", joined)
    if m and not currency:
        currency = m.group(1)
    return ExtractedInvoice(
        extractor="offline", extractor_label=LABEL, model=None, vendor_name=vendor, invoice_number=number,
        issue_date=first_date_after(ISSUE_LABELS, text_lines), due_date=first_date_after(DUE_LABELS, text_lines),
        period_start=period_start, period_end=period_end, currency=currency, total=first_money_after(text_lines),
        lines=lines, pages=pages, notes=notes,
    )

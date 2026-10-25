"""Synthetic vendor invoice PDFs for the PDF intake demo and tests.

    python scripts/make_invoice_samples.py              writes docs/samples/*.pdf
    python scripts/make_invoice_samples.py --out DIR    writes them somewhere else

Every file says on the page that it is a synthetic sample. The vendors and Kestrel Labs are fictional.

    lumen-LUM-2026-10A.pdf                  Lumen's October 1 to 15 invoice, a clean layout with a whitespace table
    lumen-LUM-2026-10A-messy.pdf            the same invoice in a busier layout, a ruled grid, other column order,
                                            day month year dates and no currency symbols
    aws-AWS-2026-10-arithmetic-error.pdf    an AWS invoice whose storage line total does not equal quantity times rate
    halcyon-HDL-50311-unknown-vendor.pdf    an invoice from a vendor that is not in Kestrel's vendor master
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "docs" / "samples"
NOTICE = ("Synthetic sample for the Elix demo. The vendor and Kestrel Labs are fictional, and this is not a real "
          "invoice.")


@dataclass
class Line:
    description: str
    quantity: str
    rate: str
    amount: str
    window: str = ""
    sku: str = ""


@dataclass
class Invoice:
    filename: str
    vendor: str
    legal_name: str
    address: list[str]
    number: str
    issued: str
    due: str
    period: str
    lines: list[Line]
    subtotal: str
    total: str
    terms: str
    accent: tuple[float, float, float] = (0.07, 0.08, 0.09)
    notes: list[str] = field(default_factory=list)


BILL_TO = ["Kestrel Labs, Accounts payable", "1200 Prairie Avenue, Suite 300", "Lincoln, NE 68508"]

LUMEN_LINES = [
    Line("Reserved H100 cluster, Oct 1 to 15", "1", "$9,000.00", "$9,000.00", "2026-10-01 to 2026-10-15"),
    Line("On demand H100 hours, Oct 1 to 15", "2,140", "$2.10", "$4,494.00", "2026-10-01 to 2026-10-15"),
]

CLEAN: list[Invoice] = [
    Invoice(
        filename="lumen-LUM-2026-10A.pdf", vendor="Lumen Compute", legal_name="Lumen Compute, Inc.",
        address=["410 Harbor Street, Suite 900", "Portland, OR 97209", "billing@lumen-compute.example"],
        number="LUM-2026-10A", issued="2026-10-24", due="2026-11-23", period="2026-10-01 to 2026-10-15",
        lines=LUMEN_LINES, subtotal="$13,494.00", total="$13,494.00",
        terms="Payment terms Net 30. Pay by ACH to the account ending 4417.", accent=(0.20, 0.18, 0.55),
        notes=["On demand hours are metered on the shared H100 fleet."],
    ),
    Invoice(
        filename="aws-AWS-2026-10-arithmetic-error.pdf", vendor="Amazon Web Services",
        legal_name="Amazon Web Services, Inc.", address=["Accounts receivable", "Seattle, WA 98109",
                                                          "aws-receivables@example.com"],
        number="AWS-2026-10", issued="2026-10-24", due="2026-11-23", period="2026-10-01 to 2026-10-15",
        lines=[
            # Planted error. 41.2 TB months at $23.00 is $947.60, but the line says $974.60.
            Line("S3 storage, per TB month", "41.2", "$23.00", "$974.60", "2026-10-01 to 2026-10-15"),
            Line("Data egress, per TB", "3.1", "$90.00", "$279.00", "2026-10-01 to 2026-10-15"),
        ],
        subtotal="$1,253.60", total="$1,253.60", terms="Payment terms Net 30. Pay by ACH to the account ending 7730.",
        accent=(0.55, 0.33, 0.05),
    ),
    Invoice(
        filename="halcyon-HDL-50311-unknown-vendor.pdf", vendor="Halcyon Data Labs", legal_name="Halcyon Data Labs LLC",
        address=["88 Meridian Way", "Boulder, CO 80302", "ar@halcyon-data.example"],
        number="HDL-50311", issued="2026-10-22", due="2026-11-21", period="2026-10-01 to 2026-10-31",
        lines=[Line("Hail feature store subscription, October", "1", "$2,400.00", "$2,400.00",
                    "2026-10-01 to 2026-10-31")],
        subtotal="$2,400.00", total="$2,400.00", terms="Payment terms Net 30. Wire or ACH.", accent=(0.10, 0.40, 0.35),
        notes=["Thank you for choosing Halcyon."],
    ),
]


def _setup() -> None:
    from reportlab import rl_config

    rl_config.invariant = 1  # same bytes on every run


def draw_clean(inv: Invoice, path: Path) -> None:
    """A tidy invoice. Labels and values on one line, a table aligned with whitespace and no rules."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    width, height = letter
    c = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    c.setTitle(f"{inv.vendor} invoice {inv.number} (synthetic sample)")
    c.setAuthor("Elix demo, synthetic data")
    c.setSubject(NOTICE)

    c.setFillColorRGB(0.96, 0.95, 0.90)
    c.rect(0, height - 26, width, 26, stroke=0, fill=1)
    c.setFillColorRGB(0.45, 0.33, 0.05)
    c.setFont("Helvetica", 8.5)
    c.drawCentredString(width / 2, height - 17, NOTICE)

    left, right = 54, width - 54
    y = height - 74
    c.setFillColorRGB(*inv.accent)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(left, y, inv.vendor)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.setFont("Helvetica", 9)
    for i, row in enumerate([inv.legal_name, *inv.address]):
        c.drawString(left, y - 18 - i * 12, row)

    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(right, y - 52 - 12 * len(inv.address), "Invoice")
    fields = [("Invoice number", inv.number), ("Invoice date", inv.issued), ("Due date", inv.due),
              ("Billing period", inv.period)]
    fy = y - 76 - 12 * len(inv.address)
    for label, value in fields:
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawString(right - 230, fy, label)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(right - 130, fy, value)
        fy -= 15

    by = y - 76 - 12 * len(inv.address)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(left, by, "Bill to")
    c.setFont("Helvetica", 9)
    for i, row in enumerate(BILL_TO):
        c.drawString(left, by - 14 - i * 12, row)

    ty = fy - 34
    cols = [left, left + 196, left + 330, left + 396, right]
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    c.drawString(cols[0], ty, "Description")
    c.drawString(cols[1], ty, "Service window")
    c.drawRightString(cols[2] + 40, ty, "Quantity")
    c.drawRightString(cols[3] + 54, ty, "Unit price")
    c.drawRightString(cols[4], ty, "Amount")
    c.setStrokeColorRGB(0.82, 0.82, 0.8)
    c.line(left, ty - 6, right, ty - 6)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    ly = ty - 22
    for line in inv.lines:
        c.setFont("Helvetica", 9.5)
        c.drawString(cols[0], ly, line.description)
        c.setFont("Helvetica", 9)
        c.drawString(cols[1], ly, line.window)
        c.drawRightString(cols[2] + 40, ly, line.quantity)
        c.drawRightString(cols[3] + 54, ly, line.rate)
        c.drawRightString(cols[4], ly, line.amount)
        ly -= 20
    c.line(left, ly + 8, right, ly + 8)

    totals = [("Subtotal", inv.subtotal), ("Tax", "$0.00"), ("Total due", f"{inv.total} USD")]
    sy = ly - 10
    for label, value in totals:
        bold = label == "Total due"
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9)
        c.drawString(right - 200, sy, label)
        c.drawRightString(right, sy, value)
        sy -= 16

    c.setFont("Helvetica", 8.5)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    ny = sy - 30
    for note in [inv.terms, *inv.notes]:
        c.drawString(left, ny, note)
        ny -= 12
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(left, 40, NOTICE)
    c.drawRightString(right, 40, "Page 1 of 1")
    c.showPage()
    c.save()


def draw_messy(path: Path) -> None:
    """The same Lumen invoice from a different billing system. Ruled grid, quantity first, item codes, wrapped
    descriptions, day month year dates, no currency symbols, and an invoice level service period only."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=40, rightMargin=40, topMargin=36, bottomMargin=40,
                            title="Lumen Compute invoice LUM-2026-10A (synthetic sample)",
                            author="Elix demo, synthetic data", subject=NOTICE, invariant=1)
    small = ParagraphStyle("small", fontName="Helvetica", fontSize=8, leading=10)
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=11.5)
    head = ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=17, leading=20, alignment=2)
    grid = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, colors.HexColor("#8A8F96")),
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ])

    story = [
        Paragraph(NOTICE, ParagraphStyle("banner", parent=small, textColor=colors.HexColor("#7A5A10"))),
        Spacer(1, 10),
        Table([[Paragraph("Page 1 of 1<br/>Customer copy", small), Paragraph("Lumen Compute Inc.", head)]],
              colWidths=[200, 315]),
        Spacer(1, 6),
        Table([["Inv no.", "LUM-2026-10A", "Date issued", "24 Oct 2026"],
               ["Customer ref", "KES-0042", "Payment due", "23 Nov 2026"],
               ["Service period", "1 Oct 2026 to 15 Oct 2026", "Currency", "USD"]],
              colWidths=[80, 170, 80, 185], style=TableStyle([
                  *grid.getCommands(), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF0F3")),
                  ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#EEF0F3"))])),
        Spacer(1, 12),
        Paragraph("From Lumen Compute Inc., accounts receivable, 410 Harbor Street, Suite 900, Portland, OR 97209", body),
        Paragraph("Bill to Kestrel Labs, 1200 Prairie Avenue, Suite 300, Lincoln, NE 68508", body),
        Spacer(1, 12),
    ]
    rows = [["Qty", "Item code", "Description", "Rate", "Line total"],
            ["1", "h100-reserved-halfmonth", Paragraph("Reserved H100 cluster<br/>half month, first half of October",
                                                       small), "9,000.00", "9,000.00"],
            ["2,140", "h100-ondemand-hr", Paragraph("H100 on demand GPU hours<br/>metered usage, all regions", small),
             "2.10", "4,494.00"]]
    lines = Table(rows, colWidths=[44, 128, 205, 64, 74], repeatRows=1)
    lines.setStyle(TableStyle([
        *grid.getCommands(), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F2430")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("ALIGN", (0, 0), (0, -1), "RIGHT"),
    ]))
    story += [lines, Spacer(1, 10)]
    totals = Table([["Net", "13,494.00"], ["Sales tax 0%", "0.00"], ["Amount due (USD)", "13,494.00"]],
                   colWidths=[120, 74], hAlign="RIGHT")
    totals.setStyle(TableStyle([*grid.getCommands(), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                                ("FONT", (0, 2), (-1, 2), "Helvetica-Bold", 9)]))
    story += [totals, Spacer(1, 16),
              Paragraph("Questions about this bill? Write to billing@lumen-compute.example and quote the invoice "
                        "number. Remit by ACH to the account ending 4417 within 30 days.", body),
              Spacer(1, 6),
              Paragraph("Usage is metered on the shared H100 fleet and includes all hours recorded by the meter in the "
                        "service period.", small),
              Spacer(1, 18), Paragraph(NOTICE, small)]
    doc.build(story)


def make_all(out: Path = SAMPLES) -> list[Path]:
    _setup()
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for inv in CLEAN:
        path = out / inv.filename
        draw_clean(inv, path)
        written.append(path)
    messy = out / "lumen-LUM-2026-10A-messy.pdf"
    draw_messy(messy)
    written.insert(1, messy)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=SAMPLES)
    args = parser.parse_args()
    for path in make_all(args.out):
        print(f"wrote {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")


if __name__ == "__main__":
    main()

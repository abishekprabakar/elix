"""Writes eval/report.md from the numbers in eval/report.json. Nothing here is typed by hand."""

from __future__ import annotations

from pathlib import Path
from typing import Any

LABELS = {
    "DUPLICATE_RETRY": "Duplicate retry", "PAYEE_CHANGED": "Payee changed", "PRICE_ABOVE_CONTRACT": "Price above contract",
    "AMOUNT_MISMATCH": "402 amount mismatch", "OVER_TXN_CAP": "Over per purchase cap", "BUDGET_EXCEEDED": "Budget exceeded",
    "SPLIT_PURCHASE": "Split purchase", "UNKNOWN_VENDOR": "Unknown vendor", "MISSING_PURPOSE": "Missing purpose",
    "MEMO_INSTRUCTIONS": "Instructions in memo", "SELF_APPROVAL": "Agent approves itself",
    "INVOICE_DOUBLE_BILL": "Invoice double bills x402 usage", "INVOICE_OVERBILL": "Invoice over meter",
    "INVOICE_RATE": "Invoice rate mismatch",
}


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Elix eval report",
        "",
        report["method"],
        "",
        f"Seed {report['seed']}. Mode {report['mode']}. Generated {report['generated_at']} in "
        f"{report['runtime_seconds']} seconds. Engine version {report.get('engine_git_hash') or 'not committed'}.",
        "",
        "## Headline",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| Anomalies planted | {report['anomalies_total']} |",
        f"| Anomalies caught | {report['anomalies_caught']} |",
        f"| Anomalies paid without a person | {report['anomalies_auto_paid']} |",
        f"| Categories fully caught | {report['categories_caught']} of {report['categories_total']} |",
        f"| Automation precision | {pct(report['automation_precision'])} |",
        f"| Clean purchases automated before learning (days 1 to 30) | {pct(report['automation_rate_before_learning'])} |",
        f"| Clean purchases automated after learning (days 31 to 90) | {pct(report['automation_rate_after_learning'])} |",
        f"| Clean scored items paid without a person | {report['clean_auto_paid']} of {report['clean_total']} |",
        f"| Audit chain verified | {'yes' if report['chain_ok'] else 'no'} ({report['chain_checked']} events) |",
        "",
        "## By category",
        "",
        "| Category | Planted | Caught | Not auto paid | Auto paid | How it was planted |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in report["per_category"]:
        lines.append(f"| {LABELS.get(row['category'], row['category'])} | {row['planted']} | {row['caught']} | "
                     f"{row['not_auto_paid']} | {row['auto_paid']} | {row['notes']} |")
    lines += ["", "## Policies learned after warm up", ""]
    if report["policies_learned"]:
        for policy in report["policies_learned"]:
            lines.append(f"- {policy['id']}. {policy['rationale']}")
    else:
        lines.append("- None.")
    lines += [
        "",
        "## How to read this",
        "",
        "An anomaly counts as caught when Elix did not pay it without a person and flagged at least one of the "
        "expected rules. Some categories also check the state (a missing purpose must wait for information), the "
        "proposed short pay (within $1 of the double billed amount) or the refused approval in the audit log. "
        "The answer key is in answer_key.json next to this file and the full event stream is in events.jsonl.",
        "",
    ]
    path.write_text("\n".join(lines))

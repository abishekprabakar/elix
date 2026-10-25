"""The eval: an answer key written without the engine, and the engine scored against it."""

from __future__ import annotations

import ast
import json

from elix.eval.generate import CATEGORIES, generate, summarize

from conftest import ROOT


def test_generator_never_imports_the_engine():
    tree = ast.parse((ROOT / "elix" / "eval" / "generate.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not any(name.startswith("elix.engine") for name in imported), imported


def test_stream_has_four_of_each_category_and_is_deterministic():
    events, key = generate(11)
    counts = summarize(key)
    assert all(counts[c] == 4 for c in CATEGORIES)
    assert 550 <= sum(1 for e in events if e["kind"] == "purchase") <= 650
    assert all(row["scored"] for row in key if row["planted"])
    assert generate(11) == (events, key)
    assert generate(12)[0] != events


def test_fast_eval_catches_every_category_and_learning_helps(tmp_path):
    from elix.eval.run import run_eval

    report = run_eval(seed=11, fast=True, out=tmp_path / "report.json")
    assert report["anomalies_auto_paid"] == 0
    assert report["categories_caught"] == report["categories_total"] == len(CATEGORIES)
    assert report["anomalies_caught"] == report["anomalies_total"] == 56
    assert report["automation_rate_after_learning"] > report["automation_rate_before_learning"]
    assert report["automation_precision"] == 1.0 and report["chain_ok"]
    assert report["fast"] and "fast" in report["mode"]
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["anomalies_total"] == 56
    markdown = (tmp_path / "report.md").read_text()
    assert "| Anomalies paid without a person | 0 |" in markdown and "—" not in markdown
    assert (tmp_path / "answer_key.json").exists() and (tmp_path / "events.jsonl").exists()


def test_eval_through_the_real_offline_rail(tmp_path, vendor_server):
    from elix.eval.run import run_eval

    report = run_eval(seed=11, fast=False, out=tmp_path / "report.json", vendor_base_url=vendor_server)
    assert report["anomalies_auto_paid"] == 0 and report["categories_caught"] == len(CATEGORIES)
    assert not report["fast"] and "offline x402" in report["mode"]

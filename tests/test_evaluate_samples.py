from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_sample_cases import build_report, build_report_paths


def test_build_rule_only_report() -> None:
    report = build_report(mode="rule_only")
    assert report["mode"] == "rule_only"
    assert report["summary"]["total"] > 0
    assert report["cases"]
    assert all("file_name" in item for item in report["cases"])


def test_build_report_paths_are_mode_specific() -> None:
    rule_report = build_report(mode="rule_only")
    rule_json, rule_md = build_report_paths(rule_report)

    assert rule_json.name == "sample_eval_rule_only.json"
    assert rule_md.name == "sample_eval_rule_only.md"

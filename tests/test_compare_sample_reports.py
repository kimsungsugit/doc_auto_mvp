from __future__ import annotations

from scripts.compare_sample_reports import build_comparison


def test_build_comparison_detects_improvements() -> None:
    rule_report = {
        "mode": "rule-only",
        "summary": {"matched": 1, "total": 3, "ratio": 0.33},
        "cases": [
            {
                "file_name": "sample.pdf",
                "matches": {"supplier_name": False, "buyer_name": False, "issue_date": True},
                "score": {"matched": 1, "total": 3, "ratio": 0.33},
            }
        ],
    }
    ai_report = {
        "mode": "openai-mini",
        "model": "gpt-5.4-mini",
        "summary": {"matched": 3, "total": 3, "ratio": 1.0},
        "cases": [
            {
                "file_name": "sample.pdf",
                "matches": {"supplier_name": True, "buyer_name": True, "issue_date": True},
                "score": {"matched": 3, "total": 3, "ratio": 1.0},
            }
        ],
    }

    comparison = build_comparison(rule_report, ai_report)

    assert comparison["summary"]["delta"] == 2
    assert comparison["summary"]["improved_case_count"] == 1
    assert comparison["summary"]["regressed_case_count"] == 0
    assert comparison["cases"][0]["improved_fields"] == ["supplier_name", "buyer_name"]

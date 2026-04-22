from __future__ import annotations

import json
from pathlib import Path


def test_sample_cases_are_defined() -> None:
    cases_path = Path("samples/sample_cases.json")
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    assert len(cases) >= 25
    for case in cases:
        assert "file_name" in case
        assert "lines" in case
        assert "expected" in case
        assert case["file_name"].endswith(".pdf")
        assert case["lines"]
        assert case.get("render_mode", "plain") in {"plain", "form", "scan"}

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKETING_DIR = ROOT / "marketing"
TEMPLATE_PATH = MARKETING_DIR / "submission_cover.template.html"
PROFILE_PATH = MARKETING_DIR / "submission_profile.json"
OUTPUT_PATH = MARKETING_DIR / "submission_cover.html"


def render_cover(template: str, profile: dict[str, str]) -> str:
    return template.format(**profile)


def main() -> None:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    rendered = render_cover(template, profile)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()

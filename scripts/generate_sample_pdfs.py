from __future__ import annotations

import json
import random
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BASE_DIR / "samples"
CASES_PATH = SAMPLES_DIR / "sample_cases.json"
OUTPUT_DIR = SAMPLES_DIR / "generated"
FONT_NAME = "MalgunGothic"
FONT_PATHS = [
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("C:/Windows/Fonts/malgunbd.ttf"),
]


def register_font() -> str:
    for path in FONT_PATHS:
        if path.exists():
            pdfmetrics.registerFont(TTFont(FONT_NAME, str(path)))
            return FONT_NAME
    return "Helvetica"


def draw_plain(pdf: canvas.Canvas, font_name: str, lines: list[str]) -> None:
    width, height = A4
    y = height - 60
    for index, line in enumerate(lines):
        pdf.setFillColor(HexColor("#111111"))
        pdf.setFont(font_name, 18 if index == 0 else 12)
        pdf.drawString(48, y, line)
        y -= 28


def draw_form(pdf: canvas.Canvas, font_name: str, lines: list[str]) -> None:
    width, height = A4
    pdf.setStrokeColor(HexColor("#b7c1c4"))
    pdf.setFillColor(HexColor("#f7fbfb"))
    pdf.roundRect(34, height - 114, width - 68, 64, 18, stroke=0, fill=1)
    pdf.setFillColor(HexColor("#0f766e"))
    pdf.setFont(font_name, 22)
    pdf.drawString(48, height - 78, lines[0])

    pdf.setStrokeColor(HexColor("#d2d8db"))
    pdf.setFillColor(HexColor("#fffdfa"))
    pdf.roundRect(34, height - 320, width - 68, 170, 18, stroke=1, fill=1)
    pdf.roundRect(34, height - 700, width - 68, 340, 18, stroke=1, fill=1)

    y = height - 176
    for line in lines[1:4]:
        pdf.setFillColor(HexColor("#24313a"))
        pdf.setFont(font_name, 11.5)
        pdf.drawString(48, y, line)
        y -= 24

    pdf.setStrokeColor(HexColor("#e3e6e8"))
    for row in range(5):
        top = height - 390 - row * 50
        pdf.line(48, top, width - 48, top)
    pdf.line(150, height - 640, 150, height - 390)
    pdf.line(390, height - 640, 390, height - 390)

    y = height - 422
    for line in lines[4:]:
        pdf.setFillColor(HexColor("#111111"))
        pdf.setFont(font_name, 11.5)
        pdf.drawString(56, y, line)
        y -= 40

    pdf.setFillColor(HexColor("#7a7f85"))
    pdf.setFont(font_name, 9)
    pdf.drawRightString(width - 40, 36, "공개 양식 기반 합성 샘플")


def draw_scan(pdf: canvas.Canvas, font_name: str, lines: list[str], seed_text: str) -> None:
    width, height = A4
    rng = random.Random(seed_text)
    pdf.setFillColor(HexColor("#f1efe9"))
    pdf.rect(0, 0, width, height, stroke=0, fill=1)

    for _ in range(36):
        shade = 236 + rng.randint(0, 10)
        color = HexColor(f"#{shade:02x}{shade:02x}{shade:02x}")
        pdf.setFillColor(color)
        x = rng.uniform(0, width)
        y = rng.uniform(0, height)
        w = rng.uniform(18, 54)
        h = rng.uniform(1.2, 2.4)
        pdf.rect(x, y, w, h, stroke=0, fill=1)

    pdf.saveState()
    pdf.translate(44, height - 72)
    pdf.rotate(-1.4)
    y = 0
    for index, line in enumerate(lines):
        font_size = 17 if index == 0 else 12
        pdf.setFont(font_name, font_size)
        gray = 34 + rng.randint(-8, 8)
        gray = max(20, min(gray, 80))
        pdf.setFillColor(HexColor(f"#{gray:02x}{gray:02x}{gray:02x}"))
        pdf.drawString(rng.uniform(-1.2, 1.8), y, line)
        y -= 28 + rng.uniform(-2, 3)
    pdf.restoreState()

    pdf.saveState()
    pdf.translate(width - 160, 98)
    pdf.rotate(-13)
    pdf.setStrokeColor(HexColor("#d6a6a6"))
    pdf.setFillColor(HexColor("#d8a3a3"))
    pdf.roundRect(0, 0, 108, 34, 8, stroke=1, fill=0)
    pdf.setFont(font_name, 10)
    pdf.drawCentredString(54, 12, "SCAN SAMPLE")
    pdf.restoreState()


def generate_pdf(output_path: Path, lines: list[str], render_mode: str = "plain") -> None:
    font_name = register_font()
    pdf = canvas.Canvas(str(output_path), pagesize=A4)

    if render_mode == "form":
        draw_form(pdf, font_name, lines)
    elif render_mode == "scan":
        draw_scan(pdf, font_name, lines, output_path.name)
    else:
        draw_plain(pdf, font_name, lines)

    pdf.save()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    expected_fields: dict[str, dict[str, str]] = {}

    for case in cases:
        output_path = OUTPUT_DIR / case["file_name"]
        generate_pdf(output_path, case["lines"], case.get("render_mode", "plain"))
        expected_fields[case["file_name"]] = case["expected"]

    (OUTPUT_DIR / "expected_fields.json").write_text(
        json.dumps(expected_fields, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"generated {len(cases)} sample PDFs in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

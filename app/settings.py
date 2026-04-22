from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_BASE = Path(__file__).resolve().parent.parent

BASE_DIR = Path(os.getenv("APP_BASE_DIR", str(_DEFAULT_BASE)))
APP_DIR = BASE_DIR / "app"
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "storage")))
TEMPLATES_DIR = Path(os.getenv("TEMPLATES_DIR", str(BASE_DIR / "templates")))
CUSTOMER_TEMPLATE_PATH = TEMPLATES_DIR / "customer_invoice_template.xlsx"

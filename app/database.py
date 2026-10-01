"""JSON store with atomic writes — simple persistence layer."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from flask import current_app


def resolve_data_dir() -> Path:
    candidates = []
    env = (current_app.config.get("DATA_DIR") or os.environ.get("DATA_DIR") or "").strip()
    if env:
        candidates.append(Path(env))
    base = Path(current_app.root_path).parent if Path(current_app.root_path).name == "app" else Path(current_app.root_path)
    # app package is in app/, project root is parent
    root = Path(__file__).resolve().parent.parent
    candidates.append(root / "data")
    candidates.append(Path("/tmp/kairozen-store-data"))
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            test = d / ".w"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return d
        except Exception:
            continue
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return resolve_data_dir() / "db.json"


def upload_dir() -> Path:
    d = resolve_data_dir() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _seed() -> dict[str, Any]:
    seed_file = Path(__file__).resolve().parent.parent / "data" / "db.json"
    if seed_file.exists():
        with open(seed_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "settings": {
            "SITE_NAME": "Kairozen Store",
            "SITE_TAGLINE": "Premium Digital Services · Instant Delivery",
            "ADMIN_PASSWORD": "admin123",
            "SHOP_NAME": "Kairozen Store",
            "TELEGRAM": "https://t.me/",
            "CURRENCY": "USD",
            "PAYMENT_NOTE": "Scan KHQR · auto deliver",
            "KHMER_SECRET_KEY": "",
            "KHMER_PROFILE_KEY": "",
            "KHMER_MACHINE_ID": "",
            "KHMER_MERCHANT_NAME": "Kairozen Store",
            "BAKONG_ID": "",
            "PAYMENT_QR": "",
        },
        "categories": [],
        "products": [],
        "stock_files": {},
        "orders": [],
        "next_order": 1001,
        "next_product": 1,
    }


def read() -> dict[str, Any]:
    path = db_path()
    if not path.exists():
        data = _seed()
        write(data)
        return data
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write(data: dict[str, Any]) -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

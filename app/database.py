"""Persistent store.

Backends (auto-selected):
  1. PostgreSQL  -> when DATABASE_URL is set   (survives every deploy / restart)
  2. JSON file   -> DATA_DIR/db.json            (use with a persistent disk)

Safety rules:
  * The seed file (data/seed.json) is only used the FIRST time, when no data exists.
  * New settings keys are merged in on read, existing values are never overwritten.
  * Every write is atomic and keeps a .bak copy (file backend).
  * Media (logo / product images) is stored in the same backend, not on the
    ephemeral container disk.
"""
from __future__ import annotations

import copy
import json
import mimetypes
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from flask import current_app

_lock = threading.RLock()
ROOT = Path(__file__).resolve().parent.parent
PERSISTENT_MOUNT = "/var/data"


# ───────────────────────── configuration ─────────────────────────
def database_url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


def use_postgres() -> bool:
    return bool(database_url())


def storage_info() -> dict[str, Any]:
    """Tell the admin panel whether data survives a redeploy."""
    if use_postgres():
        return {"mode": "postgres", "persistent": True}
    on_render = bool(os.environ.get("RENDER"))
    chosen = resolve_data_dir()
    env = (current_app.config.get("DATA_DIR") or os.environ.get("DATA_DIR") or "").strip()
    if (env and str(chosen) == str(Path(env))) or str(chosen) == PERSISTENT_MOUNT:
        return {"mode": "disk", "persistent": True, "path": str(chosen)}
    return {
        "mode": "ephemeral" if on_render else "local-file",
        "persistent": not on_render,
        "path": str(resolve_data_dir()),
    }


def resolve_data_dir() -> Path:
    candidates = []
    env = (current_app.config.get("DATA_DIR") or os.environ.get("DATA_DIR") or "").strip()
    if env:
        candidates.append(Path(env))
    # Render persistent disk mount point — used automatically when present
    if Path(PERSISTENT_MOUNT).is_dir():
        candidates.append(Path(PERSISTENT_MOUNT))
    candidates.append(ROOT / "data")
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
    d = ROOT / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return resolve_data_dir() / "db.json"


def upload_dir() -> Path:
    d = resolve_data_dir() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ───────────────────────── defaults / migration ─────────────────────────
def _default_settings() -> dict[str, Any]:
    return {
        "SITE_NAME": "Kairozen Store",
        "SITE_TAGLINE": "Premium Digital Services · Instant Delivery",
        "SITE_DESCRIPTION": "Kairozen Store — ហាងលក់គណនី Premium (Netflix, CapCut, Spotify, YouTube) ដឹកជញ្ជូនភ្លាមៗ បង់ប្រាក់ KHQR។",
        "SITE_LOGO": "",          # media name of uploaded logo
        "LOGO_VERSION": 0,        # cache-buster for favicon / logo
        "ADMIN_PASSWORD": os.environ.get("ADMIN_PASSWORD", "admin123"),
        "SHOP_NAME": "Kairozen Store",
        "TELEGRAM": "https://t.me/",
        "CURRENCY": "USD",
        "PAYMENT_NOTE": "Scan KHQR · auto deliver",
        "KHPAY_API_KEY": "",
        "KHPAY_WEBHOOK_SECRET": "",
        "KHPAY_CALLBACK_URL": "",
        "SITE_URL": "",
        "BAKONG_ID": "",
        "PAYMENT_QR": "",
        "GOOGLE_CLIENT_ID": "",
        "REQUIRE_LOGIN": True,
    }


def _default_doc() -> dict[str, Any]:
    seed_file = ROOT / "data" / "seed.json"
    doc: dict[str, Any] = {}
    if seed_file.exists():
        try:
            with open(seed_file, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except Exception:
            doc = {}
    doc.setdefault("settings", {})
    doc.setdefault("categories", [])
    doc.setdefault("products", [])
    doc.setdefault("stock_files", {})
    doc.setdefault("orders", [])
    doc.setdefault("users", {})
    doc.setdefault("next_order", 1001)
    doc.setdefault("next_product", 1)
    return doc


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Add missing keys only. Never overwrite what already exists."""
    data.setdefault("categories", [])
    data.setdefault("products", [])
    data.setdefault("stock_files", {})
    data.setdefault("orders", [])
    data.setdefault("next_order", 1001)
    data.setdefault("next_product", 1)
    # users MUST be a dict keyed by user id (not a list)
    u = data.get("users")
    if not isinstance(u, dict):
        fixed = {}
        if isinstance(u, list):
            for item in u:
                if isinstance(item, dict) and item.get("id"):
                    fixed[str(item["id"])] = item
        data["users"] = fixed
    else:
        data.setdefault("users", {})
    s = data.setdefault("settings", {})
    for k, v in _default_settings().items():
        s.setdefault(k, v)
    return data


def _first_run_doc() -> dict[str, Any]:
    doc = _default_doc()
    s = doc.setdefault("settings", {})
    for k, v in _default_settings().items():
        s.setdefault(k, v)
    return doc


# ───────────────────────── postgres backend ─────────────────────────
def _pg():
    import psycopg2  # imported lazily so file mode needs no extra package

    conn = psycopg2.connect(database_url(), connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS kz_store ("
            "k TEXT PRIMARY KEY, v TEXT NOT NULL, updated_at TIMESTAMPTZ DEFAULT now())"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS kz_media ("
            "name TEXT PRIMARY KEY, mime TEXT NOT NULL, data BYTEA NOT NULL, "
            "created_at TIMESTAMPTZ DEFAULT now())"
        )
    conn.commit()
    return conn


def _pg_read() -> dict[str, Any] | None:
    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT v FROM kz_store WHERE k = 'db'")
            row = cur.fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()


def _pg_write(data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO kz_store (k, v) VALUES ('db', %s) "
                "ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v, updated_at = now()",
                (payload,),
            )
        conn.commit()
    finally:
        conn.close()


# ───────────────────────── file backend ─────────────────────────
def _file_read() -> dict[str, Any] | None:
    path = db_path()
    for p in (path, path.with_suffix(".json.bak")):
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                continue
    return None


def _file_write(data: dict[str, Any]) -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if path.exists():
        try:
            shutil.copyfile(path, path.with_suffix(".json.bak"))
        except Exception:
            pass
    tmp.replace(path)


# ───────────────────────── public API ─────────────────────────
def read() -> dict[str, Any]:
    with _lock:
        raw = _pg_read() if use_postgres() else _file_read()
        if raw is None:
            legacy = ROOT / "data" / "db.json"
            data = None
            if not use_postgres() and legacy.exists() and legacy != db_path():
                try:
                    with open(legacy, "r", encoding="utf-8") as f:
                        data = _normalize(json.load(f))
                except Exception:
                    data = None
            data = data or _first_run_doc()
            write(data)
            return data
        return _normalize(raw)


def write(data: dict[str, Any]) -> None:
    with _lock:
        if use_postgres():
            _pg_write(data)
        else:
            _file_write(data)


def export_all() -> dict[str, Any]:
    return copy.deepcopy(read())


def import_all(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or "products" not in payload or "settings" not in payload:
        raise ValueError("Invalid backup file")
    write(_normalize(payload))


# ───────────────────────── media (logo / images) ─────────────────────────
def media_put(name: str, mime: str, blob: bytes) -> None:
    if use_postgres():
        import psycopg2

        conn = _pg()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO kz_media (name, mime, data) VALUES (%s, %s, %s) "
                    "ON CONFLICT (name) DO UPDATE SET mime = EXCLUDED.mime, data = EXCLUDED.data",
                    (name, mime, psycopg2.Binary(blob)),
                )
            conn.commit()
        finally:
            conn.close()
        return
    (upload_dir() / name).write_bytes(blob)


def media_get(name: str) -> tuple[str, bytes] | None:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    if use_postgres():
        conn = _pg()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT mime, data FROM kz_media WHERE name = %s", (name,))
                row = cur.fetchone()
            if row:
                return row[0], bytes(row[1])
        finally:
            conn.close()
        return None
    p = upload_dir() / name
    if p.exists() and p.is_file():
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return mime, p.read_bytes()
    return None

"""Server-rendered pages."""
from __future__ import annotations

import os

from flask import Blueprint, abort, jsonify, render_template, send_from_directory

from app import database as db

bp = Blueprint("web", __name__)

# Private admin URL — change via env ADMIN_PATH (no leading slash)
ADMIN_PATH = (os.environ.get("ADMIN_PATH") or "kz-panel-8391").strip().strip("/")


@bp.get("/")
def home():
    return render_template("shop/index.html")


@bp.get(f"/{ADMIN_PATH}")
def admin_panel():
    """Hidden admin UI — not linked from the storefront."""
    return render_template("admin/index.html")


@bp.get("/admin")
def admin_decoy():
    """Old path: do not reveal panel."""
    abort(404)


@bp.get("/health")
def health():
    return jsonify({
        "ok": True,
        "app": "Kairozen Store",
        "data_dir": str(db.resolve_data_dir()),
        "db_exists": db.db_path().exists(),
    })


@bp.get("/api/media/<path:filename>")
def media(filename: str):
    uploads = db.upload_dir()
    path = uploads / filename
    if path.exists():
        return send_from_directory(uploads, filename)
    from flask import current_app
    return send_from_directory(current_app.static_folder, filename)

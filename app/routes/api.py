"""Public JSON API."""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from app import database as db
from app import security
from app.routes.auth import current_user
from app.services import order_service

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.get("/catalog")
def catalog():
    data = db.read()
    products = [p for p in (data.get("products") or []) if p.get("active", True)]
    settings = data.get("settings") or {}
    return jsonify({
        "ok": True,
        "settings": {
            "SITE_NAME": settings.get("SITE_NAME", "Kairozen Store"),
            "SITE_TAGLINE": settings.get("SITE_TAGLINE", ""),
            "TELEGRAM": settings.get("TELEGRAM", ""),
            "CURRENCY": settings.get("CURRENCY", "USD"),
            "LOGO_VERSION": settings.get("LOGO_VERSION", 0),
            "GOOGLE_CLIENT_ID": (settings.get("GOOGLE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID") or "").strip(),
            "REQUIRE_LOGIN": bool(settings.get("REQUIRE_LOGIN")),
        },
        "categories": data.get("categories") or [],
        "products": products,
    })


@bp.post("/order")
def create_order():
    if not security.rate_limit("order_create", limit=15, window_sec=60):
        return jsonify({"ok": False, "error": "Too many requests"}), 429
    body = request.get_json(force=True, silent=True) or {}
    try:
        pid = int(body.get("product_id"))
    except Exception:
        return jsonify({"ok": False, "error": "product_id invalid"}), 400
    contact = body.get("telegram") or body.get("contact") or ""
    note = body.get("note") or ""
    user = current_user()
    if not user and (db.read().get("settings") or {}).get("REQUIRE_LOGIN"):
        return jsonify({"ok": False, "error": "សូមចូលគណនី Google ជាមុនសិន", "login_required": True}), 401
    result = order_service.create_order(pid, contact=contact[:120], note=note[:300], user=user)
    status = result.pop("status", 200)
    return jsonify(result), status


@bp.post("/order/check-payment")
def check_payment():
    if not security.rate_limit("order_check", limit=30, window_sec=60):
        return jsonify({"ok": False, "error": "Too many requests"}), 429
    body = request.get_json(force=True, silent=True) or {}
    oid = (body.get("order_id") or "").strip()
    result = order_service.check_and_fulfill(oid)
    status = result.pop("status", 200)
    return jsonify(result), status


@bp.post("/order/confirm-paid")
def confirm_paid():
    body = request.get_json(force=True, silent=True) or {}
    oid = (body.get("order_id") or "").strip()
    result = order_service.mark_waiting_or_fulfill(oid)
    status = result.pop("status", 200)
    return jsonify(result), status


@bp.get("/order/<oid>")
def get_order(oid: str):
    if not security.rate_limit("order_get", limit=30, window_sec=60):
        return jsonify({"ok": False, "error": "Too many requests"}), 429
    data = db.read()
    order = next(
        (o for o in data.get("orders", []) if o.get("id") == oid.upper() or o.get("id") == oid),
        None,
    )
    if not order:
        return jsonify({"ok": False, "error": "Not found"}), 404
    out = {
        "id": order["id"],
        "product_name": order.get("product_name"),
        "price": order.get("price"),
        "status": order.get("status"),
        "created_at": order.get("created_at"),
    }
    if order.get("status") == "paid":
        out["delivery"] = order.get("delivery")
    return jsonify({"ok": True, "order": out})

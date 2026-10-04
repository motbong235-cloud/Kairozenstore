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
            "REQUIRE_LOGIN": True,  # always require Google login to buy
            "BANNER_TITLE": settings.get("BANNER_TITLE") or "NEW CUSTOMERS!",
            "BANNER_SUBTITLE": settings.get("BANNER_SUBTITLE") or "Premium accounts · Instant delivery · Trusted",
            "BANNER_OFF": settings.get("BANNER_OFF") or "10%",
            "BANNER_IMAGE": settings.get("BANNER_IMAGE") or "",
            "MARQUEE_TEXT": settings.get("MARQUEE_TEXT") or "",

        },
        "categories": data.get("categories") or [],
        "products": products,
    })




@bp.get("/captcha")
def captcha():
    if not security.rate_limit("captcha", limit=30, window_sec=60):
        return jsonify({"ok": False, "error": "Too many requests"}), 429
    data = security.issue_captcha()
    return jsonify({"ok": True, **data})

@bp.post("/order")
def create_order():
    if not security.rate_limit("order", limit=10, window_sec=60):
        return jsonify({"ok": False, "error": "Too many orders, try later"}), 429

    if not security.rate_limit("order_create", limit=8, window_sec=60):
        return jsonify({"ok": False, "error": "Too many requests"}), 429
    body = request.get_json(force=True, silent=True) or {}
    try:
        pid = int(body.get("product_id"))
    except Exception:
        return jsonify({"ok": False, "error": "product_id invalid"}), 400
    note = body.get("note") or ""
    pay_with_balance = bool(body.get("pay_with_balance"))
    user = current_user()
    # Always require Google login before purchase
    if not user:
        return jsonify({
            "ok": False,
            "error": "សូមចូលគណនី Google ជាមុនសិន ទើបទិញបាន",
            "login_required": True,
        }), 401
    result = order_service.create_order(
        pid, contact="", note=note[:300], user=user, pay_with_balance=pay_with_balance
    )
    status = result.pop("status", 200)
    return jsonify(result), status


@bp.post("/order/check-payment")
def check_payment():
    """Poll KHPAY only — never marks paid without provider confirmation."""
    if not security.rate_limit("order_check", limit=12, window_sec=60):
        return jsonify({"ok": False, "error": "Too many checks · wait"}), 429
    if not security.rate_limit("order_check_burst", limit=3, window_sec=5):
        return jsonify({"ok": False, "error": "Slow down"}), 429
    body = request.get_json(force=True, silent=True) or {}
    oid = (body.get("order_id") or "").strip()
    if not oid or len(oid) > 64:
        return jsonify({"ok": False, "error": "Invalid order"}), 400
    user = current_user()
    data = db.read()
    order = next((o for o in data.get("orders", []) if o.get("id") == oid), None)
    if not order:
        return jsonify({"ok": False, "error": "Order not found"}), 404
    # Owner-only (or admin session)
    from flask import session
    is_admin = bool(session.get("admin"))
    if not is_admin:
        if not user or order.get("user_id") != user.get("id"):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
    result = order_service.check_and_fulfill(oid)
    status = result.pop("status", 200)
    return jsonify(result), status


@bp.post("/order/confirm-paid")
def confirm_paid():
    """DISABLED — was payment-bypass risk. Use KHPAY webhook / check-payment only."""
    return jsonify({
        "ok": False,
        "error": "Endpoint disabled · payment confirmed only via KHPAY",
    }), 410


@bp.post("/wallet/topup")
def wallet_topup():
    if not security.rate_limit("wallet_topup", limit=6, window_sec=60):
        return jsonify({"ok": False, "error": "Too many requests"}), 429
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "Login required", "login_required": True}), 401
    body = request.get_json(force=True, silent=True) or {}
    try:
        amount = float(body.get("amount") or 0)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid amount"}), 400
    result = order_service.create_topup(amount, user)
    status = result.pop("status", 200)
    return jsonify(result), status


@bp.get("/order/<oid>")
def get_order(oid: str):
    if not security.rate_limit("order_get", limit=30, window_sec=60):
        return jsonify({"ok": False, "error": "Too many requests"}), 429
    from flask import session
    user = current_user()
    is_admin = bool(session.get("admin"))
    data = db.read()
    order = next(
        (o for o in data.get("orders", []) if o.get("id") == oid.upper() or o.get("id") == oid),
        None,
    )
    if not order:
        return jsonify({"ok": False, "error": "Not found"}), 404
    if not is_admin:
        if not user or order.get("user_id") != user.get("id"):
            return jsonify({"ok": False, "error": "Forbidden"}), 403
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


@bp.post("/webhook/khpay")
def webhook_khpay():
    """KHPAY payment webhook — confirm order when paid. Signature required."""
    from flask import request
    from app.services import khpay as khpay_svc
    from app.services import order_service

    if not security.rate_limit("webhook_khpay", limit=60, window_sec=60):
        return jsonify({"ok": False, "error": "rate"}), 429

    raw = request.get_data() or b""
    if len(raw) > 50_000:
        return jsonify({"ok": False, "error": "payload too large"}), 413

    sig = request.headers.get("X-KHPAY-Signature") or request.headers.get("x-khpay-signature") or ""
    settings = (db.read().get("settings") or {})
    secret = (settings.get("KHPAY_WEBHOOK_SECRET") or "").strip()
    # Require webhook secret — unsigned webhooks rejected (anti payment-bypass)
    if not secret:
        return jsonify({"ok": False, "error": "Webhook secret not configured"}), 503
    if not khpay_svc.verify_webhook_signature(raw, sig, secret):
        return jsonify({"ok": False, "error": "Invalid signature"}), 401

    body = request.get_json(force=True, silent=True) or {}
    event = (body.get("event") or "").lower()
    status = (body.get("status") or "").lower()
    txn = body.get("transaction_id") or ""
    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    order_id = meta.get("order_id") or body.get("order_id") or ""

    paid = event in ("payment.paid", "paid") or status in ("paid", "completed", "approved")
    if not paid:
        return jsonify({"ok": True, "ignored": True})

    data = db.read()
    order = None
    if order_id:
        order = next((o for o in data.get("orders", []) if o.get("id") == order_id), None)
    if not order and txn:
        order = next(
            (o for o in data.get("orders", []) if o.get("khpay_transaction_id") == txn),
            None,
        )
    if not order:
        return jsonify({"ok": False, "error": "Order not found"}), 404
    if order.get("status") == "paid":
        return jsonify({"ok": True, "already": True})

    order_service.fulfill(data, order)
    db.write(data)
    return jsonify({"ok": True, "order_id": order.get("id")})


"""Admin API — protected by session."""
from __future__ import annotations

import os
import secrets
from functools import wraps

from flask import Blueprint, Response, jsonify, request, session
from werkzeug.utils import secure_filename

from app import database as db
from app import security
from app.services import order_service

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        # Soft IP check — force re-login if IP changed mid-session
        bound = session.get("admin_ip")
        if bound and bound != security.client_ip():
            session.clear()
            return jsonify({"ok": False, "error": "Session expired"}), 401
        return fn(*args, **kwargs)
    return wrapper


@bp.post("/login")
def login():
    if security.is_locked("admin_login"):
        return jsonify({"ok": False, "error": "Too many attempts · try later"}), 429
    if not security.rate_limit("admin_login", limit=8, window_sec=60):
        security.lockout("admin_login", seconds=300)
        return jsonify({"ok": False, "error": "Too many attempts · locked 5 min"}), 429

    body = request.get_json(force=True, silent=True) or {}
    pw = body.get("password") or ""
    if len(pw) > 128:
        return jsonify({"ok": False, "error": "Invalid"}), 400

    data = db.read()
    settings = data.setdefault("settings", {})
    expected = (
        settings.get("ADMIN_PASSWORD")
        or os.environ.get("ADMIN_PASSWORD", "admin123")
    )
    if not security.verify_password(pw, expected):
        # progressive soft lock after failures tracked by rate_limit
        return jsonify({"ok": False, "error": "Wrong password"}), 401

    # Migrate plain password → hashed
    if not security.is_hashed(expected):
        settings["ADMIN_PASSWORD"] = security.hash_password(pw)
        db.write(data)

    security.clear_lockout("admin_login")
    session.clear()
    session["admin"] = True
    session["admin_ip"] = security.client_ip()
    session.permanent = True
    return jsonify({"ok": True})


@bp.post("/logout")
def logout():
    session.pop("admin", None)
    return jsonify({"ok": True})


@bp.get("/me")
def me():
    return jsonify({"ok": True, "admin": bool(session.get("admin"))})


@bp.get("/data")
@admin_required
def data():
    d = db.read()
    for p in d.get("products") or []:
        order_service.sync_stock(d, p["id"])
    return jsonify({
        "ok": True,
        "settings": {k: v for k, v in (d.get("settings") or {}).items() if not k.startswith("_") and k != "ADMIN_PASSWORD"},
        "storage": db.storage_info(),
        "users": sorted(
            [
                {
                    "id": u.get("id"),
                    "name": u.get("name"),
                    "email": u.get("email"),
                    "picture": u.get("picture"),
                    "created_at": u.get("created_at"),
                    "last_login": u.get("last_login"),
                    "last_ip": u.get("last_ip") or "—",
                    "last_ip_at": u.get("last_ip_at"),
                    "balance": round(float(u.get("balance") or 0), 2),
                    "ip_history": list(u.get("ip_history") or [])[-5:],
                }
                for u in (d.get("users") or {}).values()
            ],
            key=lambda x: x.get("last_login") or "",
            reverse=True,
        )[:200],
        "categories": d.get("categories") or [],
        "products": d.get("products") or [],
        "orders": (d.get("orders") or [])[:100],
        "stock_files": {
            k: len(v) if isinstance(v, list) else 0
            for k, v in (d.get("stock_files") or {}).items()
        },
        "stats": {
            "products": len(d.get("products") or []),
            "users": len(d.get("users") or {}),
            "orders": len(d.get("orders") or []),
            "paid": sum(1 for o in d.get("orders") or [] if o.get("status") == "paid"),
            "pending": sum(
                1 for o in d.get("orders") or []
                if o.get("status") in ("pending_payment", "waiting_confirm")
            ),
            "revenue": sum(
                float(o.get("price") or 0)
                for o in d.get("orders") or []
                if o.get("status") == "paid"
            ),
        },
    })


@bp.route("/product", methods=["POST", "PUT", "DELETE"])
@admin_required
def product():
    d = db.read()
    body = request.get_json(force=True, silent=True) or {}
    if request.method == "DELETE":
        pid = int(body.get("id"))
        d["products"] = [p for p in d.get("products", []) if p.get("id") != pid]
        db.write(d)
        return jsonify({"ok": True})
    if request.method == "POST":
        nid = int(d.get("next_product") or 1)
        d["next_product"] = nid + 1
        p = {
            "id": nid,
            "cat": body.get("cat") or "other",
            "name": (body.get("name") or "Product").strip(),
            "desc": (body.get("desc") or "").strip(),
            "price": float(body.get("price") or 0),
            "old_price": float(body["old_price"]) if body.get("old_price") not in (None, "") else None,
            "stock": 0,
            "badge": (body.get("badge") or "").strip(),
            "image": (body.get("image") or "").strip(),
            "duration": (body.get("duration") or "").strip(),
            "active": bool(body.get("active", True)),
        }
        d.setdefault("products", []).append(p)
        db.write(d)
        return jsonify({"ok": True, "product": p})
    pid = int(body.get("id"))
    for p in d.get("products", []):
        if p.get("id") == pid:
            for k in ("cat", "name", "desc", "badge", "image", "duration"):
                if k in body:
                    p[k] = body[k]
            if "price" in body:
                p["price"] = float(body["price"])
            if "old_price" in body:
                p["old_price"] = float(body["old_price"]) if body["old_price"] not in (None, "") else None
            if "active" in body:
                p["active"] = bool(body["active"])
            db.write(d)
            return jsonify({"ok": True, "product": p})
    return jsonify({"ok": False, "error": "Not found"}), 404


@bp.route("/stock", methods=["GET", "POST", "DELETE"])
@admin_required
def stock():
    d = db.read()
    stock_map = d.setdefault("stock_files", {})
    if request.method == "GET":
        pid = str(request.args.get("product_id") or "")
        items = stock_map.get(pid) or []
        listed = []
        for i, raw in enumerate(items):
            val = raw.get("value") if isinstance(raw, dict) else str(raw)
            listed.append({"index": i, "value": (val or "")[:120]})
        return jsonify({"ok": True, "items": listed, "count": len(items)})
    if request.method == "DELETE":
        body = request.get_json(force=True, silent=True) or {}
        pid = str(body.get("product_id") or "")
        idx = int(body.get("index", -1))
        cur = stock_map.get(pid) or []
        if 0 <= idx < len(cur):
            cur.pop(idx)
            stock_map[pid] = cur
            order_service.sync_stock(d, int(pid))
            db.write(d)
        return jsonify({"ok": True, "count": len(stock_map.get(pid) or [])})
    body = request.get_json(force=True, silent=True) or {}
    pid = str(body.get("product_id") or "")
    lines = body.get("lines") or body.get("text") or ""
    if isinstance(lines, str):
        lines = [x.strip() for x in lines.replace("\r", "").split("\n") if x.strip()]
    cur = stock_map.get(pid) or []
    for line in lines:
        cur.append({"type": "text", "value": line})
    stock_map[pid] = cur
    order_service.sync_stock(d, int(pid))
    db.write(d)
    return jsonify({"ok": True, "count": len(cur)})


@bp.post("/order/confirm")
@admin_required
def confirm_order():
    body = request.get_json(force=True, silent=True) or {}
    oid = (body.get("order_id") or "").strip()
    d = db.read()
    order = next((o for o in d.get("orders", []) if o.get("id") == oid), None)
    if not order:
        return jsonify({"ok": False, "error": "Not found"}), 404
    if order.get("status") != "paid":
        order_service.fulfill(d, order)
        db.write(d)
    return jsonify({"ok": True, "order": order, "message": "Confirmed & delivered"})


@bp.put("/settings")
@admin_required
def settings():
    body = request.get_json(force=True, silent=True) or {}
    d = db.read()
    s = d.setdefault("settings", {})
    keys = [
        "SITE_NAME", "SITE_TAGLINE", "ADMIN_PASSWORD", "SHOP_NAME", "TELEGRAM",
        "CURRENCY", "PAYMENT_NOTE", "PAYMENT_QR", "BAKONG_ID",
        "KHMER_SECRET_KEY", "KHMER_PROFILE_KEY", "KHMER_MACHINE_ID", "KHMER_MERCHANT_NAME",
        "SITE_DESCRIPTION", "GOOGLE_CLIENT_ID", "REQUIRE_LOGIN",
        "BANNER_TITLE", "BANNER_SUBTITLE", "BANNER_OFF", "BANNER_IMAGE", "MARQUEE_TEXT",
        "KHPAY_API_KEY", "KHPAY_MERCHANT_ID", "KHPAY_WEBHOOK_SECRET", "KHPAY_CALLBACK_URL", "SITE_URL",
    ]
    for k in keys:
        if k in body:
            if k == "ADMIN_PASSWORD":
                if not body[k]:
                    continue
                if len(str(body[k])) < 6:
                    return jsonify({"ok": False, "error": "Password min 6 chars"}), 400
                s[k] = security.hash_password(str(body[k]))
                continue
            if k == "REQUIRE_LOGIN":
                s[k] = bool(body[k])
            elif k == "GOOGLE_CLIENT_ID":
                s[k] = str(body[k]).strip()
            else:
                s[k] = body[k]
    if s.get("KHMER_PROFILE_KEY") and not s.get("KHMER_SECRET_KEY"):
        s["KHMER_SECRET_KEY"] = s["KHMER_PROFILE_KEY"]
    db.write(d)
    safe = {k: v for k, v in s.items() if not k.startswith("_") and k != "ADMIN_PASSWORD"}
    return jsonify({"ok": True, "settings": safe})


# ───────────── uploads (stored in DB/disk backend, survive deploys) ─────────────
MAX_IMG = 1_500_000  # 1.5 MB


def _sniff_image(blob: bytes) -> tuple[str, str] | None:
    """Return (mime, ext) from magic bytes. SVG is rejected on purpose (script risk)."""
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp", "webp"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif"
    if blob[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon", "ico"
    return None


def _store_upload(prefix: str):
    f = request.files.get("file")
    if not f:
        return None, (jsonify({"ok": False, "error": "No file"}), 400)
    blob = f.read(MAX_IMG + 1)
    if len(blob) > MAX_IMG:
        return None, (jsonify({"ok": False, "error": "Image too large (max 1.5 MB)"}), 400)
    kind = _sniff_image(blob)
    if not kind:
        return None, (jsonify({"ok": False, "error": "Use PNG, JPG, WEBP or GIF"}), 400)
    mime, ext = kind
    name = f"{prefix}_{secrets.token_hex(6)}.{ext}"
    db.media_put(name, mime, blob)
    return name, None


@bp.post("/upload-image")
@admin_required
def upload_image():
    name, err = _store_upload("img")
    if err:
        return err
    return jsonify({"ok": True, "url": f"/api/media/{name}"})


@bp.post("/logo")
@admin_required
def upload_logo():
    name, err = _store_upload("logo")
    if err:
        return err
    d = db.read()
    s = d.setdefault("settings", {})
    s["SITE_LOGO"] = name
    s["LOGO_VERSION"] = int(s.get("LOGO_VERSION") or 0) + 1
    db.write(d)
    return jsonify({"ok": True, "version": s["LOGO_VERSION"]})


@bp.delete("/logo")
@admin_required
def reset_logo():
    d = db.read()
    s = d.setdefault("settings", {})
    s["SITE_LOGO"] = ""
    s["LOGO_VERSION"] = int(s.get("LOGO_VERSION") or 0) + 1
    db.write(d)
    return jsonify({"ok": True, "version": s["LOGO_VERSION"]})


# ───────────── backup / restore ─────────────
@bp.get("/backup")
@admin_required
def backup():
    import json
    from datetime import datetime, timezone

    payload = json.dumps(db.export_all(), ensure_ascii=False, indent=2)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=kairozen-backup-{stamp}.json"},
    )


@bp.post("/restore")
@admin_required
def restore():
    body = request.get_json(force=True, silent=True)
    try:
        db.import_all(body)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True})

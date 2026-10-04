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
    ok_c, err_c = security.verify_captcha(body.get("captcha_token"), body.get("captcha_answer"), body.get("website"))
    if not ok_c:
        return jsonify({"ok": False, "error": err_c, "captcha_required": True}), 400
    pw = body.get("password") or ""
    if len(pw) > 128:
        return jsonify({"ok": False, "error": "Invalid"}), 400

    data = db.read()
    settings = data.setdefault("settings", {})
    expected = (
        settings.get("ADMIN_PASSWORD")
        or os.environ.get("ADMIN_PASSWORD")
        or ""
    )
    if not expected:
        # Production-safe: no default password
        if os.environ.get("FLASK_ENV") == "production" or os.environ.get("RENDER"):
            return jsonify({"ok": False, "error": "Admin password not configured (set ADMIN_PASSWORD)"}), 503
        expected = "admin123"  # local dev only
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



def _parse_ts(s: str):
    from datetime import datetime, timezone
    if not s:
        return None
    try:
        ts = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception:
        return None


def _online_ips(d: dict) -> list:
    from datetime import datetime, timezone, timedelta
    cut = datetime.now(timezone.utc) - timedelta(seconds=180)
    out = []
    for ip, v in (d.get("presence") or {}).items():
        ts = _parse_ts((v or {}).get("at") or "")
        if ts and ts >= cut:
            out.append({
                "ip": ip,
                "at": (v or {}).get("at"),
                "user_id": (v or {}).get("user_id") or "",
                "email": (v or {}).get("email") or "",
            })
    out.sort(key=lambda x: x.get("at") or "", reverse=True)
    return out


def _users_with_online(d: dict) -> list:
    from datetime import datetime, timezone, timedelta
    cut = datetime.now(timezone.utc) - timedelta(seconds=180)
    online_ips = set()
    for ip, v in (d.get("presence") or {}).items():
        ts = _parse_ts((v or {}).get("at") or "")
        if ts and ts >= cut:
            online_ips.add(ip)
    rows = []
    for u in (d.get("users") or {}).values():
        last_seen = u.get("last_seen") or u.get("last_login") or ""
        ts = _parse_ts(last_seen)
        ip = (u.get("last_ip") or "").strip()
        online = bool((ts and ts >= cut) or (ip and ip in online_ips))
        rows.append({
            "id": u.get("id"),
            "name": u.get("name"),
            "email": u.get("email"),
            "picture": u.get("picture"),
            "created_at": u.get("created_at"),
            "last_login": u.get("last_login"),
            "last_seen": last_seen,
            "last_ip": ip or "—",
            "last_ip_at": u.get("last_ip_at"),
            "balance": round(float(u.get("balance") or 0), 2),
            "ip_history": list(u.get("ip_history") or [])[-5:],
            "online": online,
        })
    online = [x for x in rows if x.get("online")]
    offline = [x for x in rows if not x.get("online")]
    online.sort(key=lambda x: x.get("last_seen") or "", reverse=True)
    offline.sort(key=lambda x: x.get("last_seen") or x.get("last_login") or "", reverse=True)
    return online + offline


@bp.get("/data")
@admin_required
def data():
    d = db.read()
    for p in d.get("products") or []:
        order_service.sync_stock(d, p["id"])
    low_stock_n = 0
    for p in d.get("products") or []:
        if not p.get("active", True):
            continue
        n = len((d.get("stock_files") or {}).get(str(p.get("id")), []) or [])
        if n == 0:
            n = int(p.get("stock") or 0)
        if n <= 3:
            low_stock_n += 1
    return jsonify({
        "ok": True,
        "settings": {k: v for k, v in (d.get("settings") or {}).items() if not k.startswith("_") and k != "ADMIN_PASSWORD"},
        "storage": db.storage_info(),
        "users": _users_with_online(d)[:200],
        "online_ips": _online_ips(d),
        "spam": security.get_spam_report(100),
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
            "low_stock": low_stock_n,
            "online_now": len(_online_ips(d)),
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




@bp.post("/user/credit")
@admin_required
def user_credit():
    """Admin add/subtract wallet balance for a user (by email or id)."""
    body = request.get_json(force=True, silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    uid = (body.get("user_id") or "").strip()
    try:
        amount = float(body.get("amount") or 0)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid amount"}), 400
    note = (body.get("note") or "").strip()[:200]
    if amount == 0:
        return jsonify({"ok": False, "error": "Amount cannot be 0"}), 400
    if abs(amount) > 10000:
        return jsonify({"ok": False, "error": "Amount too large"}), 400
    d = db.read()
    users = d.setdefault("users", {})
    user = None
    if uid and uid in users:
        user = users[uid]
    elif email:
        for u in users.values():
            if (u.get("email") or "").lower() == email:
                user = u
                break
    if not user:
        return jsonify({"ok": False, "error": "User not found"}), 404
    old = float(user.get("balance") or 0)
    new = round(old + amount, 2)
    if new < 0:
        return jsonify({"ok": False, "error": f"Balance would be negative (now ${old:.2f})"}), 400
    user["balance"] = new
    # audit log
    logs = d.setdefault("admin_logs", [])
    logs.append({
        "at": order_service.utc_now(),
        "action": "credit",
        "user_id": user.get("id"),
        "email": user.get("email"),
        "amount": amount,
        "balance_before": old,
        "balance_after": new,
        "note": note,
    })
    d["admin_logs"] = logs[-200:]
    db.write(d)
    return jsonify({
        "ok": True,
        "user_id": user.get("id"),
        "email": user.get("email"),
        "balance": new,
        "amount": amount,
    })


@bp.route("/category", methods=["POST", "DELETE"])
@admin_required
def category():
    body = request.get_json(force=True, silent=True) or {}
    d = db.read()
    cats = d.setdefault("categories", [])
    if request.method == "DELETE":
        slug = (body.get("slug") or "").strip()
        d["categories"] = [c for c in cats if c.get("slug") != slug]
        db.write(d)
        return jsonify({"ok": True, "categories": d["categories"]})
    name = (body.get("name") or "").strip()
    slug = (body.get("slug") or "").strip().lower().replace(" ", "-")
    if not name:
        return jsonify({"ok": False, "error": "Name required"}), 400
    if not slug:
        slug = "".join(c if c.isalnum() or c == "-" else "-" for c in name.lower())
    existing = next((c for c in cats if c.get("slug") == slug), None)
    if existing:
        existing["name"] = name
    else:
        cats.append({"slug": slug, "name": name})
    db.write(d)
    return jsonify({"ok": True, "categories": cats})


@bp.post("/order/deliver")
@admin_required
def order_deliver():
    """Manually set delivery text and mark paid (for custom fulfillment)."""
    body = request.get_json(force=True, silent=True) or {}
    oid = (body.get("order_id") or "").strip()
    delivery = (body.get("delivery") or "").strip()
    d = db.read()
    order = next((o for o in d.get("orders", []) if o.get("id") == oid), None)
    if not order:
        return jsonify({"ok": False, "error": "Not found"}), 404
    if delivery:
        order["delivery"] = delivery
    if order.get("status") != "paid":
        order_service.fulfill(d, order)
        if delivery:
            order["delivery"] = delivery
    else:
        if delivery:
            order["delivery"] = delivery
    db.write(d)
    return jsonify({"ok": True, "order": order})



@bp.post("/order/refund")
@admin_required
def order_refund():
    """Refund paid order to user wallet (or mark refunded)."""
    body = request.get_json(force=True, silent=True) or {}
    oid = (body.get("order_id") or "").strip()
    d = db.read()
    order = next((o for o in d.get("orders", []) if o.get("id") == oid), None)
    if not order:
        return jsonify({"ok": False, "error": "Not found"}), 404
    if order.get("status") == "refunded":
        return jsonify({"ok": False, "error": "Already refunded"}), 400
    if order.get("status") != "paid":
        return jsonify({"ok": False, "error": "Only paid orders can refund"}), 400
    amount = float(order.get("price") or 0)
    uid = order.get("user_id")
    new_bal = None
    if uid and order.get("type") != "topup":
        # credit back product purchase
        users = d.setdefault("users", {})
        u = users.get(uid)
        if u is not None:
            new_bal = round(float(u.get("balance") or 0) + amount, 2)
            u["balance"] = new_bal
    elif uid and order.get("type") == "topup":
        # reverse topup: debit
        users = d.setdefault("users", {})
        u = users.get(uid)
        if u is not None:
            bal = float(u.get("balance") or 0)
            new_bal = round(max(0, bal - amount), 2)
            u["balance"] = new_bal
    order["status"] = "refunded"
    order["refunded_at"] = order_service.utc_now()
    order["refund_note"] = (body.get("note") or "").strip()[:200]
    logs = d.setdefault("admin_logs", [])
    logs.append({
        "at": order_service.utc_now(),
        "action": "refund",
        "order_id": oid,
        "amount": amount,
        "user_id": uid,
        "note": order.get("refund_note"),
    })
    d["admin_logs"] = logs[-200:]
    db.write(d)
    try:
        order_service.notify_telegram(
            f"↩️ REFUND\nOrder {oid}\n${amount:.2f}\nUser {order.get('contact') or uid or '-'}"
        )
    except Exception:
        pass
    return jsonify({"ok": True, "order": order, "balance": new_bal})


@bp.get("/tenants")
@admin_required
def tenants_list():
    from app.services import tenants as ten
    return jsonify({"ok": True, "tenants": ten.list_tenants(), "price": ten.plan_price(), "base_domain": ten.base_domain()})


@bp.post("/tenants/activate")
@admin_required
def tenants_activate():
    from app.services import tenants as ten
    body = request.get_json(force=True, silent=True) or {}
    slug = (body.get("slug") or "").strip().lower()
    t = ten.activate_tenant(slug)
    if not t:
        return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True, "tenant": t})


@bp.put("/settings")
@admin_required
def settings():

    body = request.get_json(force=True, silent=True) or {}
    d = db.read()
    s = d.setdefault("settings", {})
    keys = [
        "SITE_NAME", "SITE_TAGLINE", "ADMIN_PASSWORD", "SHOP_NAME", "TELEGRAM", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "BANNER_IMAGES", "FOOTER_ABOUT", "FOOTER_EMAIL", "FOOTER_ADDRESS", "SOCIAL_FACEBOOK", "SOCIAL_TELEGRAM", "SOCIAL_TIKTOK", "SOCIAL_YOUTUBE", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
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
MAX_RAW = int(os.environ.get("MAX_UPLOAD_MB", "50")) * 1_000_000   # raw upload limit (default 50 MB)
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}

MAX_IMG = 1_500_000    # stored size target after auto-compress
MAX_SIDE = 1600        # longest side in px


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


def _shrink(blob: bytes, mime: str, ext: str) -> tuple[bytes, str, str]:
    """Auto resize/compress big photos so phone images (3-10 MB) upload fine."""
    if ext in ("gif", "ico") or (len(blob) <= MAX_IMG and ext != "jpg"):
        return blob, mime, ext
    try:
        import io
        from PIL import Image, ImageOps

        Image.MAX_IMAGE_PIXELS = 400_000_000  # allow very large camera/DSLR photos
        im = Image.open(io.BytesIO(blob))
        if ext == "jpg":
            im.draft("RGB", (MAX_SIDE * 2, MAX_SIDE * 2))  # decode small → low RAM
        im = ImageOps.exif_transpose(im)  # fix rotated phone photos
        if max(im.size) > MAX_SIDE or len(blob) > MAX_IMG:
            im.thumbnail((MAX_SIDE, MAX_SIDE))
        has_alpha = im.mode in ("RGBA", "LA", "P") and (
            im.mode != "P" or "transparency" in im.info
        )
        out = io.BytesIO()
        if has_alpha:
            im.convert("RGBA").save(out, "WEBP", quality=85, method=4)
            return out.getvalue(), "image/webp", "webp"
        im = im.convert("RGB")
        q = 85
        while True:
            out = io.BytesIO()
            im.save(out, "JPEG", quality=q, optimize=True)
            if out.tell() <= MAX_IMG or q <= 50:
                break
            q -= 10
        return out.getvalue(), "image/jpeg", "jpg"
    except Exception:
        return blob, mime, ext


def _store_upload(prefix: str):
    f = request.files.get("file") or request.files.get("image")
    if f is None:
        return None, "No file"
    filename = secure_filename(f.filename or "") or "upload.bin"
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if ext not in ALLOWED_IMAGE_EXT:
        return None, "Only jpg/png/webp/gif allowed"
    mime = (f.mimetype or "").lower()
    if mime and mime not in ALLOWED_IMAGE_MIME:
        return None, "Invalid image type"
    blob = f.read(MAX_RAW + 1)
    if len(blob) > MAX_RAW:
        return None, (jsonify({"ok": False, "error": "Image too large (max {} MB)".format(MAX_RAW // 1_000_000)}), 400)
    kind = _sniff_image(blob)
    if not kind:
        return None, (jsonify({"ok": False, "error": "Use PNG, JPG, WEBP or GIF"}), 400)
    mime, ext = kind
    blob, mime, ext = _shrink(blob, mime, ext)
    if len(blob) > 4_000_000:
        return None, (jsonify({"ok": False, "error": "Image still too large"}), 400)
    name = f"{prefix}_{secrets.token_hex(6)}.{ext}"
    try:
        db.media_put(name, mime, blob)
    except Exception as e:  # DB/disk problem → show a real message instead of "Fail"
        return None, (jsonify({"ok": False, "error": f"Storage error: {type(e).__name__}"}), 500)
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
        headers={"Content-Disposition": f"attachment; filename=premiumkh-backup-{stamp}.json"},
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

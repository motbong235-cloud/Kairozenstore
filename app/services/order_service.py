"""Order + stock business logic."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from app import database as db
from app.services import khmer_system


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sync_stock(data: dict, product_id: int) -> None:
    items = (data.get("stock_files") or {}).get(str(product_id)) or []
    for p in data.get("products") or []:
        if p.get("id") == product_id:
            p["stock"] = len(items) if isinstance(items, list) else 0
            break


def pop_stock(data: dict, product_id: int) -> str | None:
    stock = data.setdefault("stock_files", {})
    key = str(product_id)
    items = stock.get(key) or []
    if not items:
        return None
    raw = items.pop(0)
    stock[key] = items
    if isinstance(raw, dict):
        return raw.get("value") or raw.get("text") or str(raw)
    return str(raw)


def fulfill(data: dict, order: dict) -> dict:
    delivery = pop_stock(data, order["product_id"])
    order["delivery"] = delivery
    order["status"] = "paid" if delivery else "waiting_confirm"
    order["paid_at"] = utc_now()
    sync_stock(data, order["product_id"])
    return order


def create_order(product_id: int, contact: str = "", note: str = "") -> dict[str, Any]:
    data = db.read()
    product = next(
        (p for p in data.get("products", []) if p.get("id") == product_id and p.get("active", True)),
        None,
    )
    if not product:
        return {"ok": False, "error": "Product not found", "status": 404}
    if int(product.get("stock") or 0) <= 0:
        return {"ok": False, "error": "Out of stock", "status": 400}

    contact = (contact or "").strip() or ("guest_" + secrets.token_hex(3))
    n = int(data.get("next_order") or 1001)
    oid = f"KZ{n}"
    data["next_order"] = n + 1
    price = float(product.get("price") or 0)

    order = {
        "id": oid,
        "product_id": product_id,
        "product_name": product.get("name"),
        "price": price,
        "contact": contact,
        "note": (note or "").strip(),
        "status": "pending_payment",
        "delivery": None,
        "created_at": utc_now(),
        "paid_at": None,
        "ks_verify_key": None,
        "ks_error": None,
        "payment_qr": None,
    }

    settings = data.get("settings") or {}
    pay = {
        "SHOP_NAME": settings.get("SHOP_NAME") or "Kairozen Store",
        "PAYMENT_QR": settings.get("PAYMENT_QR") or "",
        "PAYMENT_NOTE": settings.get("PAYMENT_NOTE") or "Scan KHQR to pay",
        "BAKONG_ID": settings.get("BAKONG_ID") or "",
        "KS_DYNAMIC": False,
    }
    ks_data = None

    secret = (settings.get("KHMER_SECRET_KEY") or settings.get("KHMER_PROFILE_KEY") or "").strip()
    if secret:
        vkey = khmer_system.make_verify_key()
        tg_id = str(abs(hash(contact + oid)) % 10_000_000_000)
        order["ks_verify_key"] = vkey
        order["ks_telegram_user_id"] = tg_id
        try:
            resp = khmer_system.generate(
                secret_key=secret,
                amount=price,
                verify_key=vkey,
                telegram_user_id=tg_id,
                bakong_account_id=(settings.get("BAKONG_ID") or None) or None,
                merchant_name=(settings.get("KHMER_MERCHANT_NAME") or settings.get("SHOP_NAME") or "Kairozen Store"),
                machine_id=(settings.get("KHMER_MACHINE_ID") or None) or None,
                profile_key=(settings.get("KHMER_PROFILE_KEY") or None) or None,
            )
            qr = resp.get("qr_image_url") or resp.get("qr") or ""
            if resp.get("success") or qr:
                order["payment_qr"] = qr
                pay["PAYMENT_QR"] = qr
                pay["KS_DYNAMIC"] = True
                ks_data = {"qr_image_url": qr, "verify_key": vkey}
            else:
                order["ks_error"] = resp.get("error") or resp.get("message") or str(resp)[:200]
        except Exception as e:
            order["ks_error"] = str(e)
    elif not pay["PAYMENT_QR"]:
        order["ks_error"] = "Admin: set Khmer System Profile Key or upload static QR"

    data.setdefault("orders", []).insert(0, order)
    db.write(data)
    return {
        "ok": True,
        "order": {
            "id": order["id"],
            "price": order["price"],
            "status": order["status"],
            "product_name": order["product_name"],
        },
        "payment": pay,
        "khmer_system": ks_data,
        "ks_error": order.get("ks_error"),
    }


def check_and_fulfill(order_id: str, force_check: bool = True) -> dict[str, Any]:
    data = db.read()
    order = next((o for o in data.get("orders", []) if o.get("id") == order_id), None)
    if not order:
        return {"ok": False, "error": "Order not found", "status": 404}
    if order.get("status") == "paid":
        return {"ok": True, "payment_status": "completed", "order": order, "message": "Delivered"}

    settings = data.get("settings") or {}
    secret = (settings.get("KHMER_SECRET_KEY") or settings.get("KHMER_PROFILE_KEY") or "").strip()
    vkey = order.get("ks_verify_key")
    tg_id = order.get("ks_telegram_user_id")
    payment_status = "pending"

    if force_check and secret and vkey and tg_id:
        resp = khmer_system.check(secret_key=secret, verify_key=vkey, telegram_user_id=str(tg_id))
        st = (resp.get("status") or resp.get("payment_status") or "").lower()
        if resp.get("success") or st in ("paid", "completed", "success", "approved"):
            payment_status = "completed"
            try:
                khmer_system.confirm(secret_key=secret, verify_key=vkey, telegram_user_id=str(tg_id))
            except Exception:
                pass
            fulfill(data, order)
            db.write(data)

    return {
        "ok": True,
        "payment_status": payment_status,
        "order": {
            "id": order["id"],
            "status": order["status"],
            "delivery": order.get("delivery") if order["status"] == "paid" else None,
            "product_name": order.get("product_name"),
            "price": order.get("price"),
        },
    }


def mark_waiting_or_fulfill(order_id: str) -> dict[str, Any]:
    result = check_and_fulfill(order_id, force_check=True)
    if not result.get("ok"):
        return result
    if result.get("payment_status") == "completed" or result.get("order", {}).get("status") == "paid":
        # reload full order
        data = db.read()
        order = next((o for o in data.get("orders", []) if o.get("id") == order_id), None)
        return {"ok": True, "order": order, "message": "Account delivered"}

    data = db.read()
    order = next((o for o in data.get("orders", []) if o.get("id") == order_id), None)
    if order and order.get("status") != "paid":
        order["status"] = "waiting_confirm"
        db.write(data)
    return {"ok": True, "order": order, "message": "Waiting admin confirm"}

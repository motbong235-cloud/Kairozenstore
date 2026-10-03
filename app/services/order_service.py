"""Order + stock + wallet business logic."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from app import database as db
from app.services import khpay

def notify_telegram(text: str) -> None:
    """Fire-and-forget admin Telegram message (optional settings)."""
    try:
        import json
        import urllib.request
        import urllib.parse
        s = (db.read().get("settings") or {})
        token = (s.get("TELEGRAM_BOT_TOKEN") or "").strip()
        chat = (s.get("TELEGRAM_CHAT_ID") or "").strip()
        if not token or not chat:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = json.dumps({
            "chat_id": chat,
            "text": text[:3500],
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass




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


def _credit_balance(data: dict, user_id: str | None, amount: float) -> float:
    if not user_id or amount <= 0:
        return 0.0
    users = data.setdefault("users", {})
    u = users.get(user_id)
    if u is None:
        return 0.0
    bal = round(float(u.get("balance") or 0) + amount, 2)
    u["balance"] = bal
    users[user_id] = u
    return bal


def _debit_balance(data: dict, user_id: str, amount: float) -> float | None:
    users = data.setdefault("users", {})
    u = users.get(user_id)
    if u is None:
        return None
    bal = float(u.get("balance") or 0)
    if bal + 1e-9 < amount:
        return None
    bal = round(bal - amount, 2)
    u["balance"] = bal
    users[user_id] = u
    return bal


def fulfill(data: dict, order: dict) -> dict:
    if order.get("type") == "topup":
        amount = float(order.get("price") or 0)
        new_bal = _credit_balance(data, order.get("user_id"), amount)
        order["delivery"] = f"Top-up +${amount:.2f}"
        order["status"] = "paid"
        order["paid_at"] = utc_now()
        order["balance_after"] = new_bal
        return order

    delivery = pop_stock(data, order["product_id"])
    order["delivery"] = delivery
    order["status"] = "paid" if delivery else "waiting_confirm"
    order["paid_at"] = utc_now()
    if order.get("product_id"):
        sync_stock(data, order["product_id"])
    # notify admin (best-effort)
    try:
        kind = "TOPUP" if order.get("type") == "topup" else "ORDER"
        msg = (
            f"✅ {kind} PAID\n"
            f"ID: {order.get('id')}\n"
            f"Product: {order.get('product_name') or '-'}\n"
            f"Amount: ${float(order.get('price') or 0):.2f}\n"
            f"User: {order.get('contact') or order.get('user_id') or '-'}\n"
            f"Status: {order.get('status')}\n"
            f"Delivery: {(order.get('delivery') or '-')[:200]}"
        )
        notify_telegram(msg)
        # low stock warning
        if order.get("product_id") and order.get("type") != "topup":
            stock = (data.get("stock_files") or {}).get(str(order["product_id"])) or []
            if len(stock) <= 3:
                notify_telegram(
                    f"⚠️ LOW STOCK\n{order.get('product_name')}\nเหลือ {len(stock)} บัญชี"
                    if False else
                    f"⚠️ LOW STOCK\n{order.get('product_name')}\nនៅសល់ {len(stock)} accounts"
                )
    except Exception:
        pass
    return order


def _make_khpay_qr(data: dict, order: dict, price: float, note: str = "") -> tuple[dict, dict | None]:
    """Generate KHPAY QR; store txn id on order."""
    settings = data.get("settings") or {}
    pay = {
        "SHOP_NAME": settings.get("SHOP_NAME") or "Kairozen Store",
        "PAYMENT_QR": settings.get("PAYMENT_QR") or "",
        "PAYMENT_NOTE": settings.get("PAYMENT_NOTE") or "Scan KHQR to pay",
        "BAKONG_ID": settings.get("BAKONG_ID") or "",
        "KS_DYNAMIC": False,
        "PROVIDER": "khpay",
    }
    ks_data = None
    api_key = (settings.get("KHPAY_API_KEY") or "").strip()
    if api_key:
        try:
            # Build public callback if site URL known
            callback = (settings.get("KHPAY_CALLBACK_URL") or "").strip() or None
            base = (settings.get("SITE_URL") or "").strip().rstrip("/")
            if not callback and base:
                callback = f"{base}/api/webhook/khpay"
            resp = khpay.generate(
                api_key=api_key,
                amount=price,
                note=note or order.get("id") or "Kairozen",
                merchant_id=(settings.get("KHPAY_MERCHANT_ID") or "").strip() or None,
                callback_url=callback,
                metadata={"order_id": order.get("id")},
            )
            if resp.get("success"):
                order["khpay_transaction_id"] = resp.get("transaction_id")
                qr = resp.get("qr_image") or ""
                order["payment_qr"] = qr
                order["khpay_payment_url"] = resp.get("payment_url") or ""
                order["khpay_qr_string"] = resp.get("qr_string") or ""
                order["abapay_deeplink"] = resp.get("abapay_deeplink") or ""
                pay["PAYMENT_QR"] = qr
                pay["PAYMENT_URL"] = resp.get("payment_url") or ""
                pay["QR_STRING"] = (resp.get("qr_string") or "").strip()
                pay["ABA_DEEPLINK"] = (resp.get("abapay_deeplink") or "").strip()
                pay["KS_DYNAMIC"] = True
                pay["PROVIDER"] = "khpay"
                # Always mark ABA open available when we have QR or deeplink or payment page
                pay["ABA_OPEN"] = bool(
                    pay["ABA_DEEPLINK"] or pay["QR_STRING"] or pay["PAYMENT_URL"] or qr
                )
                ks_data = {
                    "transaction_id": resp.get("transaction_id"),
                    "qr_image": qr,
                    "payment_url": resp.get("payment_url"),
                    "qr_string": resp.get("qr_string") or "",
                    "abapay_deeplink": resp.get("abapay_deeplink") or "",
                }
            else:
                order["ks_error"] = resp.get("error") or "KHPAY generate failed"
        except Exception as e:
            order["ks_error"] = str(e)
    elif not pay["PAYMENT_QR"]:
        order["ks_error"] = "Admin: set KHPAY API Key (khpay.site) or upload static QR"
    return pay, ks_data



def create_order(
    product_id: int,
    contact: str = "",
    note: str = "",
    user: dict | None = None,
    pay_with_balance: bool = False,
) -> dict[str, Any]:
    data = db.read()
    product = next(
        (p for p in data.get("products", []) if p.get("id") == product_id and p.get("active", True)),
        None,
    )
    if not product:
        return {"ok": False, "error": "Product not found", "status": 404}
    if int(product.get("stock") or 0) <= 0:
        return {"ok": False, "error": "Out of stock", "status": 400}

    # No username field — auto from login or guest
    contact = (user or {}).get("email") or ("guest_" + secrets.token_hex(3))
    n = int(data.get("next_order") or 1001)
    oid = f"KZ{n}-{secrets.token_hex(2).upper()}"
    data["next_order"] = n + 1
    price = float(product.get("price") or 0)

    order = {
        "id": oid,
        "type": "product",
        "product_id": product_id,
        "product_name": product.get("name"),
        "price": price,
        "contact": contact,
        "user_id": (user or {}).get("id"),
        "user_email": (user or {}).get("email"),
        "note": (note or "").strip(),
        "status": "pending_payment",
        "delivery": None,
        "created_at": utc_now(),
        "paid_at": None,
        "ks_verify_key": None,
        "ks_error": None,
        "payment_qr": None,
        "paid_with": None,
    }

    # Pay from wallet balance (instant)
    if pay_with_balance:
        if not user or not user.get("id"):
            return {"ok": False, "error": "Login required for balance payment", "login_required": True, "status": 401}
        # refresh balance from DB
        u = (data.get("users") or {}).get(user["id"]) or user
        bal = float(u.get("balance") or 0)
        if bal + 1e-9 < price:
            return {"ok": False, "error": f"Balance insufficient (${bal:.2f})", "status": 400}
        new_bal = _debit_balance(data, user["id"], price)
        if new_bal is None:
            return {"ok": False, "error": "Balance insufficient", "status": 400}
        order["paid_with"] = "balance"
        fulfill(data, order)
        data.setdefault("orders", []).insert(0, order)
        db.write(data)
        return {
            "ok": True,
            "paid_with": "balance",
            "balance": new_bal,
            "order": {
                "id": order["id"],
                "price": order["price"],
                "status": order["status"],
                "product_name": order["product_name"],
                "delivery": order.get("delivery"),
            },
            "payment": None,
            "ks_error": None,
        }

    pay, ks_data = _make_khpay_qr(data, order, price, contact)
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


def create_topup(amount: float, user: dict) -> dict[str, Any]:
    amount = round(float(amount), 2)
    if amount < 1:
        return {"ok": False, "error": "Minimum top-up $1", "status": 400}
    if amount > 500:
        return {"ok": False, "error": "Maximum top-up $500", "status": 400}
    if not user or not user.get("id"):
        return {"ok": False, "error": "Login required", "login_required": True, "status": 401}

    data = db.read()
    n = int(data.get("next_order") or 1001)
    oid = f"TP{n}-{secrets.token_hex(2).upper()}"
    data["next_order"] = n + 1
    contact = user.get("email") or user["id"]

    order = {
        "id": oid,
        "type": "topup",
        "product_id": 0,
        "product_name": f"Wallet Top-up ${amount:.2f}",
        "price": amount,
        "contact": contact,
        "user_id": user["id"],
        "user_email": user.get("email"),
        "note": "",
        "status": "pending_payment",
        "delivery": None,
        "created_at": utc_now(),
        "paid_at": None,
        "ks_verify_key": None,
        "ks_error": None,
        "payment_qr": None,
        "paid_with": "khqr",
    }
    pay, ks_data = _make_khpay_qr(data, order, amount, contact)
    data.setdefault("orders", []).insert(0, order)
    db.write(data)
    return {
        "ok": True,
        "order": {
            "id": order["id"],
            "price": order["price"],
            "status": order["status"],
            "product_name": order["product_name"],
            "type": "topup",
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
        bal = None
        if order.get("user_id"):
            u = (data.get("users") or {}).get(order["user_id"])
            if u:
                bal = float(u.get("balance") or 0)
        return {
            "ok": True,
            "payment_status": "completed",
            "order": order,
            "balance": bal,
            "message": "Delivered",
        }

    settings = data.get("settings") or {}
    api_key = (settings.get("KHPAY_API_KEY") or "").strip()
    txn_id = order.get("khpay_transaction_id") or order.get("ks_verify_key")
    payment_status = "pending"

    if force_check and api_key and txn_id:
        resp = khpay.check(api_key=api_key, transaction_id=str(txn_id))
        if resp.get("paid"):
            payment_status = "completed"
            fulfill(data, order)
            db.write(data)

    bal = None
    if order.get("user_id"):
        u = (db.read().get("users") or {}).get(order["user_id"])
        if u:
            bal = float(u.get("balance") or 0)

    return {
        "ok": True,
        "payment_status": payment_status,
        "balance": bal,
        "order": {
            "id": order["id"],
            "type": order.get("type") or "product",
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
        data = db.read()
        order = next((o for o in data.get("orders", []) if o.get("id") == order_id), None)
        bal = None
        if order and order.get("user_id"):
            u = (data.get("users") or {}).get(order["user_id"])
            if u:
                bal = float(u.get("balance") or 0)
        return {"ok": True, "order": order, "balance": bal, "message": "Account delivered"}

    data = db.read()
    order = next((o for o in data.get("orders", []) if o.get("id") == order_id), None)
    if order and order.get("status") != "paid":
        order["status"] = "waiting_confirm"
        db.write(data)
    return {"ok": True, "order": order, "message": "Waiting admin confirm"}


def orders_for_user(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    data = db.read()
    out = []
    for o in data.get("orders", []):
        if o.get("user_id") == user_id:
            out.append({
                "id": o["id"],
                "type": o.get("type") or "product",
                "product_name": o.get("product_name"),
                "price": o.get("price"),
                "status": o.get("status"),
                "created_at": o.get("created_at"),
                "paid_with": o.get("paid_with"),
                "delivery": o.get("delivery") if o.get("status") == "paid" else None,
            })
        if len(out) >= limit:
            break
    return out

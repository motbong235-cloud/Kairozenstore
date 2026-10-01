"""KHPAY (khpay.site) — same flow as KaiJakLike bot.

POST /api/v1/qr/generate  + Bearer ak_…
GET  /api/v1/qr/check/{transaction_id}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import urllib.error
import urllib.request
from typing import Any

BASE_URL = "https://khpay.site/api/v1"
TIMEOUT = 20


def _request(
    method: str,
    path: str,
    api_key: str,
    body: dict | None = None,
) -> dict[str, Any]:
    url = f"{BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "KairozenStore/1.0",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except Exception:
                return {"success": False, "error": raw[:300]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"success": False, "error": raw[:300] or str(e)}
        if "success" not in parsed:
            parsed["success"] = False
        parsed.setdefault("error", parsed.get("error") or str(e))
        parsed["http_status"] = e.code
        return parsed
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def _qr_string_to_data_uri(qr_string: str) -> str:
    """Fallback: build PNG data-URI from KHQR payload (like bot uses qrcode)."""
    if not qr_string:
        return ""
    try:
        import qrcode
        from PIL import Image

        img = qrcode.make(qr_string)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return ""


def _normalize_qr_image(qr_image: str, qr_string: str = "") -> str:
    """
    Bot logic:
      - data:image/... → use as-is (HTML <img src>)
      - http(s):// → use as-is
      - long base64 → prefix data:image/png;base64,
      - else build from qr_string
    """
    q = (qr_image or "").strip()
    if q.startswith("data:image"):
        return q
    if q.startswith("http://") or q.startswith("https://"):
        return q
    if len(q) > 200:
        try:
            base64.b64decode(q, validate=False)
            return f"data:image/png;base64,{q}"
        except Exception:
            pass
    if qr_string:
        return _qr_string_to_data_uri(qr_string)
    return q


def generate(
    api_key: str,
    amount: float,
    note: str = "",
    merchant_id: str | None = None,
    callback_url: str | None = None,
    success_url: str | None = None,
    cancel_url: str | None = None,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """Mirror bot `_khpay_create` + QR normalize for web <img>."""
    if not (api_key or "").strip():
        return {"success": False, "error": "KHPAY_API_KEY មិនបានកំណត់"}

    body: dict[str, Any] = {
        "amount": round(float(amount), 2),
        "currency": "USD",
        "note": (note or "")[:200],
    }
    mid = (merchant_id or "").strip()
    if mid:
        body["merchant_id"] = mid
    if callback_url:
        body["callback_url"] = callback_url
    if success_url:
        body["success_url"] = success_url
    if cancel_url:
        body["cancel_url"] = cancel_url
    if metadata:
        body["metadata"] = {str(k): v for k, v in list(metadata.items())[:10]}

    resp = _request("POST", "/qr/generate", api_key, body)

    # Bot: success + data dict, OR top-level transaction_id / qr_image
    data: dict = {}
    if resp.get("success") and isinstance(resp.get("data"), dict):
        data = resp["data"]
    elif resp.get("transaction_id") or resp.get("qr_image") or resp.get("qr_string"):
        data = resp
    else:
        err = str(resp.get("error") or resp.get("message") or resp)[:500]
        return {"success": False, "error": err, "raw": resp}

    txn_id = data.get("transaction_id") or data.get("id") or ""
    qr_image_raw = data.get("qr_image") or data.get("qrImage") or data.get("qr_image_url") or data.get("download_qr") or ""
    qr_string = data.get("qr_string") or data.get("qrString") or ""
    pay_url = data.get("payment_url") or data.get("pay_url") or ""
    aba = (
        data.get("abapay_deeplink")
        or data.get("aba_deeplink")
        or data.get("deeplink")
        or data.get("deep_link")
        or ""
    )
    if isinstance(aba, str):
        aba = aba.strip()
    else:
        aba = ""

    qr_image = _normalize_qr_image(str(qr_image_raw), str(qr_string))

    if not txn_id and not qr_image and not qr_string:
        return {"success": False, "error": "KHPAY response missing QR", "raw": resp}

    return {
        "success": True,
        "transaction_id": str(txn_id),
        "qr_string": str(qr_string),
        "qr_image": qr_image,
        "payment_url": str(pay_url),
        "abapay_deeplink": aba,
        "expires_in": data.get("expires_in") or 180,
        "raw": resp,
    }


def check(api_key: str, transaction_id: str) -> dict[str, Any]:
    """Mirror bot `_khpay_check_detail`."""
    out: dict[str, Any] = {"success": True, "paid": False, "amount": None, "status": "", "raw": {}}
    tid = (transaction_id or "").strip()
    if not (api_key or "").strip() or not tid:
        return {**out, "success": False, "error": "missing key or transaction_id"}

    # Bot URL: KHPAY_CHECK_URL + / + transaction_id
    resp = _request("GET", f"/qr/check/{tid}", api_key)
    d = resp.get("data") if isinstance(resp.get("data"), dict) else resp
    if not isinstance(d, dict):
        d = {}
    out["raw"] = d
    st = str(d.get("status") or d.get("action") or "").strip()
    out["status"] = st
    paid_ok = bool(
        d.get("paid") is True
        or st.lower() in ("paid", "success", "completed", "approved")
    )
    amt = d.get("amount") or d.get("paid_amount") or d.get("total")
    if amt is not None:
        try:
            out["amount"] = round(float(amt), 2)
        except (TypeError, ValueError):
            pass
    out["paid"] = paid_ok
    out["action"] = str(d.get("action") or "").lower()
    return out


def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    sig = signature.strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    return hmac.compare_digest(expected, sig)

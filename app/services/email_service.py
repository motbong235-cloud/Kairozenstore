"""Email notifications (SMTP) — order paid / topup."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any


def _smtp_config(settings: dict | None = None) -> dict:
    s = settings or {}
    return {
        "host": (s.get("SMTP_HOST") or os.environ.get("SMTP_HOST") or "").strip(),
        "port": int(s.get("SMTP_PORT") or os.environ.get("SMTP_PORT") or 587),
        "user": (s.get("SMTP_USER") or os.environ.get("SMTP_USER") or "").strip(),
        "password": (s.get("SMTP_PASSWORD") or os.environ.get("SMTP_PASSWORD") or "").strip(),
        "from_email": (s.get("SMTP_FROM") or os.environ.get("SMTP_FROM") or s.get("SMTP_USER") or os.environ.get("SMTP_USER") or "").strip(),
        "from_name": (s.get("SITE_NAME") or os.environ.get("SITE_NAME") or "Kairozen Store").strip(),
    }


def send_email(to: str, subject: str, html: str, text: str = "", settings: dict | None = None) -> bool:
    cfg = _smtp_config(settings)
    if not cfg["host"] or not cfg["from_email"] or not to:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f'{cfg["from_name"]} <{cfg["from_email"]}>'
        msg["To"] = to
        if text:
            msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            server.ehlo()
            try:
                server.starttls(context=context)
                server.ehlo()
            except Exception:
                pass
            if cfg["user"] and cfg["password"]:
                server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from_email"], [to], msg.as_string())
        return True
    except Exception:
        return False


def notify_order_paid(order: dict, user: dict | None, settings: dict | None = None) -> bool:
    email = (user or {}).get("email") or order.get("contact") or ""
    if not email or "@" not in str(email):
        return False
    oid = order.get("id") or ""
    name = order.get("product_name") or ("Top-up" if order.get("type") == "topup" else "Order")
    price = float(order.get("price") or 0)
    delivery = order.get("delivery") or ""
    site = (settings or {}).get("SITE_NAME") or "Kairozen Store"
    subject = f"[{site}] Order {oid} · Paid"
    text = (
        f"សួស្តី,\n\n"
        f"ការបង់ប្រាក់ជោគជ័យ។\n"
        f"Order: {oid}\n"
        f"Product: {name}\n"
        f"Amount: ${price:.2f}\n"
    )
    if delivery and order.get("type") != "topup":
        text += f"\nAccount / Delivery:\n{delivery}\n"
    elif order.get("type") == "topup":
        text += f"\nWallet credited +${price:.2f}\n"
    text += f"\n— {site}\n"
    html = f"""
    <div style="font-family:system-ui,sans-serif;max-width:520px;margin:0 auto;padding:20px;color:#0f172a">
      <h2 style="color:#1d4ed8;margin:0 0 12px">{site}</h2>
      <p>ការបង់ប្រាក់ <b>ជោគជ័យ</b>។</p>
      <table style="width:100%;border-collapse:collapse;font-size:14px;margin:16px 0">
        <tr><td style="padding:8px;border:1px solid #e2e8f0;color:#64748b">Order</td>
            <td style="padding:8px;border:1px solid #e2e8f0"><b>{oid}</b></td></tr>
        <tr><td style="padding:8px;border:1px solid #e2e8f0;color:#64748b">Product</td>
            <td style="padding:8px;border:1px solid #e2e8f0">{name}</td></tr>
        <tr><td style="padding:8px;border:1px solid #e2e8f0;color:#64748b">Amount</td>
            <td style="padding:8px;border:1px solid #e2e8f0"><b>${price:.2f}</b></td></tr>
      </table>
      {"<p style='background:#f0fdf4;border:1px solid #bbf7d0;padding:12px;border-radius:10px'><b>Delivery</b><br><code style='word-break:break-all'>" + delivery.replace("<","&lt;") + "</code></p>" if delivery and order.get("type") != "topup" else ""}
      {"<p>Wallet <b>+$" + f"{price:.2f}" + "</b></p>" if order.get("type") == "topup" else ""}
      <p style="color:#64748b;font-size:12px;margin-top:24px">មើលប្រវត្តិក្នុង Profile លើ website។</p>
    </div>
    """
    return send_email(str(email), subject, html, text, settings)

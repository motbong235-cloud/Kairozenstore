"""Customer login — Google Sign-In (Google Identity Services, ID-token flow)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, jsonify, request, session

from app import database as db
from app import security
from app.services import order_service

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def client_id() -> str:
    s = (db.read().get("settings") or {})
    return (s.get("GOOGLE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID") or "").strip()


def verify_google_token(id_token: str, expected_aud: str) -> dict | None:
    """Validate an ID token with Google's tokeninfo endpoint."""
    url = "https://oauth2.googleapis.com/tokeninfo?" + urllib.parse.urlencode({"id_token": id_token})
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            claims = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if claims.get("aud") != expected_aud:
        return None
    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return None
    if str(claims.get("email_verified")).lower() != "true":
        return None
    try:
        if int(claims.get("exp", 0)) < time.time():
            return None
    except Exception:
        return None
    if not claims.get("sub") or not claims.get("email"):
        return None
    return claims


def public_user(u: dict | None) -> dict | None:
    if not u:
        return None
    return {
        "id": u["id"],
        "name": u.get("name"),
        "email": u.get("email"),
        "picture": u.get("picture"),
        "balance": round(float(u.get("balance") or 0), 2),
    }


def current_user() -> dict | None:
    uid = session.get("user_id")
    if not uid:
        return None
    return (db.read().get("users") or {}).get(uid)


@bp.post("/google")
def google_login():
    if not security.rate_limit("google_login", limit=20, window_sec=60):
        return jsonify({"ok": False, "error": "Too many requests"}), 429
    cid = client_id()
    if not cid:
        return jsonify({"ok": False, "error": "Google login is not configured"}), 503
    body = request.get_json(force=True, silent=True) or {}
    token = (body.get("credential") or "").strip()
    if not token or len(token) > 4096:
        return jsonify({"ok": False, "error": "Invalid token"}), 400
    claims = verify_google_token(token, cid)
    if not claims:
        return jsonify({"ok": False, "error": "Google verification failed"}), 401

    data = db.read()
    users = data.setdefault("users", {})
    uid = "g_" + claims["sub"]
    now = order_service.utc_now()
    u = users.get(uid) or {"id": uid, "created_at": now, "balance": 0}
    if "balance" not in u:
        u["balance"] = 0
    u.update({
        "email": claims["email"],
        "name": claims.get("name") or claims["email"].split("@")[0],
        "picture": claims.get("picture") or "",
        "last_login": now,
    })
    users[uid] = u
    db.write(data)

    session["user_id"] = uid
    session.permanent = True
    return jsonify({"ok": True, "user": public_user(u)})


@bp.get("/me")
def me():
    return jsonify({"ok": True, "user": public_user(current_user())})


@bp.post("/logout")
def logout():
    session.pop("user_id", None)
    return jsonify({"ok": True})


@bp.get("/orders")
def my_orders():
    u = current_user()
    if not u:
        return jsonify({"ok": False, "error": "Login required"}), 401
    return jsonify({"ok": True, "orders": order_service.orders_for_user(u["id"])})

"""Security helpers — rate limit, headers, password hash, lockout."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from functools import wraps
from threading import Lock

from flask import Request, g, jsonify, request, session

# In-memory rate buckets (per process — good enough for single worker Render free)
_lock = Lock()
_buckets: dict[str, list[float]] = defaultdict(list)
_lockouts: dict[str, float] = {}


def client_ip() -> str:
    # Render / proxies
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


def rate_limit(key: str, limit: int, window_sec: int) -> bool:
    """Return True if allowed, False if exceeded."""
    now = time.time()
    bucket_key = f"{key}:{client_ip()}"
    with _lock:
        hits = _buckets[bucket_key]
        # drop old
        hits[:] = [t for t in hits if now - t < window_sec]
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


def is_locked(scope: str) -> bool:
    key = f"{scope}:{client_ip()}"
    with _lock:
        until = _lockouts.get(key, 0)
        return time.time() < until


def lockout(scope: str, seconds: int = 300) -> None:
    key = f"{scope}:{client_ip()}"
    with _lock:
        _lockouts[key] = time.time() + seconds


def clear_lockout(scope: str) -> None:
    key = f"{scope}:{client_ip()}"
    with _lock:
        _lockouts.pop(key, None)


def hash_password(password: str, salt: str | None = None) -> str:
    """Format: pbkdf2$iterations$salt$hash"""
    salt = salt or secrets.token_hex(16)
    iterations = 120_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2${iterations}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    # Legacy plain-text support (migrate on successful login)
    if not stored.startswith("pbkdf2$"):
        return hmac.compare_digest(password, stored)
    try:
        _, iter_s, salt, hexhash = stored.split("$", 3)
        iterations = int(iter_s)
    except Exception:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return hmac.compare_digest(dk.hex(), hexhash)


def is_hashed(stored: str) -> bool:
    return bool(stored) and stored.startswith("pbkdf2$")


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
}


def apply_security_headers(response):
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    # CSP soft — allow inline for current templates + Trading not needed
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data: https: blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://accounts.google.com/gsi/style; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "script-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/client; "
        "frame-src https://accounts.google.com/gsi/; "
        "connect-src 'self' https:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    return response

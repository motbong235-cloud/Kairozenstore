"""Security helpers — Redis-backed rate limit / IP tracking (memory fallback)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import defaultdict
from threading import Lock

from flask import request, session

_lock = Lock()
_buckets: dict[str, list[float]] = defaultdict(list)
_lockouts: dict[str, float] = {}
_block_log: list[dict] = []
_MAX_BLOCK_LOG = 200

PROBE_PREFIXES = (
    "/wp-admin", "/wp-login", "/xmlrpc.php", "/phpmyadmin",
    "/.env", "/.git", "/vendor/phpunit", "/actuator",
    "/admin.php", "/cgi-bin", "/.aws", "/config.json",
    "/wp-content", "/wp-includes", "/shell", "/eval",
)

_redis = None
_redis_failed = False


def _redis_client():
    """Lazy Redis connection from REDIS_URL. None if unavailable."""
    global _redis, _redis_failed
    if _redis_failed:
        return None
    if _redis is not None:
        return _redis
    url = (os.environ.get("REDIS_URL") or os.environ.get("REDIS_TLS_URL") or "").strip()
    if not url:
        _redis_failed = True
        return None
    try:
        import redis
        client = redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=0.3,
            socket_timeout=0.3,
            retry_on_timeout=False,
        )
        client.ping()
        _redis = client
        return _redis
    except Exception:
        _redis_failed = True
        _redis = None
        return None


def redis_enabled() -> bool:
    return _redis_client() is not None


def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    real = request.headers.get("X-Real-IP", "")
    if real:
        return real.strip()[:45]
    return (request.remote_addr or "0.0.0.0")[:45]


def _record_block(ip: str, key: str, limit: int, window_sec: int, hits: int) -> None:
    """Log spam hit to memory + Redis list."""
    global _block_log
    entry = {
        "ip": ip,
        "key": key,
        "limit": limit,
        "window": window_sec,
        "hits": hits,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "path": "",
    }
    try:
        entry["path"] = (request.path or "")[:120]
    except Exception:
        pass
    with _lock:
        _block_log.append(entry)
        if len(_block_log) > _MAX_BLOCK_LOG:
            _block_log = _block_log[-_MAX_BLOCK_LOG:]
    r = _redis_client()
    if r:
        try:
            pipe = r.pipeline()
            pipe.lpush("kz:spam:events", json.dumps(entry, ensure_ascii=False))
            pipe.ltrim("kz:spam:events", 0, 199)
            pipe.hincrby("kz:spam:ip:" + ip, "count", 1)
            pipe.hset("kz:spam:ip:" + ip, mapping={
                "last_at": entry["at"],
                "last_key": key,
                "last_path": entry["path"],
            })
            pipe.expire("kz:spam:ip:" + ip, 86400 * 7)  # 7 days
            pipe.sadd("kz:spam:ips", ip)
            pipe.expire("kz:spam:ips", 86400 * 7)
            pipe.execute()
        except Exception:
            pass


def rate_limit(key: str, limit: int, window_sec: int) -> bool:
    """Return True if allowed. Redis when available; never blocks the request long."""
    ip = client_ip()
    r = _redis_client()
    if r:
        try:
            window_id = int(time.time() // window_sec)
            rk = f"kz:rl:{key}:{ip}:{window_id}"
            n = r.incr(rk)
            if n == 1:
                r.expire(rk, window_sec + 2)
            if n > limit:
                try:
                    _record_block(ip, key, limit, window_sec, n)
                except Exception:
                    pass
                return False
            return True
        except Exception:
            # Redis slow/down → mark failed so we stop trying this process
            global _redis_failed, _redis
            _redis_failed = True
            _redis = None

    now = time.time()
    bucket_key = f"{key}:{ip}"
    with _lock:
        hits = _buckets[bucket_key]
        hits[:] = [t for t in hits if now - t < window_sec]
        if len(hits) >= limit:
            _record_block(ip, key, limit, window_sec, len(hits))
            return False
        hits.append(now)
        if len(_buckets) > 8000:
            for k in list(_buckets.keys())[:2000]:
                _buckets.pop(k, None)
        return True


def is_locked(scope: str) -> bool:
    ip = client_ip()
    r = _redis_client()
    if r:
        try:
            v = r.get(f"kz:lock:{scope}:{ip}")
            if v is not None:
                return True
        except Exception:
            pass
    key = f"{scope}:{ip}"
    with _lock:
        return time.time() < _lockouts.get(key, 0)


def lockout(scope: str, seconds: int = 300) -> None:
    ip = client_ip()
    r = _redis_client()
    if r:
        try:
            r.setex(f"kz:lock:{scope}:{ip}", seconds, "1")
        except Exception:
            pass
    key = f"{scope}:{ip}"
    with _lock:
        _lockouts[key] = time.time() + seconds
    _record_block(ip, f"lockout:{scope}", 1, seconds, 1)


def clear_lockout(scope: str) -> None:
    ip = client_ip()
    r = _redis_client()
    if r:
        try:
            r.delete(f"kz:lock:{scope}:{ip}")
        except Exception:
            pass
    key = f"{scope}:{ip}"
    with _lock:
        _lockouts.pop(key, None)


def get_spam_report(limit: int = 100) -> dict:
    """Admin: IP spam summary from Redis (preferred) or memory."""
    r = _redis_client()
    if r:
        try:
            events_raw = r.lrange("kz:spam:events", 0, min(49, limit - 1))
            events = []
            for row in events_raw:
                try:
                    events.append(json.loads(row))
                except Exception:
                    pass
            ips = list(r.smembers("kz:spam:ips") or [])
            by_ip = []
            for ip in ips:
                h = r.hgetall("kz:spam:ip:" + ip) or {}
                if not h:
                    continue
                by_ip.append({
                    "ip": ip,
                    "count": int(h.get("count") or 0),
                    "keys": {h.get("last_key") or "?": int(h.get("count") or 0)},
                    "last_at": h.get("last_at") or "",
                    "paths": [h.get("last_path")] if h.get("last_path") else [],
                })
            by_ip.sort(key=lambda x: x["count"], reverse=True)
            # lockouts scan (limited)
            locks = []
            try:
                for key in r.scan_iter(match="kz:lock:*", count=50):
                    ttl = r.ttl(key)
                    if ttl and ttl > 0:
                        locks.append({"key": key, "until": time.time() + ttl, "seconds_left": int(ttl)})
            except Exception:
                pass
            return {
                "backend": "redis",
                "events": events[:50],
                "by_ip": by_ip[:50],
                "lockouts": locks[:50],
                "total_events": len(events_raw) if events_raw is not None else len(events),
            }
        except Exception:
            pass

    # memory fallback
    with _lock:
        logs = list(_block_log[-limit:])
        by_ip_map: dict[str, dict] = {}
        for e in logs:
            ip = e.get("ip") or "?"
            row = by_ip_map.setdefault(ip, {"ip": ip, "count": 0, "keys": {}, "last_at": "", "paths": []})
            row["count"] += 1
            k = e.get("key") or "?"
            row["keys"][k] = row["keys"].get(k, 0) + 1
            row["last_at"] = e.get("at") or row["last_at"]
            path = e.get("path") or ""
            if path and path not in row["paths"]:
                row["paths"].append(path)
                row["paths"] = row["paths"][-5:]
        now = time.time()
        locks = []
        for k, until in list(_lockouts.items()):
            if until > now:
                locks.append({"key": k, "until": until, "seconds_left": int(until - now)})
        summary = sorted(by_ip_map.values(), key=lambda x: x["count"], reverse=True)
    return {
        "backend": "memory",
        "events": list(reversed(logs[-50:])),
        "by_ip": summary[:50],
        "lockouts": locks,
        "total_events": len(logs),
    }


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    iterations = 120_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2${iterations}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
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


def csrf_token() -> str:
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_hex(24)
        session["_csrf"] = tok
    return tok


def validate_csrf(token: str | None) -> bool:
    expected = session.get("_csrf") or ""
    if not expected or not token:
        return False
    return hmac.compare_digest(str(token), str(expected))


def sanitize_text(value: str, max_len: int = 500) -> str:
    s = (value or "").strip()
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s)
    return s[:max_len]


def is_probe_path(path: str) -> bool:
    p = (path or "").lower()
    return any(p.startswith(b) for b in PROBE_PREFIXES)


def issue_captcha() -> dict:
    import random
    a, b = random.randint(2, 12), random.randint(1, 9)
    answer = a + b
    exp = int(time.time()) + 300
    material = f"{exp}:{answer}"
    sig = hashlib.sha256(f"kzcap:{material}".encode()).hexdigest()[:32]
    token = f"{exp}.{sig}"
    session["_captcha_ans"] = hashlib.sha256(f"{token}:{answer}".encode()).hexdigest()
    session["_captcha_exp"] = exp
    return {"question": f"{a} + {b} = ?", "token": token, "ttl": 300}


def verify_captcha(token: str | None, answer: str | None, honeypot: str | None = None) -> tuple[bool, str]:
    if honeypot:
        return False, "Bot detected"
    if not token or answer is None or str(answer).strip() == "":
        return False, "សូមបំពេញ CAPTCHA"
    try:
        ans = int(str(answer).strip())
    except Exception:
        return False, "CAPTCHA មិនត្រឹមត្រូវ"
    exp = session.get("_captcha_exp") or 0
    if int(time.time()) > int(exp):
        return False, "CAPTCHA ផុតកំណត់ · សូមសាកម្តងទៀត"
    expected = session.get("_captcha_ans") or ""
    got = hashlib.sha256(f"{token}:{ans}".encode()).hexdigest()
    if not expected or not hmac.compare_digest(expected, got):
        return False, "CAPTCHA មិនត្រឹមត្រូវ"
    session.pop("_captcha_ans", None)
    session.pop("_captcha_exp", None)
    return True, ""


def verify_recaptcha(response_token: str | None, secret: str) -> bool:
    if not secret or not response_token:
        return False
    try:
        import urllib.parse
        import urllib.request
        data = urllib.parse.urlencode({
            "secret": secret,
            "response": response_token,
            "remoteip": client_ip(),
        }).encode()
        req = urllib.request.Request(
            "https://www.google.com/recaptcha/api/siteverify",
            data=data,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            out = json.loads(resp.read().decode())
        return bool(out.get("success"))
    except Exception:
        return False


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
    "X-XSS-Protection": "0",
}


def apply_security_headers(response):
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "img-src 'self' data: https: blob:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://accounts.google.com/gsi/style; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "script-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/client; "
        "frame-src https://accounts.google.com/gsi/; "
        "connect-src 'self' https://accounts.google.com https://khpay.site https://oauth2.googleapis.com; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'",
    )
    if request.is_secure or request.headers.get("X-Forwarded-Proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

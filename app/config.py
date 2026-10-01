"""Application configuration — env-first, same idea as Laravel .env + config/."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(24)
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

    # Data directory (Render disk → local → tmp)
    DATA_DIR = os.environ.get("DATA_DIR", "").strip()

    SITE_NAME = "Kairozen Store"
    SITE_TAGLINE = "Premium Digital Services · Instant Delivery"

    # Khmer System (can also be set from admin panel / db)
    KHMER_PROFILE_KEY = os.environ.get("KHMER_PROFILE_KEY", "")
    KHMER_SECRET_KEY = os.environ.get("KHMER_SECRET_KEY", "")
    KHMER_MACHINE_ID = os.environ.get("KHMER_MACHINE_ID", "")
    KHMER_MERCHANT_NAME = os.environ.get("KHMER_MERCHANT_NAME", "Kairozen Store")

    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB uploads

    # Session / cookie hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"  # set 0 for local http
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8  # 8 hours


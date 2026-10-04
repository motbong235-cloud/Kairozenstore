"""Kairozen Store application factory."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from flask import Flask

from app.config import Config
from app.security import apply_security_headers
from app.tenant import bind_tenant


def create_app(config_class=Config) -> Flask:
    root = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        static_folder=str(root / "static"),
        template_folder=str(root / "templates"),
        instance_relative_config=False,
    )
    app.config.from_object(config_class)
    # Allow phone-camera photos (server auto-compresses them afterwards)
    app.config["MAX_CONTENT_LENGTH"] = max(
        int(app.config.get("MAX_CONTENT_LENGTH") or 0), (int(os.environ.get("MAX_UPLOAD_MB", "50")) + 5) * 1024 * 1024
    )

    from app.routes import admin, api, auth, web

    app.register_blueprint(web.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(admin.bp)

    # Keep the session secret stable across restarts/deploys, otherwise every
    # deploy logs out all customers + admin. Env wins; else it is stored in the DB.
    if not os.environ.get("SECRET_KEY"):
        try:
            from app import database as db

            with app.app_context():
                d = db.read()
                st = d.setdefault("settings", {})
                if not st.get("_SECRET_KEY"):
                    st["_SECRET_KEY"] = secrets.token_hex(32)
                    db.write(d)
                app.secret_key = st["_SECRET_KEY"]
        except Exception:
            pass

    @app.after_request
    def _sec_headers(resp):
        return apply_security_headers(resp)

    @app.before_request
    def _security_gate():
        bind_tenant()
        from flask import request, abort, jsonify, session
        from app import security as sec
        path = request.path or ""

        # 1) Block scanner probes
        if sec.is_probe_path(path):
            abort(404)

        # 2) Global API rate limit (per IP) — soft on reads, strict on writes
        if path.startswith("/api/"):
            method = (request.method or "GET").upper()
            # Public catalog / health: very high limit (page load polls these)
            if path in ("/api/catalog", "/health") or path.startswith("/api/catalog"):
                if not sec.rate_limit("catalog", limit=600, window_sec=60):
                    return jsonify({"ok": False, "error": "Too many requests"}), 429
            elif path.startswith("/api/webhook/"):
                if not sec.rate_limit("webhook", limit=120, window_sec=60):
                    return jsonify({"ok": False, "error": "Too many requests"}), 429
            elif path.startswith("/api/auth/heartbeat"):
                if not sec.rate_limit("hb", limit=60, window_sec=60):
                    return jsonify({"ok": True, "throttled": True})
            elif path.startswith("/api/admin/"):
                if not sec.rate_limit("admin_api", limit=120, window_sec=60):
                    return jsonify({"ok": False, "error": "Too many requests"}), 429
            elif method in ("GET", "HEAD"):
                # other reads
                if not sec.rate_limit("api_read", limit=300, window_sec=60):
                    return jsonify({"ok": False, "error": "Too many requests"}), 429
            else:
                # POST/PUT/DELETE — stricter (orders, topup, login)
                if not sec.rate_limit("api_write", limit=60, window_sec=60):
                    return jsonify({"ok": False, "error": "Too many requests"}), 429

        # 3) Method hardening
        if request.method not in ("GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"):
            abort(405)

        # 4) Reject oversized JSON content-type tricks on write methods
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            cl = request.content_length or 0
            if cl > 25 * 1024 * 1024:  # hard ceiling before Flask MAX
                abort(413)

        return None

    # Session cookie hardening
    app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    if os.environ.get("SESSION_COOKIE_SECURE", "1") not in ("0", "false", "False"):
        app.config.setdefault("SESSION_COOKIE_SECURE", True)
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", 60 * 60 * 24 * 14)  # 14 days



    @app.errorhandler(413)
    def err_413(e):
        from flask import jsonify
        return jsonify({"ok": False, "error": "File too large (max {} MB)".format(os.environ.get("MAX_UPLOAD_MB", "50"))}), 413

    @app.errorhandler(500)
    def err_500(e):
        app.logger.exception("Server error")
        from flask import request as _rq, jsonify as _js
        if _rq.path.startswith("/api/"):
            return _js({"ok": False, "error": "Server error"}), 500
        return "<h1>Server Error</h1><p><a href='/'>Back to store</a></p>", 500

    return app

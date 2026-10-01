"""Kairozen Store application factory."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from flask import Flask

from app.config import Config
from app.security import apply_security_headers


def create_app(config_class=Config) -> Flask:
    root = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        static_folder=str(root / "static"),
        template_folder=str(root / "templates"),
        instance_relative_config=False,
    )
    app.config.from_object(config_class)

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

    # Block noisy probe paths quickly
    @app.before_request
    def _block_probes():
        from flask import request, abort
        path = (request.path or "").lower()
        blocked = (
            "/wp-admin", "/wp-login", "/.env", "/xmlrpc.php",
            "/phpmyadmin", "/.git", "/vendor/phpunit",
        )
        if any(path.startswith(b) for b in blocked):
            abort(404)


    @app.errorhandler(500)
    def err_500(e):
        app.logger.exception("Server error")
        return "<h1>Server Error</h1><p><a href='/'>Back to store</a></p>", 500

    return app

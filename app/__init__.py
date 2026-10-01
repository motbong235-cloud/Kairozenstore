"""Kairozen Store application factory."""
from __future__ import annotations

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

    from app.routes import admin, api, web

    app.register_blueprint(web.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(admin.bp)

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
        return (
            f"<h1>Server Error</h1><pre>{getattr(e, 'original_exception', e)}</pre>"
            f"<p><a href='/health'>/health</a></p>",
            500,
        )

    return app

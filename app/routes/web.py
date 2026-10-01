"""Server-rendered pages."""
from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, jsonify, render_template, request, send_from_directory

from app import database as db

bp = Blueprint("web", __name__)

# Private admin URL — change via env ADMIN_PATH (no leading slash)
ADMIN_PATH = (os.environ.get("ADMIN_PATH") or "kz-panel-8391").strip().strip("/")


def _site() -> dict:
    s = db.read().get("settings") or {}
    v = s.get("LOGO_VERSION", 0)
    base = request.url_root.rstrip("/")
    return {
        "name": s.get("SITE_NAME") or "Kairozen Store",
        "tagline": s.get("SITE_TAGLINE") or "",
        "description": s.get("SITE_DESCRIPTION") or s.get("SITE_TAGLINE") or "",
        "logo": f"/site-logo?v={v}",
        "logo_abs": f"{base}/site-logo?v={v}",
        "url": base,
        "google_client_id": (s.get("GOOGLE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID") or "").strip(),
    }


@bp.get("/")
def home():
    return render_template("shop/index.html", site=_site())


@bp.get("/profile")
def profile():
    return render_template("shop/profile.html", site=_site())


@bp.get(f"/{ADMIN_PATH}")
def admin_panel():
    """Hidden admin UI — not linked from the storefront."""
    resp = current_app.make_response(render_template("admin/index.html", site=_site()))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@bp.get("/admin")
def admin_decoy():
    """Old path: do not reveal panel."""
    abort(404)


@bp.get("/health")
def health():
    return jsonify({
        "ok": True,
        "app": "Kairozen Store",
        "storage": db.storage_info(),
    })


def _logo_response():
    s = db.read().get("settings") or {}
    name = s.get("SITE_LOGO") or ""
    if name:
        got = db.media_get(name)
        if got:
            mime, blob = got
            resp = Response(blob, mimetype=mime)
            resp.headers["Cache-Control"] = "public, max-age=86400"
            return resp
    resp = send_from_directory(current_app.static_folder, "logo.png", mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.get("/site-logo")
def site_logo():
    return _logo_response()


@bp.get("/favicon.ico")
def favicon():
    # Google & browsers request /favicon.ico by default → serve the admin-uploaded logo
    return _logo_response()


@bp.get("/robots.txt")
def robots():
    base = request.url_root.rstrip("/")
    txt = f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /{ADMIN_PATH}\nSitemap: {base}/sitemap.xml\n"
    return Response(txt, mimetype="text/plain")


@bp.get("/sitemap.xml")
def sitemap():
    base = request.url_root.rstrip("/")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{base}/</loc></url></urlset>"
    )
    return Response(xml, mimetype="application/xml")


@bp.get("/api/media/<path:filename>")
def media(filename: str):
    got = db.media_get(filename)
    if got:
        mime, blob = got
        resp = Response(blob, mimetype=mime)
        resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return resp
    static = Path(current_app.static_folder)
    if (static / filename).is_file():
        return send_from_directory(current_app.static_folder, filename)
    abort(404)

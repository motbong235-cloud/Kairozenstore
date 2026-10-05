"""Server-rendered pages."""
from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, jsonify, render_template, request, send_from_directory

from app import database as db
from app.tenant import is_valid_slug, root_domain, current_slug
from app.services import tenants as ten_svc

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
        "khmer_font": (Path(current_app.static_folder) / "fonts" / "khmer.woff2").is_file(),
        "google_client_id": (s.get("GOOGLE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID") or "").strip(),
    }


@bp.get("/")
def home():
    from flask import g, abort
    import json
    slug = getattr(g, "tenant_slug", None)
    if slug:
        try:
            main = db.resolve_data_dir() / "db.json"
            if main.exists():
                reg = json.loads(main.read_text(encoding="utf-8"))
                t = (reg.get("tenants") or {}).get(slug)
                if t is not None and not t.get("active", True):
                    abort(404)
        except Exception:
            pass
    return render_template("shop/index.html", site=_site())


@bp.get("/profile")
def profile():
    return render_template("shop/profile.html", site=_site())


@bp.get("/faq")
def faq():
    return render_template("shop/faq.html", site=_site())


@bp.get("/terms")
def terms():
    return render_template("shop/terms.html", site=_site())


@bp.get("/about")
def about():
    return render_template("shop/about.html", site=_site())


@bp.get("/how-to-buy")
def how_to_buy():
    return render_template("shop/how_to_buy.html", site=_site())


@bp.get("/contact")
def contact():
    return render_template("shop/contact.html", site=_site())


@bp.get("/privacy")
def privacy():
    return render_template("shop/privacy.html", site=_site())


@bp.get("/partner")
def partner():
    """Signup page — open a store on subdomain for $25."""
    return render_template(
        "shop/partner.html",
        site=_site(),
        price=ten_svc.plan_price(),
        base_domain=ten_svc.base_domain() or "yourdomain.com",
    )


@bp.get("/products")
def products():
    """Shiryu-style full catalog."""
    return render_template("shop/products.html", site=_site())


@bp.get("/login")
def login_page():
    return render_template("shop/login.html", site=_site())


@bp.get("/register")
def register_page():
    return render_template("shop/register.html", site=_site())


@bp.get("/product/<int:pid>")
def product_detail(pid: int):
    """Shiryu-style product detail page."""
    return render_template("shop/product.html", site=_site(), product_id=pid)


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




@bp.get("/open-store")
def open_store_page():
    """Public page: request your subdomain store."""
    return render_template("shop/open_store.html", site=_site(), root_domain=root_domain())



@bp.get("/health")
def health():
    return jsonify({
        "ok": True,
        "app": "Kairozen Store",
        "tenant": current_slug(),
        "root_domain": root_domain(),
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


@bp.get("/manifest.webmanifest")
def web_manifest():
    from flask import current_app, send_from_directory
    resp = send_from_directory(current_app.static_folder, "manifest.webmanifest", mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@bp.get("/sw.js")
def service_worker():
    from flask import current_app, send_from_directory
    resp = send_from_directory(current_app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


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



@bp.get("/open-aba")
def open_aba():
    """Redirect to ABA Mobile app (deep link). Query: dl= or qr="""
    from flask import request, Response, redirect
    import urllib.parse

    dl = (request.args.get("dl") or "").strip()
    qr = (request.args.get("qr") or "").strip()
    if not dl and qr:
        dl = "abamobilebank://ababank.com?type=payway&qrcode=" + urllib.parse.quote(qr, safe="")
    if not dl:
        return Response("Missing dl or qr", status=400)
    # intent for Android fallback
    intent = "intent://ababank.com?type=payway&qrcode=" + urllib.parse.quote(qr or "", safe="") + "#Intent;scheme=abamobilebank;package=com.paygo24.ibank;end"
    safe_dl = (
        dl.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    )
    safe_intent = intent.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    # JS-safe
    js_dl = json_dumps_safe(dl)
    js_intent = json_dumps_safe(intent)
    html = f"""<!DOCTYPE html>
<html lang="km"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>បើក ABA Pay…</title>
<style>
body{{font-family:system-ui,sans-serif;text-align:center;padding:2rem;background:#0b1f3a;color:#fff;margin:0}}
.btn{{display:inline-block;margin:12px 8px;padding:14px 28px;background:#ed1c24;color:#fff;text-decoration:none;border-radius:12px;font-size:1.1rem;font-weight:700}}
.btn2{{background:#1a73e8}}
.hint{{color:#a8c0d8;font-size:0.9rem;margin-top:1.5rem;line-height:1.5}}
</style>
<script>
(function(){{
  var dl = {js_dl};
  var intent = {js_intent};
  var isAndroid = /Android/i.test(navigator.userAgent||"");
  function go(){{
    if(isAndroid){{
      window.location.href = intent;
      setTimeout(function(){{ window.location.href = dl; }}, 400);
    }} else {{
      window.location.href = dl;
    }}
  }}
  go();
  setTimeout(go, 300);
}})();
</script>
</head>
<body>
  <p style="font-size:1.2rem;margin-top:2rem">🏦 កំពុងបើក <b>ABA Mobile → Pay</b>…</p>
  <p><a class="btn" href="{safe_intent}">បើក ABA (Android)</a></p>
  <p><a class="btn btn2" href="{safe_dl}">បើក ABA (iOS)</a></p>
  <p class="hint">បើមិនចូល App — សូមបើកក្នុង Safari / Chrome</p>
</body></html>"""
    return Response(html, mimetype="text/html; charset=utf-8")


def json_dumps_safe(s: str) -> str:
    import json
    return json.dumps(s or "")

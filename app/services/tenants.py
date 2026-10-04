"""Multi-tenant stores — subdomain per partner ($25 plan)."""
from __future__ import annotations

import copy
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

from flask import g, has_request_context, request

from app import database as db

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")
RESERVED = {
    "www", "api", "admin", "mail", "ftp", "app", "store", "shop",
    "static", "cdn", "platform", "partner", "billing", "support",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def base_domain() -> str:
    return (os.environ.get("BASE_DOMAIN") or "").strip().lower().lstrip(".")


def plan_price() -> float:
    try:
        return float(os.environ.get("TENANT_PRICE") or 25)
    except Exception:
        return 25.0


def normalize_slug(slug: str) -> str:
    s = (slug or "").strip().lower()
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:32]


def valid_slug(slug: str) -> tuple[bool, str]:
    s = normalize_slug(slug)
    if not s or not SLUG_RE.match(s):
        return False, "Slug មិនត្រឹមត្រូវ (a-z, 0-9, -)"
    if s in RESERVED:
        return False, "Slug នេះមិនអាចប្រើបាន"
    return True, s


def _platform() -> dict:
    d = db.read()
    return d.setdefault("platform", {"tenants": {}, "orders": []})


def list_tenants() -> list[dict]:
    p = _platform()
    return list((p.get("tenants") or {}).values())


def get_tenant(slug: str) -> dict | None:
    ok, s = valid_slug(slug)
    if not ok:
        return None
    return (_platform().get("tenants") or {}).get(s)


def save_tenant(tenant: dict) -> None:
    d = db.read()
    plat = d.setdefault("platform", {"tenants": {}, "orders": []})
    tenants = plat.setdefault("tenants", {})
    slug = tenant.get("slug")
    if not slug:
        return
    tenants[slug] = tenant
    db.write(d)


def create_tenant_pending(slug: str, shop_name: str, email: str, owner_id: str | None = None) -> dict[str, Any]:
    ok, s = valid_slug(slug)
    if not ok:
        return {"ok": False, "error": s}
    if get_tenant(s):
        return {"ok": False, "error": "Slug នេះមានរួចហើយ"}
    price = plan_price()
    order_id = "TN" + secrets.token_hex(4).upper()
    tenant = {
        "slug": s,
        "shop_name": (shop_name or s).strip()[:80],
        "email": (email or "").strip()[:120],
        "owner_id": owner_id,
        "status": "pending_payment",  # pending_payment | active | suspended
        "plan": "store",
        "price": price,
        "order_id": order_id,
        "created_at": utc_now(),
        "paid_at": None,
        "subdomain": f"{s}.{base_domain()}" if base_domain() else s,
    }
    # platform order record
    d = db.read()
    plat = d.setdefault("platform", {"tenants": {}, "orders": []})
    plat.setdefault("tenants", {})[s] = tenant
    plat.setdefault("orders", []).insert(0, {
        "id": order_id,
        "type": "tenant_plan",
        "slug": s,
        "price": price,
        "status": "pending_payment",
        "created_at": utc_now(),
        "email": email,
    })
    plat["orders"] = plat["orders"][:200]
    db.write(d)
    return {"ok": True, "tenant": tenant, "order_id": order_id, "price": price}


def activate_tenant(slug: str) -> dict | None:
    t = get_tenant(slug)
    if not t:
        return None
    t["status"] = "active"
    t["paid_at"] = utc_now()
    # seed empty shop settings for tenant
    t.setdefault("settings", {
        "SITE_NAME": t.get("shop_name") or slug,
        "SITE_TAGLINE": "Premium store",
        "REQUIRE_LOGIN": True,
    })
    t.setdefault("products", [])
    t.setdefault("categories", [])
    t.setdefault("orders", [])
    t.setdefault("users", {})
    t.setdefault("stock_files", {})
    save_tenant(t)
    # mark platform order paid
    d = db.read()
    for o in (d.get("platform") or {}).get("orders") or []:
        if o.get("slug") == slug and o.get("type") == "tenant_plan":
            o["status"] = "paid"
            o["paid_at"] = utc_now()
    db.write(d)
    return t


def resolve_host_tenant() -> dict | None:
    """Return tenant dict if Host is {slug}.BASE_DOMAIN and tenant active."""
    if not has_request_context():
        return None
    host = (request.host or "").split(":")[0].lower()
    bd = base_domain()
    if not bd or not host.endswith("." + bd):
        return None
    sub = host[: -(len(bd) + 1)]
    if not sub or "." in sub:
        return None
    t = get_tenant(sub)
    if not t or t.get("status") != "active":
        return None
    return t


def tenant_data() -> dict:
    """Data blob for current request: tenant store or main platform store."""
    t = getattr(g, "tenant", None) if has_request_context() else None
    if t and t.get("status") == "active":
        # ensure structure
        return {
            "settings": t.get("settings") or {},
            "products": t.get("products") or [],
            "categories": t.get("categories") or [],
            "orders": t.get("orders") or [],
            "users": t.get("users") or {},
            "stock_files": t.get("stock_files") or {},
            "_tenant_slug": t.get("slug"),
        }
    return db.read()


def write_tenant_data(data: dict) -> None:
    slug = data.get("_tenant_slug")
    if not slug:
        db.write(data)
        return
    t = get_tenant(slug)
    if not t:
        return
    t["settings"] = data.get("settings") or {}
    t["products"] = data.get("products") or []
    t["categories"] = data.get("categories") or []
    t["orders"] = data.get("orders") or []
    t["users"] = data.get("users") or {}
    t["stock_files"] = data.get("stock_files") or {}
    save_tenant(t)

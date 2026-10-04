"""Multi-tenant subdomain resolution.

Example:
  kairozen.store          → main store (platform)
  shop1.kairozen.store    → tenant "shop1"
  www.kairozen.store      → main store

Env:
  ROOT_DOMAIN=kairozen.store
  PLATFORM_HOSTS=kairozen.store,www.kairozen.store,localhost,127.0.0.1
"""
from __future__ import annotations

import os
import re
import time
from flask import g, request

_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")

RESERVED = {
    "www", "api", "admin", "mail", "smtp", "ftp", "cdn", "static",
    "app", "store", "shop", "support", "help", "status", "blog",
    "preview", "staging", "test", "dev", "platform",
}


def root_domain() -> str:
    return (os.environ.get("ROOT_DOMAIN") or "kairozen.store").strip().lower()


def platform_hosts() -> set[str]:
    raw = os.environ.get("PLATFORM_HOSTS") or ""
    hosts = {h.strip().lower() for h in raw.split(",") if h.strip()}
    rd = root_domain()
    hosts.update({rd, f"www.{rd}", "localhost", "127.0.0.1"})
    # Render default host always platform
    render = (os.environ.get("RENDER_EXTERNAL_HOSTNAME") or "").strip().lower()
    if render:
        hosts.add(render)
    return hosts


def parse_host() -> str:
    host = (request.headers.get("X-Forwarded-Host") or request.host or "").split(":")[0]
    return host.strip().lower()


def resolve_tenant_slug() -> str | None:
    """Return tenant slug or None for main/platform store."""
    host = parse_host()
    if not host or host in platform_hosts():
        return None
    rd = root_domain()
    if host.endswith("." + rd):
        sub = host[: -(len(rd) + 1)]
        if not sub or "." in sub:  # multi-level not supported
            return None
        sub = sub.lower()
        if sub in RESERVED or not _SLUG_RE.match(sub):
            return None
        return sub
    # Custom domain tenants (optional map in env later)
    return None


def is_valid_slug(slug: str) -> bool:
    s = (slug or "").strip().lower()
    return bool(_SLUG_RE.match(s)) and s not in RESERVED


def bind_tenant() -> None:
    """Call in before_request — sets g.tenant_slug, g.is_tenant."""
    slug = resolve_tenant_slug()
    g.tenant_slug = slug
    g.is_tenant = bool(slug)


def current_slug() -> str | None:
    return getattr(g, "tenant_slug", None)

# Kairozen Store

Premium digital accounts shop (CapCut, Netflix, Spotify…)  
**Auto payment (Khmer System)** · **Auto delivery** · **Admin panel**

Inspired by shops like [shiryupremium.com](https://shiryupremium.com) (Tailwind + Alpine frontend, server-rendered catalog).

## Architecture (professional layout)

Similar to a clean PHP/Laravel project:

```
kairozen-store/
├── server.py / wsgi.py     # entry (like public/index.php)
├── app/
│   ├── __init__.py         # create_app() factory
│   ├── config.py           # env config
│   ├── database.py         # data layer
│   ├── routes/
│   │   ├── web.py          # pages
│   │   ├── api.py          # public API
│   │   └── admin.py        # admin API
│   └── services/
│       ├── order_service.py
│       └── khmer_system.py
├── templates/
│   ├── shop/index.html
│   └── admin/index.html
├── static/
├── data/db.json
└── requirements.txt
```

| Layer | Role |
|-------|------|
| **routes** | HTTP only (controllers) |
| **services** | Business logic (orders, payment) |
| **database** | Read/write JSON (repository) |
| **config** | Secrets & env |

## Run

```bash
pip install -r requirements.txt
python server.py
# http://127.0.0.1:5000
# http://127.0.0.1:5000/admin  password: admin123
```

## Admin setup

1. Stock → product ID → paste accounts (1 line each)
2. Settings → Khmer System Profile Key
3. Customer pays KHQR → account auto-delivered

## Deploy (Render)

```
Build:  pip install -r requirements.txt
Start:  gunicorn server:app --bind 0.0.0.0:$PORT
```

Optional env: `SECRET_KEY`, `ADMIN_PASSWORD`, `DATA_DIR=/var/data` (with disk)


## Private Admin

- Storefront has **no Admin link**
- Panel URL: `/kz-panel-8391` (default)
- Change with env: `ADMIN_PATH=your-secret-path`
- Old `/admin` returns **404**
- Password: `admin123` (change in Settings)


## Security

| Feature | Detail |
|---------|--------|
| Hidden admin | `/kz-panel-8391` (env `ADMIN_PATH`) · `/admin` → 404 |
| Password | PBKDF2 hash (auto-migrates on login) |
| Login lockout | Rate limit + 5 min lock after spam |
| Order API | Rate limited |
| Session | HttpOnly · SameSite=Lax · Secure (HTTPS) |
| Headers | CSP · X-Frame-Options DENY · nosniff |
| Probes | `/wp-admin` · `/.env` · `/phpmyadmin` → 404 |

Local HTTP testing:
```
SESSION_COOKIE_SECURE=0 python server.py
```


## v2 — Persistent data · Logo · Google login

### 1. Data never lost on update
Cause of data loss before: `data/db.json` lived inside the deployed code, so every deploy replaced it.
Now:
- `data/seed.json` = first-run starter data only (never overwrites live data)
- Live data goes to **Postgres** (`DATABASE_URL`) or a **persistent disk** (`DATA_DIR`)
- Logo + product images are stored in the same place (not on the temporary disk)
- New settings are merged in on startup; existing values are never overwritten
- Admin → Settings → **Backup / Restore**, and a banner warns if storage is temporary
- `/health` shows the active storage mode

### 2. Website logo
Admin → Settings → **Website Logo**. Used for favicon, `/favicon.ico`, Open Graph, schema.org
`Organization.logo` (what Google reads for search results), and the store header.
Use a square PNG ≥ 512px (Google needs ≥ 48px, multiple of 48 is ideal).

### 3. Login with Google
1. Google Cloud Console → APIs & Services → Credentials → **OAuth client ID (Web)**
2. Authorized JavaScript origins: `https://your-site.onrender.com`
3. Paste the Client ID in Admin → Settings → Login with Google (or env `GOOGLE_CLIENT_ID`)
4. Optional: tick "require login" to force Google login before buying
Customers can see "My orders" and their delivered accounts after login. Admin → Users lists them.


### Persistent disk (/var/data) on Render
1. Service → **Disks** → Add Disk → Mount Path `/var/data` (needs a paid plan)
2. Environment → `DATA_DIR` = `/var/data`
3. Redeploy. `/health` must show `"mode": "disk", "persistent": true, "path": "/var/data"`
Even if `DATA_DIR` is forgotten, the app auto-uses `/var/data` when the disk is mounted.


## Khmer font (ពុម្ពអក្សរខ្មែរ)

All pages load Khmer fonts in 3 layers (see `templates/_fonts.html`):
1. **Self-hosted** `static/fonts/khmer.woff2` (optional, most reliable — works offline / if Google is blocked)
2. **Google Fonts**: Kantumruy Pro + Noto Sans Khmer
3. **System fonts**: Khmer OS, Khmer UI, Leelawadee UI, Nokora…

To self-host: download a Khmer font from fonts.google.com (e.g. *Kantumruy Pro* or *Noto Sans Khmer*),
convert to `.woff2` and save it as `static/fonts/khmer.woff2`. It is picked up automatically (no code change).

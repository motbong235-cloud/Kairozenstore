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

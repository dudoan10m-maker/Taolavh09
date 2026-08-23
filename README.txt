TOOLMOWIS BACKEND - FIXED

Production API:
https://taolavh10.onrender.com

Persistence:
- Render PostgreSQL is required.
- DATABASE_URL is injected from the Render PostgreSQL database.
- No database password is hard-coded in app.py.
- REQUIRE_POSTGRES=true prevents silent fallback to /tmp SQLite.

Start:
gunicorn app:app

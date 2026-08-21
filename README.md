# Toolmowis backend

## Upload these 3 files to GitHub
- app.py
- requirements.txt
- render.yaml

## Render
Build Command:
pip install -r requirements.txt

Start Command:
gunicorn app:app

## Permanent storage
Create a PostgreSQL database on Render and put its Internal Database URL into the
web service environment variable `DATABASE_URL`.

If DATABASE_URL is not set, the app uses `/tmp/toolmowis.db`; that fallback is
only for testing and is NOT permanent on Render.

## Test after deploy
Open:
/
 /health
 /api
 /pricing
 /api/status

The root `/` intentionally returns JSON so it does not show a 404.

This backend provides generic account/settings/pricing/status storage. It does
not implement gambling-result prediction logic.

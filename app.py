import os
import json
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SQLITE_PATH = os.getenv("SQLITE_PATH", "/tmp/toolmowis.db")


def db_type():
    return "postgres" if DATABASE_URL else "sqlite"


def sqlite_conn():
    parent = os.path.dirname(SQLITE_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(SQLITE_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    if DATABASE_URL:
        import psycopg2
        con = psycopg2.connect(DATABASE_URL)
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                assigned_key TEXT,
                max_devices INTEGER NOT NULL DEFAULT 1,
                deleted BOOLEAN NOT NULL DEFAULT FALSE,
                created_at BIGINT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                key TEXT PRIMARY KEY,
                max_devices INTEGER NOT NULL DEFAULT 1,
                accounts TEXT NOT NULL DEFAULT '[]',
                created_at BIGINT NOT NULL
            )
        """)
        con.commit()
        cur.close()
        con.close()
    else:
        con = sqlite_conn()
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                assigned_key TEXT,
                max_devices INTEGER NOT NULL DEFAULT 1,
                deleted INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                key TEXT PRIMARY KEY,
                max_devices INTEGER NOT NULL DEFAULT 1,
                accounts TEXT NOT NULL DEFAULT '[]',
                created_at INTEGER NOT NULL
            )
        """)
        con.commit()
        con.close()


def now_ms():
    import time
    return int(time.time() * 1000)


def get_setting(name, default):
    if DATABASE_URL:
        import psycopg2
        con = psycopg2.connect(DATABASE_URL)
        cur = con.cursor()
        cur.execute("SELECT value FROM settings WHERE key=%s", (name,))
        row = cur.fetchone()
        cur.close()
        con.close()
    else:
        con = sqlite_conn()
        row = con.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
        con.close()

    if not row:
        return default
    try:
        return json.loads(row[0] if not isinstance(row, sqlite3.Row) else row["value"])
    except Exception:
        return default


def set_setting(name, value):
    raw = json.dumps(value, ensure_ascii=False)
    if DATABASE_URL:
        import psycopg2
        con = psycopg2.connect(DATABASE_URL)
        cur = con.cursor()
        cur.execute("""
            INSERT INTO settings(key,value) VALUES(%s,%s)
            ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value
        """, (name, raw))
        con.commit()
        cur.close()
        con.close()
    else:
        con = sqlite_conn()
        con.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
            (name, raw)
        )
        con.commit()
        con.close()


DEFAULT_PRICING = {
    "plans": {
        "1": {"base": 33000, "sale": 0, "saleExpiry": None},
        "2": {"base": 41000, "sale": 0, "saleExpiry": None},
        "3": {"base": 53000, "sale": 0, "saleExpiry": None}
    },
    "saleEnabled": False
}

DEFAULT_STATUS = {"locked": False, "message": ""}


@app.get("/")
def home():
    return jsonify({
        "ok": True,
        "service": "Toolmowis API",
        "status": "online",
        "database": db_type()
    })


@app.get("/health")
def health():
    return jsonify({"ok": True, "database": db_type()})


@app.get("/api/status")
def api_status():
    return jsonify(get_setting("status", DEFAULT_STATUS))


@app.post("/api/status")
def update_status():
    data = request.get_json(silent=True) or {}
    value = {
        "locked": bool(data.get("locked", False)),
        "message": str(data.get("message", "") or "")
    }
    set_setting("status", value)
    return jsonify({"ok": True, "status": value})


@app.get("/pricing")
def pricing():
    return jsonify(get_setting("pricing", DEFAULT_PRICING))


@app.post("/pricing")
def update_pricing():
    data = request.get_json(silent=True) or {}
    current = get_setting("pricing", DEFAULT_PRICING)
    plans = current.get("plans", {})

    incoming = data.get("plans", data)
    for n in ("1", "2", "3"):
        item = incoming.get(n, incoming.get(int(n), {})) if isinstance(incoming, dict) else {}
        if item:
            base = int(item.get("base", plans[n]["base"]))
            sale = int(item.get("sale", plans[n]["sale"]))
            if base < 0 or sale < 0 or (sale and sale >= base):
                return jsonify({"ok": False, "error": f"Invalid pricing for plan {n}"}), 400
            plans[n] = {
                "base": base,
                "sale": sale,
                "saleExpiry": item.get("saleExpiry", plans[n].get("saleExpiry"))
            }

    current["plans"] = plans
    current["saleEnabled"] = bool(data.get("saleEnabled", current.get("saleEnabled", False)))
    set_setting("pricing", current)
    return jsonify({"ok": True, "pricing": current})


@app.get("/api")
def api_root():
    return jsonify({
        "ok": True,
        "endpoints": ["/", "/health", "/api/status", "/pricing"]
    })


@app.errorhandler(404)
def not_found(_):
    return jsonify({"ok": False, "error": "Not Found"}), 404


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))

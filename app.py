import os, json, time, secrets, sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# PostgreSQL connection: Render Environment Variable takes priority.
# Fallback is included so the service can connect immediately after deployment.
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://taolavh09_db_user:clhlySNpQIPgNhKwn6P3sKT5q3ulyNIS@dpg-da4q5u3bc2fs73c042ug-a/taolavh09_db").strip()
SQLITE_PATH = os.getenv("SQLITE_PATH", "/tmp/toolmowis.db")
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "https://taolavh09-9.onrender.com").rstrip("/")

DEFAULT_PRICING = {
    "plans": {
        "1": {"base": 33000, "sale": 0, "saleExpiry": None},
        "2": {"base": 41000, "sale": 0, "saleExpiry": None},
        "3": {"base": 53000, "sale": 0, "saleExpiry": None},
    },
    "saleEnabled": False,
}
DEFAULT_STATUS = {"locked": False, "message": ""}


def now_ms():
    return int(time.time() * 1000)


def normalize_exp_ms(value):
    """Chuẩn hoá thời gian hết hạn về milliseconds.
    Admin có thể gửi Unix seconds (10 chữ số) hoặc milliseconds (13 chữ số).
    """
    try:
        n = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    # Unix timestamp tính bằng giây hiện tại chỉ khoảng 10 chữ số.
    if n < 100_000_000_000:
        n *= 1000
    return n


def pg():
    import psycopg2
    con = psycopg2.connect(DATABASE_URL)
    con.autocommit = False
    return con


def sql_conn():
    parent = os.path.dirname(SQLITE_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(SQLITE_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    if DATABASE_URL:
        con = pg()
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS accounts(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, assigned_key TEXT, max_devices INTEGER NOT NULL DEFAULT 1,
            deleted BOOLEAN NOT NULL DEFAULT FALSE, created_at BIGINT NOT NULL)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS keys(
            key TEXT PRIMARY KEY, user_name TEXT, exp BIGINT NOT NULL,
            max_devices INTEGER NOT NULL DEFAULT 1, devices TEXT NOT NULL DEFAULT '[]',
            accounts TEXT NOT NULL DEFAULT '[]', deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at BIGINT NOT NULL)""")
        con.commit(); cur.close(); con.close()
    else:
        con = sql_conn(); c = con.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS accounts(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, assigned_key TEXT, max_devices INTEGER NOT NULL DEFAULT 1,
            deleted INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS keys(
            key TEXT PRIMARY KEY, user_name TEXT, exp INTEGER NOT NULL,
            max_devices INTEGER NOT NULL DEFAULT 1, devices TEXT NOT NULL DEFAULT '[]',
            accounts TEXT NOT NULL DEFAULT '[]', deleted INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL)""")
        con.commit(); con.close()


def setting_get(name, default):
    if DATABASE_URL:
        con = pg(); c = con.cursor()
        c.execute("SELECT value FROM settings WHERE key=%s", (name,))
        row = c.fetchone(); c.close(); con.close()
        raw = row[0] if row else None
    else:
        con = sql_conn()
        row = con.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
        con.close(); raw = row["value"] if row else None
    if raw is None:
        return default
    try: return json.loads(raw)
    except Exception: return default


def setting_set(name, value):
    raw = json.dumps(value, ensure_ascii=False)
    if DATABASE_URL:
        con = pg(); c = con.cursor()
        c.execute("""INSERT INTO settings(key,value) VALUES(%s,%s)
                    ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value""", (name, raw))
        con.commit(); c.close(); con.close()
    else:
        con = sql_conn()
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (name, raw))
        con.commit(); con.close()


def account_json(row):
    if not row: return None
    r = dict(row) if not isinstance(row, dict) else row
    return {
        "id": r["id"], "name": r["name"], "email": r["email"],
        "password": r["password"], "assignedKey": r.get("assigned_key"),
        "maxDevices": int(r.get("max_devices") or 1),
        "createdAt": int(r["created_at"])
    }


def key_json(row):
    if not row: return None
    r = dict(row) if not isinstance(row, dict) else row
    devices = json.loads(r.get("devices") or "[]")
    accounts = json.loads(r.get("accounts") or "[]")
    return {
        "key": r["key"], "user": r.get("user_name") or "",
        "exp": int(r["exp"]), "maxDevices": int(r.get("max_devices") or 1),
        "devicesUsed": len(devices), "devices": devices,
        "accounts": accounts, "deleted": bool(r.get("deleted", False)),
        "createdAt": int(r["created_at"])
    }


def get_account_by_id(aid):
    if DATABASE_URL:
        con=pg(); c=con.cursor(); c.execute("SELECT * FROM accounts WHERE id=%s AND deleted=FALSE",(aid,)); row=c.fetchone()
        cols=[d[0] for d in c.description] if row else []
        c.close(); con.close()
        return dict(zip(cols,row)) if row else None
    con=sql_conn(); row=con.execute("SELECT * FROM accounts WHERE id=? AND deleted=0",(aid,)).fetchone(); con.close()
    return dict(row) if row else None


def get_key(k):
    if DATABASE_URL:
        con=pg(); c=con.cursor(); c.execute("SELECT * FROM keys WHERE key=%s AND deleted=FALSE",(k,)); row=c.fetchone()
        cols=[d[0] for d in c.description] if row else []
        c.close(); con.close()
        return dict(zip(cols,row)) if row else None
    con=sql_conn(); row=con.execute("SELECT * FROM keys WHERE key=? AND deleted=0",(k,)).fetchone(); con.close()
    return dict(row) if row else None


@app.get("/")
def home():
    return jsonify({"ok": True, "service": "Toolmowis API", "status": "online",
                    "database": "postgres" if DATABASE_URL else "sqlite", "apiBase": PUBLIC_API_URL})


@app.get("/health")
def health():
    return jsonify({"ok": True, "database": "postgres" if DATABASE_URL else "sqlite"})


@app.get("/api/status")
def get_status():
    s = setting_get("status", DEFAULT_STATUS)
    if DATABASE_URL:
        try:
            con=pg(); c=con.cursor(); c.execute("SELECT COUNT(*) FROM accounts WHERE deleted=FALSE")
            n=c.fetchone()[0]; c.close(); con.close()
            s = dict(s); s["storage"]="postgres"; s["accountCount"]=n
        except Exception as e:
            s=dict(s); s["storage"]="postgres-error"; s["dbError"]=str(e)
    else:
        s=dict(s); s["storage"]="sqlite"
    return jsonify(s)


@app.post("/api/status")
def set_status():
    d=request.get_json(silent=True) or {}
    s={"locked":bool(d.get("locked",False)), "message":str(d.get("message","") or "")}
    setting_set("status",s)
    saved = setting_get("status", None)
    if saved != s:
        return jsonify({"ok":False,"error":"Bảo trì chưa được lưu/xác minh trên server"}),500
    return jsonify({"ok":True, **saved})


@app.post("/register")
def register():
    d=request.get_json(silent=True) or {}
    email=str(d.get("email","")).strip()
    password=str(d.get("password",""))
    name=str(d.get("name") or d.get("username") or email.split("@")[0]).strip()
    aid=str(d.get("id") or secrets.token_hex(12))
    if not email or not password: return jsonify({"success":False,"error":"Thiếu email hoặc mật khẩu"}),400
    try:
        if DATABASE_URL:
            con=pg(); c=con.cursor()
            c.execute("SELECT id FROM accounts WHERE email=%s AND deleted=FALSE",(email,))
            if c.fetchone(): c.close(); con.close(); return jsonify({"success":False,"error":"Tài khoản đã tồn tại"}),409
            c.execute("""INSERT INTO accounts(id,name,email,password,created_at)
                         VALUES(%s,%s,%s,%s,%s)""",(aid,name,email,password,now_ms()))
            con.commit(); c.close(); con.close()
        else:
            con=sql_conn()
            if con.execute("SELECT id FROM accounts WHERE email=? AND deleted=0",(email,)).fetchone():
                con.close(); return jsonify({"success":False,"error":"Tài khoản đã tồn tại"}),409
            con.execute("""INSERT INTO accounts(id,name,email,password,created_at)
                           VALUES(?,?,?,?,?)""",(aid,name,email,password,now_ms()))
            con.commit(); con.close()
        acc={"id":aid,"name":name,"email":email,"password":password,
             "assignedKey":None,"maxDevices":1,"createdAt":now_ms()}
        return jsonify({"success":True,"account":acc})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)}),500


@app.post("/login")
def login():
    d=request.get_json(silent=True) or {}
    email=str(d.get("email","")).strip(); password=str(d.get("password",""))
    if DATABASE_URL:
        con=pg(); c=con.cursor()
        c.execute("SELECT * FROM accounts WHERE email=%s AND password=%s AND deleted=FALSE",(email,password))
        row=c.fetchone(); cols=[x[0] for x in c.description] if row else []
        c.close(); con.close(); row=dict(zip(cols,row)) if row else None
    else:
        con=sql_conn(); row=con.execute("SELECT * FROM accounts WHERE email=? AND password=? AND deleted=0",(email,password)).fetchone(); con.close()
        row=dict(row) if row else None
    if not row: return jsonify({"success":False,"error":"Sai email hoặc mật khẩu"}),401
    return jsonify({"success":True,"account":account_json(row)})


@app.get("/accounts")
def accounts():
    if DATABASE_URL:
        con=pg(); c=con.cursor(); c.execute("SELECT * FROM accounts WHERE deleted=FALSE ORDER BY created_at DESC")
        rows=c.fetchall(); cols=[x[0] for x in c.description]; c.close(); con.close()
        arr=[account_json(dict(zip(cols,r))) for r in rows]
    else:
        con=sql_conn(); rows=con.execute("SELECT * FROM accounts WHERE deleted=0 ORDER BY created_at DESC").fetchall(); con.close()
        arr=[account_json(dict(r)) for r in rows]
    return jsonify({"accounts":arr})


@app.get("/my-account")
def my_account():
    """Return the authoritative server-side account + assigned key data."""
    aid = str(request.args.get("id", "")).strip()
    if not aid:
        return jsonify({"ok": False, "error": "Thiếu id"}), 400

    acc = get_account_by_id(aid)
    if not acc:
        return jsonify({"ok": False, "error": "Không tìm thấy tài khoản"}), 404

    account = account_json(acc)
    assigned_key = account.get("assignedKey")
    result = {
        "ok": True,
        "account": account,
        "assignedKey": assigned_key,
        "assignedExp": 0,
        "maxDevices": account.get("maxDevices", 1)
    }

    if assigned_key:
        key = get_key(assigned_key)
        if key:
            result["assignedExp"] = int(key.get("exp") or 0)
            result["maxDevices"] = int(key.get("max_devices") or account.get("maxDevices") or 1)
            result["key"] = assigned_key
            result["keyInfo"] = key_json(key)

    return jsonify(result)


@app.post("/delete-account")
def delete_account():
    d=request.get_json(silent=True) or {}; aid=str(d.get("accountId",""))
    if DATABASE_URL:
        con=pg(); c=con.cursor(); c.execute("UPDATE accounts SET deleted=TRUE WHERE id=%s",(aid,)); con.commit(); c.close(); con.close()
    else:
        con=sql_conn(); con.execute("UPDATE accounts SET deleted=1 WHERE id=?",(aid,)); con.commit(); con.close()
    return jsonify({"ok":True})


@app.post("/create-key")
def create_key():
    d = request.get_json(silent=True) or {}
    k = str(d.get("key", "")).strip()
    user = str(d.get("user", "") or "")
    try:
        exp = normalize_exp_ms(d.get("exp"))
        maxd = max(1, min(3, int(d.get("maxDevices") or 1)))
    except Exception:
        return jsonify({"ok": False, "error": "exp/maxDevices không hợp lệ"}), 400

    if not k or not exp:
        return jsonify({"ok": False, "error": "Thiếu key/exp"}), 400

    try:
        created = now_ms()
        if DATABASE_URL:
            con = pg()
            try:
                c = con.cursor()
                c.execute("""INSERT INTO keys
                    (key,user_name,exp,max_devices,devices,accounts,deleted,created_at)
                    VALUES(%s,%s,%s,%s,'[]','[]',FALSE,%s)
                    ON CONFLICT(key) DO UPDATE SET
                      user_name=EXCLUDED.user_name,
                      exp=EXCLUDED.exp,
                      max_devices=EXCLUDED.max_devices,
                      deleted=FALSE""",
                    (k, user, exp, maxd, created))
                c.execute("SELECT * FROM keys WHERE key=%s AND deleted=FALSE", (k,))
                row = c.fetchone()
                if not row:
                    con.rollback()
                    return jsonify({"ok": False, "error": "Ghi key thất bại"}), 500
                con.commit()
            finally:
                c.close()
                con.close()
        else:
            con = sql_conn()
            try:
                con.execute("""INSERT OR REPLACE INTO keys
                    (key,user_name,exp,max_devices,devices,accounts,deleted,created_at)
                    VALUES(?,?,?,?, '[]','[]',0,?)""",
                    (k, user, exp, maxd, created))
                con.commit()
                row = con.execute(
                    "SELECT * FROM keys WHERE key=? AND deleted=0", (k,)
                ).fetchone()
                if not row:
                    return jsonify({"ok": False, "error": "Ghi key thất bại"}), 500
            finally:
                con.close()

        return jsonify({"ok": True, "success": True, "key": k})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/keys")
def keys():
    if DATABASE_URL:
        con=pg(); c=con.cursor(); c.execute("SELECT * FROM keys WHERE deleted=FALSE ORDER BY created_at DESC")
        rows=c.fetchall(); cols=[x[0] for x in c.description]; c.close(); con.close()
        arr=[key_json(dict(zip(cols,r))) for r in rows]
    else:
        con=sql_conn(); rows=con.execute("SELECT * FROM keys WHERE deleted=0 ORDER BY created_at DESC").fetchall(); con.close()
        arr=[key_json(dict(r)) for r in rows]
    return jsonify({"keys":arr})


@app.post("/delete-key")
def delete_key():
    d=request.get_json(silent=True) or {}; k=str(d.get("key",""))
    if DATABASE_URL:
        con=pg(); c=con.cursor(); c.execute("UPDATE keys SET deleted=TRUE WHERE key=%s",(k,)); con.commit(); c.close(); con.close()
    else:
        con=sql_conn(); con.execute("UPDATE keys SET deleted=1 WHERE key=?",(k,)); con.commit(); con.close()
    return jsonify({"ok":True})


@app.post("/assign-key")
def assign_key():
    d = request.get_json(silent=True) or {}
    aid = str(d.get("accountId", "")).strip()
    k = str(d.get("key", "")).strip()

    try:
        exp = normalize_exp_ms(d.get("exp"))
        maxd = max(1, min(3, int(d.get("maxDevices") or 1)))
    except Exception:
        return jsonify({"ok": False, "error": "exp/maxDevices không hợp lệ"}), 400

    if not aid or not k or not exp:
        return jsonify({"ok": False, "error": "Thiếu accountId/key/exp"}), 400

    try:
        if DATABASE_URL:
            con = pg()
            try:
                c = con.cursor()
                c.execute(
                    "SELECT * FROM accounts WHERE id=%s AND deleted=FALSE FOR UPDATE",
                    (aid,)
                )
                row = c.fetchone()
                if not row:
                    con.rollback()
                    return jsonify({"ok": False, "error": "Không tìm thấy tài khoản"}), 404

                cols = [x[0] for x in c.description]
                acc = dict(zip(cols, row))
                now = now_ms()

                c.execute("""INSERT INTO keys
                    (key,user_name,exp,max_devices,devices,accounts,deleted,created_at)
                    VALUES(%s,%s,%s,%s,'[]',%s,FALSE,%s)
                    ON CONFLICT(key) DO UPDATE SET
                      user_name=EXCLUDED.user_name,
                      exp=EXCLUDED.exp,
                      max_devices=EXCLUDED.max_devices,
                      accounts=EXCLUDED.accounts,
                      deleted=FALSE""",
                    (k, acc["name"], exp, maxd, json.dumps([aid]), now))

                c.execute(
                    "UPDATE accounts SET assigned_key=%s,max_devices=%s WHERE id=%s",
                    (k, maxd, aid)
                )

                # Verify inside the same transaction before commit.
                c.execute(
                    "SELECT assigned_key,max_devices FROM accounts WHERE id=%s",
                    (aid,)
                )
                arow = c.fetchone()
                c.execute(
                    "SELECT key,exp,max_devices,accounts FROM keys WHERE key=%s AND deleted=FALSE",
                    (k,)
                )
                krow = c.fetchone()

                if not arow or arow[0] != k or not krow or krow[0] != k:
                    con.rollback()
                    return jsonify({"ok": False, "error": "Server xác minh sau khi ghi thất bại"}), 500

                con.commit()
            finally:
                c.close()
                con.close()
        else:
            con = sql_conn()
            try:
                row = con.execute(
                    "SELECT * FROM accounts WHERE id=? AND deleted=0", (aid,)
                ).fetchone()
                if not row:
                    return jsonify({"ok": False, "error": "Không tìm thấy tài khoản"}), 404

                acc = dict(row)
                now = now_ms()
                con.execute("""INSERT OR REPLACE INTO keys
                    (key,user_name,exp,max_devices,devices,accounts,deleted,created_at)
                    VALUES(?,?,?,?, '[]',?,0,?)""",
                    (k, acc["name"], exp, maxd, json.dumps([aid]), now))
                con.execute(
                    "UPDATE accounts SET assigned_key=?,max_devices=? WHERE id=?",
                    (k, maxd, aid)
                )

                arow = con.execute(
                    "SELECT assigned_key FROM accounts WHERE id=?", (aid,)
                ).fetchone()
                krow = con.execute(
                    "SELECT key FROM keys WHERE key=? AND deleted=0", (k,)
                ).fetchone()

                if not arow or arow["assigned_key"] != k or not krow or krow["key"] != k:
                    con.rollback()
                    return jsonify({"ok": False, "error": "Server xác minh sau khi ghi thất bại"}), 500

                con.commit()
            finally:
                con.close()

        # Final read-back: this is what the frontend will consume.
        check = get_account_by_id(aid)
        if not check or check.get("assigned_key") != k:
            return jsonify({"ok": False, "error": "Đã ghi nhưng đọc lại không thấy key"}), 500

        key_row = get_key(k)
        if not key_row:
            return jsonify({"ok": False, "error": "Key không tồn tại sau khi cấp"}), 500

        return jsonify({
            "ok": True,
            "success": True,
            "key": k,
            "accountId": aid,
            "assignedKey": k,
            "assignedExp": int(key_row["exp"]),
            "maxDevices": int(key_row.get("max_devices") or maxd)
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post("/verify-key")
def verify_key():
    d=request.get_json(silent=True) or {}
    k=str(d.get("key","")).strip(); device=str(d.get("device","")).strip()
    row=get_key(k)
    if not row: return jsonify({"valid":False,"error":"Key không tồn tại"}),404
    exp=normalize_exp_ms(row["exp"]); maxd=int(row.get("max_devices") or 1)
    if exp and now_ms()>exp:
        return jsonify({"valid":False,"error":"Key đã hết hạn","expiry":exp})
    devices=json.loads(row.get("devices") or "[]")
    accounts=json.loads(row.get("accounts") or "[]")
    if device and device not in devices:
        if len(devices)>=maxd:
            return jsonify({"valid":False,"error":"Đã đủ số thiết bị","devicesUsed":len(devices),"maxDevices":maxd})
        devices.append(device)
    # Device count is global per key: different accounts using the same key
    # consume the same shared device slots.
    if DATABASE_URL:
        con=pg(); c=con.cursor(); c.execute("UPDATE keys SET devices=%s WHERE key=%s",(json.dumps(devices),k))
        con.commit(); c.close(); con.close()
    else:
        con=sql_conn(); con.execute("UPDATE keys SET devices=? WHERE key=?",(json.dumps(devices),k)); con.commit(); con.close()
    return jsonify({"valid":True,"devicesUsed":len(devices),"maxDevices":maxd,
                    "deviceCount":len(devices),"devices":devices,"key":k,
                    "expiry":exp,"exp":exp,"expiresAt":exp})


@app.get("/pricing")
def get_pricing():
    return jsonify(setting_get("pricing",DEFAULT_PRICING))


@app.post("/pricing")
def set_pricing():
    d=request.get_json(silent=True) or {}
    cur=setting_get("pricing",DEFAULT_PRICING)
    plans=d.get("plans") or d.get("pricingByDevices") or {}
    for n in ("1","2","3"):
        x=plans.get(n) or plans.get(int(n)) or {}
        if x:
            cur["plans"][n]={
                "base":int(x.get("base",cur["plans"][n]["base"])),
                "sale":int(x.get("sale",cur["plans"][n].get("sale",0))),
                "saleExpiry":x.get("saleExpiry",cur["plans"][n].get("saleExpiry"))
            }
    cur["saleEnabled"]=bool(d.get("saleEnabled",cur.get("saleEnabled",False)))
    setting_set("pricing",cur)
    saved = setting_get("pricing", None)
    if saved != cur:
        return jsonify({"ok":False,"error":"SALE chưa được lưu/xác minh trên server"}),500
    return jsonify({"ok":True, **saved})


@app.get("/bank-config")
def bank_config():
    return jsonify(setting_get("bankConfig",{}))


@app.post("/bank-config")
def set_bank_config():
    d=request.get_json(silent=True) or {}
    setting_set("bankConfig",d); return jsonify({"ok":True,"config":d})


DEFAULT_GAME_CONFIG = {
    "games": {
        "sunwin": True, "68game": True, "lc79": True, "ta28": True, "hitclub": True
    },
    "features": {"analysis": True, "gamelinks": True},
    "maintMsgs": {},
    "links": {
        "sunwin": "https://web.sunwin.pizza/?affId=Sunwin",
        "68game": "https://68gbcskh2.cfd/?code=25546789",
        "lc79": "https://lc79b.bet/",
        "ta28": "http://TA28.WORK",
        "hitclub": "https://hitclub.app/"
    }
}


def _merge_game_config(current, payload):
    cfg = dict(current or {})
    for section in ("games", "features", "maintMsgs", "links"):
        if section not in cfg or not isinstance(cfg.get(section), dict):
            cfg[section] = {}
    if isinstance(payload.get("links"), dict):
        for k, v in payload["links"].items():
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                cfg["links"][k] = v.strip()
    if isinstance(payload.get("games"), dict):
        cfg["games"].update(payload["games"])
    if isinstance(payload.get("features"), dict):
        cfg["features"].update(payload["features"])
    if isinstance(payload.get("maintMsgs"), dict):
        cfg["maintMsgs"].update(payload["maintMsgs"])
    # Support the existing admin toggle format: {game, enabled, maintMsg}
    if payload.get("game") is not None:
        game = str(payload.get("game")).strip()
        if game:
            cfg["games"][game] = bool(payload.get("enabled", True))
            if "maintMsg" in payload:
                cfg["maintMsgs"][game] = str(payload.get("maintMsg") or "")
    # Support the existing feature toggle format: {feature, enabled}
    if payload.get("feature") is not None:
        feat = str(payload.get("feature")).strip()
        if feat:
            cfg["features"][feat] = bool(payload.get("enabled", True))
    return cfg


@app.get("/api/game-config")
def get_game_config():
    cfg = setting_get("gameConfig", DEFAULT_GAME_CONFIG)
    cfg = _merge_game_config(DEFAULT_GAME_CONFIG, cfg)
    return jsonify(cfg)


@app.post("/api/game-config")
def set_game_config():
    payload = request.get_json(silent=True) or {}
    current = setting_get("gameConfig", DEFAULT_GAME_CONFIG)
    cfg = _merge_game_config(current, payload)
    try:
        setting_set("gameConfig", cfg)
        saved = setting_get("gameConfig", None)
        if not saved or saved.get("links") != cfg.get("links"):
            return jsonify({"ok": False, "error": "Không xác minh được cấu hình link sau khi lưu"}), 500
        return jsonify({"ok": True, **saved})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/inbox")
def inbox():
    return jsonify({"ok":True,"items":[]})


@app.get("/api")
def api_info():
    return jsonify({"ok":True,"apiBase":PUBLIC_API_URL,"endpoints":[
        "/", "/health", "/register", "/login", "/accounts", "/delete-account",
        "/create-key", "/keys", "/delete-key", "/assign-key", "/my-account", "/verify-key",
        "/pricing", "/api/status", "/bank-config", "/api/game-config", "/inbox"
    ]})


@app.errorhandler(404)
def not_found(_):
    return jsonify({"ok":False,"error":"Not Found"}),404


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))

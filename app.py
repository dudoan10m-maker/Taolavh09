import os, json, time, secrets, sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Production database configuration.
# Never hard-code PostgreSQL credentials in source code. Render injects
# DATABASE_URL from the PostgreSQL database defined in render.yaml.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
REQUIRE_POSTGRES = os.getenv("REQUIRE_POSTGRES", "false").strip().lower() in {
    "1", "true", "yes", "on"
}
SQLITE_PATH = os.getenv("SQLITE_PATH", "/tmp/toolmowis.db")
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "https://taolavh10.onrender.com").rstrip("/")

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
    """Create the database schema before the server starts.

    Render production uses PostgreSQL exclusively when REQUIRE_POSTGRES=true.
    A short retry loop handles the case where the database is still becoming
    available while the web service is starting.
    """
    if REQUIRE_POSTGRES and not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is missing. Attach the Render PostgreSQL database "
            "and expose its connectionString as DATABASE_URL."
        )

    if DATABASE_URL:
        last_error = None
        for attempt in range(1, 6):
            con = None
            cur = None
            try:
                con = pg()
                cur = con.cursor()
                cur.execute("""CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS accounts(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL, assigned_key TEXT, max_devices INTEGER NOT NULL DEFAULT 1,
                    deleted BOOLEAN NOT NULL DEFAULT FALSE, created_at BIGINT NOT NULL)""")
                cur.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS balance BIGINT NOT NULL DEFAULT 0")
                cur.execute("""CREATE TABLE IF NOT EXISTS deposits(
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, amount BIGINT NOT NULL,
                    content TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
                    created_at BIGINT NOT NULL, approved_at BIGINT, rejected_at BIGINT)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS keys(
                    key TEXT PRIMARY KEY, user_name TEXT, exp BIGINT NOT NULL,
                    max_devices INTEGER NOT NULL DEFAULT 1, devices TEXT NOT NULL DEFAULT '[]',
                    accounts TEXT NOT NULL DEFAULT '[]', deleted BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at BIGINT NOT NULL)""")
                con.commit()
                return
            except Exception as e:
                last_error = e
                if con:
                    try:
                        con.rollback()
                    except Exception:
                        pass
                if attempt < 5:
                    time.sleep(2)
            finally:
                if cur:
                    try:
                        cur.close()
                    except Exception:
                        pass
                if con:
                    try:
                        con.close()
                    except Exception:
                        pass
        raise RuntimeError(f"PostgreSQL initialization failed: {last_error}") from last_error

    if REQUIRE_POSTGRES:
        raise RuntimeError("PostgreSQL is required but DATABASE_URL is empty.")

    # Local development fallback only. Render production sets REQUIRE_POSTGRES=true.
    con = sql_conn(); c = con.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS accounts(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL, assigned_key TEXT, max_devices INTEGER NOT NULL DEFAULT 1,
        deleted INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)""")
    cols = [r[1] for r in c.execute("PRAGMA table_info(accounts)").fetchall()]
    if "balance" not in cols:
        c.execute("ALTER TABLE accounts ADD COLUMN balance INTEGER NOT NULL DEFAULT 0")
    c.execute("""CREATE TABLE IF NOT EXISTS deposits(
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL, amount INTEGER NOT NULL,
        content TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
        created_at INTEGER NOT NULL, approved_at INTEGER, rejected_at INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS keys(
        key TEXT PRIMARY KEY, user_name TEXT, exp INTEGER NOT NULL,
        max_devices INTEGER NOT NULL DEFAULT 1, devices TEXT NOT NULL DEFAULT '[]',
        accounts TEXT NOT NULL DEFAULT '[]', deleted INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL)""")
    con.commit(); c.close(); con.close()


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
        "balance": int(r.get("balance") or 0),
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
        exp = int(d.get("exp") or 0)
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
        exp = int(d.get("exp") or 0)
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
    exp=int(row["exp"]); maxd=int(row.get("max_devices") or 1)
    if exp and now_ms()>exp: return jsonify({"valid":False,"error":"Key đã hết hạn"})
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
                    "deviceCount":len(devices),"devices":devices,"key":k})


# ---------------- WALLET / DEPOSIT ----------------
def deposit_json(row):
    if not row: return None
    r=dict(row) if not isinstance(row,dict) else row
    return {"id":str(r["id"]),"depositId":str(r["id"]),"orderId":str(r["id"]),
            "accountId":str(r["account_id"]),"amount":int(r["amount"]),
            "content":r.get("content") or "","status":r.get("status") or "pending",
            "createdAt":int(r["created_at"]),"approvedAt":int(r["approved_at"]) if r.get("approved_at") else None,
            "rejectedAt":int(r["rejected_at"]) if r.get("rejected_at") else None}

def get_deposit(deposit_id):
    if DATABASE_URL:
        con=pg(); c=con.cursor(); c.execute("SELECT * FROM deposits WHERE id=%s",(deposit_id,)); row=c.fetchone()
        cols=[d[0] for d in c.description] if row else []
        c.close(); con.close(); return dict(zip(cols,row)) if row else None
    con=sql_conn(); row=con.execute("SELECT * FROM deposits WHERE id=?",(deposit_id,)).fetchone(); con.close()
    return dict(row) if row else None

@app.post("/create-deposit")
def create_deposit():
    d=request.get_json(silent=True) or {}; aid=str(d.get("accountId") or d.get("account_id") or "").strip()
    content=str(d.get("content") or d.get("transferContent") or "").strip()
    try: amount=int(d.get("amount") or 0)
    except Exception: amount=0
    if not aid or amount<=0: return jsonify({"ok":False,"success":False,"error":"Thiếu accountId hoặc số tiền không hợp lệ"}),400
    if not get_account_by_id(aid): return jsonify({"ok":False,"success":False,"error":"Không tìm thấy tài khoản"}),404
    did=str(d.get("depositId") or d.get("orderId") or ("DEP-"+secrets.token_hex(8).upper())); created=now_ms()
    if DATABASE_URL:
        con=pg(); c=con.cursor()
        try:
            c.execute("""INSERT INTO deposits(id,account_id,amount,content,status,created_at) VALUES(%s,%s,%s,%s,'pending',%s) ON CONFLICT(id) DO NOTHING""",(did,aid,amount,content,created))
            c.execute("SELECT * FROM deposits WHERE id=%s",(did,)); row=c.fetchone(); cols=[x[0] for x in c.description]; con.commit()
        finally: c.close(); con.close()
        return jsonify({"ok":True,"success":True,"deposit":deposit_json(dict(zip(cols,row)))})
    con=sql_conn()
    try:
        con.execute("INSERT OR IGNORE INTO deposits(id,account_id,amount,content,status,created_at) VALUES(?,?,?,?,?,?)",(did,aid,amount,content,"pending",created))
        row=con.execute("SELECT * FROM deposits WHERE id=?",(did,)).fetchone(); con.commit()
    finally: con.close()
    return jsonify({"ok":True,"success":True,"deposit":deposit_json(dict(row))})

@app.get("/deposits")
def deposits_list():
    status=str(request.args.get("status","pending")).strip().lower()
    if status not in {"","pending","completed","rejected"}: return jsonify({"ok":False,"error":"status không hợp lệ"}),400
    if DATABASE_URL:
        con=pg(); c=con.cursor()
        try:
            q="""SELECT d.*,a.name account_name,a.email account_email,COALESCE(a.balance,0) account_balance FROM deposits d LEFT JOIN accounts a ON a.id=d.account_id"""
            if status: q+=" WHERE d.status=%s"; c.execute(q+" ORDER BY d.created_at DESC",(status,))
            else: c.execute(q+" ORDER BY d.created_at DESC")
            rows=c.fetchall(); cols=[x[0] for x in c.description]
        finally: c.close(); con.close()
        arr=[]
        for row in rows:
            r=dict(zip(cols,row)); x=deposit_json(r); x.update({"accountName":r.get("account_name") or "","accountEmail":r.get("account_email") or "","accountBalance":int(r.get("account_balance") or 0)}); arr.append(x)
    else:
        con=sql_conn()
        try:
            q="""SELECT d.*,a.name account_name,a.email account_email,COALESCE(a.balance,0) account_balance FROM deposits d LEFT JOIN accounts a ON a.id=d.account_id"""
            rows=con.execute(q+(" WHERE d.status=?" if status else "")+" ORDER BY d.created_at DESC",((status,) if status else ())).fetchall()
        finally: con.close()
        arr=[]
        for row in rows:
            r=dict(row); x=deposit_json(r); x.update({"accountName":r.get("account_name") or "","accountEmail":r.get("account_email") or "","accountBalance":int(r.get("account_balance") or 0)}); arr.append(x)
    return jsonify({"ok":True,"deposits":arr,"items":arr,"count":len(arr)})

@app.post("/approve-deposit")
def approve_deposit():
    d=request.get_json(silent=True) or {}; did=str(d.get("depositId") or d.get("orderId") or d.get("id") or "").strip()
    try: amount_override=int(d.get("amount")) if d.get("amount") not in (None,"") else None
    except Exception: return jsonify({"ok":False,"error":"Số tiền không hợp lệ"}),400
    if not did: return jsonify({"ok":False,"error":"Thiếu depositId/orderId"}),400
    try:
        if DATABASE_URL:
            con=pg(); c=con.cursor()
            try:
                c.execute("SELECT * FROM deposits WHERE id=%s FOR UPDATE",(did,)); row=c.fetchone()
                if not row: con.rollback(); return jsonify({"ok":False,"error":"Không tìm thấy giao dịch"}),404
                cols=[x[0] for x in c.description]; dep=dict(zip(cols,row))
                if dep["status"]=="completed":
                    c.execute("SELECT balance FROM accounts WHERE id=%s",(dep["account_id"],)); a=c.fetchone(); con.rollback()
                    return jsonify({"ok":True,"success":True,"alreadyProcessed":True,"balance":int(a[0] or 0)})
                if dep["status"]!="pending": con.rollback(); return jsonify({"ok":False,"error":"Giao dịch không còn chờ"}),409
                amount=int(amount_override if amount_override is not None else dep["amount"])
                if amount<=0: con.rollback(); return jsonify({"ok":False,"error":"Số tiền phải > 0"}),400
                c.execute("UPDATE deposits SET amount=%s,status='completed',approved_at=%s WHERE id=%s",(amount,now_ms(),did))
                c.execute("UPDATE accounts SET balance=COALESCE(balance,0)+%s WHERE id=%s AND deleted=FALSE RETURNING balance",(amount,dep["account_id"]))
                a=c.fetchone()
                if not a: con.rollback(); return jsonify({"ok":False,"error":"Tài khoản không tồn tại"}),404
                con.commit(); balance=int(a[0] or 0)
            finally: c.close(); con.close()
        else:
            con=sql_conn()
            try:
                con.execute("BEGIN IMMEDIATE"); row=con.execute("SELECT * FROM deposits WHERE id=?",(did,)).fetchone()
                if not row: con.rollback(); return jsonify({"ok":False,"error":"Không tìm thấy giao dịch"}),404
                dep=dict(row)
                if dep["status"]=="completed":
                    a=con.execute("SELECT balance FROM accounts WHERE id=?",(dep["account_id"],)).fetchone(); con.rollback(); return jsonify({"ok":True,"success":True,"alreadyProcessed":True,"balance":int(a["balance"] or 0)})
                if dep["status"]!="pending": con.rollback(); return jsonify({"ok":False,"error":"Giao dịch không còn chờ"}),409
                amount=int(amount_override if amount_override is not None else dep["amount"])
                if amount<=0: con.rollback(); return jsonify({"ok":False,"error":"Số tiền phải > 0"}),400
                con.execute("UPDATE deposits SET amount=?,status='completed',approved_at=? WHERE id=?",(amount,now_ms(),did))
                cur=con.execute("UPDATE accounts SET balance=COALESCE(balance,0)+? WHERE id=? AND deleted=0",(amount,dep["account_id"]))
                if cur.rowcount!=1: con.rollback(); return jsonify({"ok":False,"error":"Tài khoản không tồn tại"}),404
                a=con.execute("SELECT balance FROM accounts WHERE id=?",(dep["account_id"],)).fetchone(); con.commit(); balance=int(a["balance"] or 0)
            finally: con.close()
        return jsonify({"ok":True,"success":True,"deposit":deposit_json(get_deposit(did)),"balance":balance})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

@app.post("/manual-deposit")
def manual_deposit():
    """Admin manually credits an account and records the credit as completed."""
    d=request.get_json(silent=True) or {}
    ident=str(d.get("account") or d.get("accountId") or d.get("email") or "").strip()
    content=str(d.get("content") or "ADMIN MANUAL").strip()
    try: amount=int(d.get("amount") or 0)
    except Exception: amount=0
    if not ident or amount<=0: return jsonify({"ok":False,"error":"Thiếu tài khoản hoặc số tiền không hợp lệ"}),400

    # Resolve by account ID first, then email.
    acc=get_account_by_id(ident)
    if not acc:
        if DATABASE_URL:
            con=pg(); c=con.cursor()
            try:
                c.execute("SELECT * FROM accounts WHERE email=%s AND deleted=FALSE",(ident,)); row=c.fetchone()
                cols=[x[0] for x in c.description] if row else []
            finally: c.close(); con.close()
            acc=dict(zip(cols,row)) if row else None
        else:
            con=sql_conn(); row=con.execute("SELECT * FROM accounts WHERE email=? AND deleted=0",(ident,)).fetchone(); con.close()
            acc=dict(row) if row else None
    if not acc: return jsonify({"ok":False,"error":"Không tìm thấy tài khoản"}),404

    did="MANUAL-"+secrets.token_hex(7).upper(); created=now_ms()
    try:
        if DATABASE_URL:
            con=pg(); c=con.cursor()
            try:
                c.execute("UPDATE accounts SET balance=COALESCE(balance,0)+%s WHERE id=%s AND deleted=FALSE RETURNING balance",(amount,acc["id"]))
                row=c.fetchone()
                if not row: con.rollback(); return jsonify({"ok":False,"error":"Tài khoản không tồn tại"}),404
                balance=int(row[0] or 0)
                c.execute("""INSERT INTO deposits(id,account_id,amount,content,status,created_at,approved_at)
                             VALUES(%s,%s,%s,%s,'completed',%s,%s)""",(did,acc["id"],amount,content,created,created))
                con.commit()
            finally: c.close(); con.close()
        else:
            con=sql_conn()
            try:
                con.execute("BEGIN IMMEDIATE")
                cur=con.execute("UPDATE accounts SET balance=COALESCE(balance,0)+? WHERE id=? AND deleted=0",(amount,acc["id"]))
                if cur.rowcount!=1: con.rollback(); return jsonify({"ok":False,"error":"Tài khoản không tồn tại"}),404
                row=con.execute("SELECT balance FROM accounts WHERE id=?",(acc["id"],)).fetchone(); balance=int(row["balance"] or 0)
                con.execute("""INSERT INTO deposits(id,account_id,amount,content,status,created_at,approved_at)
                              VALUES(?,?,?,?,?, ?, ?)""",(did,acc["id"],amount,content,"completed",created,created))
                con.commit()
            finally: con.close()
        return jsonify({"ok":True,"success":True,"depositId":did,"accountId":acc["id"],"amount":amount,"balance":balance})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500

@app.post("/reject-deposit")
def reject_deposit():
    d=request.get_json(silent=True) or {}; did=str(d.get("depositId") or d.get("orderId") or d.get("id") or "").strip()
    if not did: return jsonify({"ok":False,"error":"Thiếu depositId/orderId"}),400
    if DATABASE_URL:
        con=pg(); c=con.cursor()
        try:
            c.execute("UPDATE deposits SET status='rejected',rejected_at=%s WHERE id=%s AND status='pending' RETURNING id",(now_ms(),did)); ok=c.fetchone(); con.commit()
        finally: c.close(); con.close()
    else:
        con=sql_conn()
        try: cur=con.execute("UPDATE deposits SET status='rejected',rejected_at=? WHERE id=? AND status='pending'",(now_ms(),did)); con.commit(); ok=cur.rowcount==1
        finally: con.close()
    if not ok: return jsonify({"ok":False,"error":"Không tìm thấy giao dịch chờ duyệt"}),404
    return jsonify({"ok":True,"success":True,"deposit":deposit_json(get_deposit(did))})

@app.get("/deposit-status")
def deposit_status():
    did=str(request.args.get("orderId") or request.args.get("depositId") or "").strip(); aid=str(request.args.get("accountId") or "").strip()
    dep=get_deposit(did) if did else None
    if not dep: return jsonify({"ok":False,"success":False,"status":"not_found"}),404
    if aid and str(dep["account_id"])!=aid: return jsonify({"ok":False,"success":False,"error":"Không khớp tài khoản"}),403
    out={"ok":True,"success":dep["status"]=="completed","status":dep["status"],"deposit":deposit_json(dep)}
    if dep["status"]=="completed": out["amount"]=int(dep["amount"]); out["balance"]=int((get_account_by_id(str(dep["account_id"])) or {}).get("balance") or 0)
    return jsonify(out)

@app.get("/wallet")
def wallet():
    aid=str(request.args.get("accountId") or request.args.get("id") or "").strip(); acc=get_account_by_id(aid) if aid else None
    if not acc: return jsonify({"ok":False,"error":"Không tìm thấy tài khoản"}),404
    bal=int(acc.get("balance") or 0); return jsonify({"ok":True,"success":True,"accountId":aid,"balance":bal,"walletBalance":bal})

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

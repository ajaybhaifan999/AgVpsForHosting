#!/usr/bin/env python3
"""
Agajay VPS Panel - Professional VPS-like file runner for Railway
Owner: Agajayofficial / agajay
"""
import os, json, uuid, shutil, subprocess, threading, time, signal, queue, sys
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, request, jsonify, render_template, redirect,
                   url_for, session, send_from_directory, Response, stream_with_context, abort)
from werkzeug.utils import secure_filename

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
UPLOAD_DIR = os.path.join(BASE, "uploads")
LOG_DIR = os.path.join(BASE, "logs")
for d in (DATA_DIR, UPLOAD_DIR, LOG_DIR):
    os.makedirs(d, exist_ok=True)

USERS_FILE = os.path.join(DATA_DIR, "users.json")
PLANS_FILE = os.path.join(DATA_DIR, "plans.json")

OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "Agajayofficial")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "agajay")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "agajay-vps-secret-change-me")
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB per upload

# ---------- storage helpers ----------
def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)

def load_users():
    return _load(USERS_FILE, {})

def save_users(u):
    _save(USERS_FILE, u)

def load_plans():
    return _load(PLANS_FILE, {
        "starter":  {"name": "Starter",  "price": "$2/mo",  "files": 5,   "days": 30,  "ram": "512MB"},
        "pro":      {"name": "Pro",      "price": "$5/mo",  "files": 25,  "days": 30,  "ram": "1GB"},
        "business": {"name": "Business", "price": "$10/mo", "files": 100, "days": 30,  "ram": "2GB"},
        "lifetime": {"name": "Lifetime", "price": "$49",    "files": 500, "days": 3650,"ram": "4GB"},
    })

# ---------- process manager ----------
class ProcessManager:
    def __init__(self):
        self.procs = {}      # file_id -> Popen
        self.logs  = {}      # file_id -> list[str]
        self.subs  = {}      # file_id -> list[queue.Queue]
        self.lock  = threading.Lock()

    def _emit(self, fid, line):
        with self.lock:
            buf = self.logs.setdefault(fid, [])
            buf.append(line)
            if len(buf) > 2000:
                del buf[:1000]
            for q in list(self.subs.get(fid, [])):
                try: q.put_nowait(line)
                except Exception: pass

    def _reader(self, fid, proc):
        try:
            for raw in iter(proc.stdout.readline, b""):
                try: line = raw.decode("utf-8", "replace").rstrip()
                except Exception: line = str(raw)
                self._emit(fid, line)
        finally:
            proc.wait()
            self._emit(fid, f"[process exited code={proc.returncode}]")

    def is_running(self, fid):
        p = self.procs.get(fid)
        return bool(p and p.poll() is None)

    def start(self, fid, cmd, cwd):
        if self.is_running(fid):
            return False, "Already running"
        self._emit(fid, f"$ {' '.join(cmd)}")
        try:
            p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, bufsize=1)
        except FileNotFoundError as e:
            self._emit(fid, f"[error] {e}")
            return False, str(e)
        self.procs[fid] = p
        threading.Thread(target=self._reader, args=(fid, p), daemon=True).start()
        return True, "started"

    def stop(self, fid):
        p = self.procs.get(fid)
        if not p or p.poll() is not None:
            return False
        try:
            p.terminate()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill()
        except Exception: pass
        return True

    def get_logs(self, fid):
        return list(self.logs.get(fid, []))

    def subscribe(self, fid):
        q = queue.Queue()
        with self.lock:
            self.subs.setdefault(fid, []).append(q)
        return q

    def unsubscribe(self, fid, q):
        with self.lock:
            if q in self.subs.get(fid, []):
                self.subs[fid].remove(q)

PM = ProcessManager()

# ---------- auth ----------
def current_user():
    u = session.get("user")
    if not u: return None
    if u.get("role") == "owner":
        return u
    users = load_users()
    rec = users.get(u["username"])
    if not rec: return None
    # check expiry
    try:
        exp = datetime.fromisoformat(rec["expires_at"])
        if datetime.utcnow() > exp:
            return None
    except Exception:
        pass
    return {"username": u["username"], "role": "user", "record": rec}

def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not current_user():
            return redirect(url_for("login"))
        return f(*a, **kw)
    return w

def owner_required(f):
    @wraps(f)
    def w(*a, **kw):
        u = current_user()
        if not u or u.get("role") != "owner":
            return redirect(url_for("login"))
        return f(*a, **kw)
    return w

def user_dir(username):
    p = os.path.join(UPLOAD_DIR, secure_filename(username))
    os.makedirs(p, exist_ok=True)
    return p

# ---------- routes ----------
@app.route("/")
def index():
    return redirect(url_for("dashboard") if current_user() else url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    err = None
    if request.method == "POST":
        un = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        if un == OWNER_USERNAME and pw == OWNER_PASSWORD:
            session["user"] = {"username": un, "role": "owner"}
            return redirect(url_for("owner_panel"))
        users = load_users()
        rec = users.get(un)
        if rec and rec.get("password") == pw:
            try:
                exp = datetime.fromisoformat(rec["expires_at"])
                if datetime.utcnow() > exp:
                    err = "Account expired. Contact owner."
                else:
                    session["user"] = {"username": un, "role": "user"}
                    return redirect(url_for("dashboard"))
            except Exception:
                err = "Invalid account"
        else:
            err = "Invalid username or password"
    return render_template("login.html", error=err)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/pricing")
def pricing():
    return render_template("pricing.html", plans=load_plans(), user=current_user())

# ---------- owner ----------
@app.route("/owner")
@owner_required
def owner_panel():
    return render_template("owner.html", users=load_users(), plans=load_plans())

@app.route("/owner/users", methods=["POST"])
@owner_required
def owner_create_user():
    d = request.get_json(force=True)
    un = (d.get("username") or "").strip()
    pw = (d.get("password") or "").strip()
    days = int(d.get("days") or 30)
    files = int(d.get("files") or 10)
    if not un or not pw:
        return jsonify(ok=False, error="username and password required"), 400
    if un == OWNER_USERNAME:
        return jsonify(ok=False, error="reserved username"), 400
    users = load_users()
    users[un] = {
        "username": un,
        "password": pw,
        "days": days,
        "file_limit": files,
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() + timedelta(days=days)).isoformat(),
    }
    save_users(users)
    return jsonify(ok=True, user=users[un])

@app.route("/owner/users/<username>", methods=["DELETE"])
@owner_required
def owner_delete_user(username):
    users = load_users()
    if username in users:
        del users[username]
        save_users(users)
        shutil.rmtree(user_dir(username), ignore_errors=True)
    return jsonify(ok=True)

@app.route("/owner/users/<username>/extend", methods=["POST"])
@owner_required
def owner_extend(username):
    d = request.get_json(force=True)
    days = int(d.get("days") or 30)
    users = load_users()
    if username not in users:
        return jsonify(ok=False), 404
    try:
        cur = datetime.fromisoformat(users[username]["expires_at"])
    except Exception:
        cur = datetime.utcnow()
    base = max(cur, datetime.utcnow())
    users[username]["expires_at"] = (base + timedelta(days=days)).isoformat()
    save_users(users)
    return jsonify(ok=True, user=users[username])

@app.route("/owner/plans", methods=["POST"])
@owner_required
def owner_save_plans():
    plans = request.get_json(force=True)
    _save(PLANS_FILE, plans)
    return jsonify(ok=True)

# ---------- user dashboard ----------
@app.route("/dashboard")
@login_required
def dashboard():
    u = current_user()
    if u["role"] == "owner":
        return redirect(url_for("owner_panel"))
    d = user_dir(u["username"])
    files = []
    for name in sorted(os.listdir(d)):
        full = os.path.join(d, name)
        if os.path.isfile(full):
            fid = name
            files.append({
                "id": fid, "name": name,
                "size": os.path.getsize(full),
                "running": PM.is_running(fid_for(u["username"], name)),
            })
    rec = u["record"]
    return render_template("dashboard.html", user=u, files=files, record=rec)

def fid_for(username, filename):
    return f"{username}::{filename}"

@app.route("/api/upload", methods=["POST"])
@login_required
def upload():
    u = current_user()
    if u["role"] == "owner":
        return jsonify(ok=False, error="owner uses panel"), 400
    d = user_dir(u["username"])
    limit = int(u["record"].get("file_limit", 10))
    existing = len([f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))])
    saved = []
    for f in request.files.getlist("files"):
        if not f or not f.filename: continue
        if existing >= limit:
            return jsonify(ok=False, error=f"File limit reached ({limit})"), 400
        name = secure_filename(f.filename)
        f.save(os.path.join(d, name))
        existing += 1
        saved.append(name)
    return jsonify(ok=True, saved=saved)

@app.route("/api/delete/<path:name>", methods=["DELETE"])
@login_required
def delete_file(name):
    u = current_user()
    if u["role"] == "owner": return jsonify(ok=False), 400
    name = secure_filename(name)
    p = os.path.join(user_dir(u["username"]), name)
    fid = fid_for(u["username"], name)
    PM.stop(fid)
    if os.path.exists(p): os.remove(p)
    return jsonify(ok=True)

@app.route("/api/run/<path:name>", methods=["POST"])
@login_required
def run_file(name):
    u = current_user()
    if u["role"] == "owner": return jsonify(ok=False), 400
    name = secure_filename(name)
    p = os.path.join(user_dir(u["username"]), name)
    if not os.path.exists(p):
        return jsonify(ok=False, error="not found"), 404
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext == "py":     cmd = [sys.executable, "-u", p]
    elif ext == "js":   cmd = ["node", p]
    elif ext == "sh":   cmd = ["bash", p]
    elif ext == "ts":   cmd = ["npx", "-y", "tsx", p]
    else:
        return jsonify(ok=False, error=f"Unsupported file type: .{ext}"), 400
    fid = fid_for(u["username"], name)
    ok, msg = PM.start(fid, cmd, cwd=user_dir(u["username"]))
    return jsonify(ok=ok, message=msg)

@app.route("/api/stop/<path:name>", methods=["POST"])
@login_required
def stop_file(name):
    u = current_user()
    if u["role"] == "owner": return jsonify(ok=False), 400
    name = secure_filename(name)
    PM.stop(fid_for(u["username"], name))
    return jsonify(ok=True)

@app.route("/api/logs/<path:name>")
@login_required
def get_logs(name):
    u = current_user()
    if u["role"] == "owner": return jsonify(ok=False), 400
    name = secure_filename(name)
    return jsonify(ok=True, logs=PM.get_logs(fid_for(u["username"], name)),
                   running=PM.is_running(fid_for(u["username"], name)))

@app.route("/api/stream/<path:name>")
@login_required
def stream_logs(name):
    u = current_user()
    if u["role"] == "owner": abort(400)
    name = secure_filename(name)
    fid = fid_for(u["username"], name)
    q = PM.subscribe(fid)
    @stream_with_context
    def gen():
        try:
            for line in PM.get_logs(fid):
                yield f"data: {line}\n\n"
            while True:
                try:
                    line = q.get(timeout=15)
                    yield f"data: {line}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            PM.unsubscribe(fid, q)
    return Response(gen(), mimetype="text/event-stream")

@app.route("/api/install", methods=["POST"])
@login_required
def install_pkg():
    u = current_user()
    if u["role"] == "owner": return jsonify(ok=False), 400
    d = request.get_json(force=True)
    manager = d.get("manager", "pip")
    module = (d.get("module") or "").strip()
    if not module or not all(c.isalnum() or c in "-_.=<>" for c in module):
        return jsonify(ok=False, error="invalid module name"), 400
    if manager == "pip":
        cmd = [sys.executable, "-m", "pip", "install", module]
    elif manager == "pkg":
        # Railway/nix: fall back to apt-get if pkg missing
        cmd = ["bash", "-lc", f"command -v pkg >/dev/null && pkg install -y {module} || apt-get install -y {module} || npm install -g {module}"]
    elif manager == "npm":
        cmd = ["npm", "install", "-g", module]
    else:
        return jsonify(ok=False, error="unknown manager"), 400
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return jsonify(ok=out.returncode == 0,
                       stdout=out.stdout[-4000:], stderr=out.stderr[-4000:])
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.route("/healthz")
def healthz():
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)

import logging
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from .. import db, enablebanking, licence, sync

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")

CONTAINER_NAME = "klartion"
IMAGE_NAME = "daalves/klartion:latest"

@app.before_request
def load_secret_key():
    from .. import config as cfg
    import os
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

def _cfg():
    from .. import config
    return config

def _is_configured():
    return _cfg().is_configured()

def _is_connected():
    return db.get_tokens() is not None

@app.route("/")
def index():
    if not _is_configured():
        return redirect(url_for("setup_licence"))
    if not _is_connected():
        return redirect(url_for("connect"))
    return redirect(url_for("status"))

@app.route("/setup", methods=["GET", "POST"])
def setup_licence():
    error = None
    if request.method == "POST":
        key = request.form.get("license_key", "").strip()
        result = licence.activate(key)
        if not result["valid"] and not result.get("offline"):
            error = result["error"] or "Invalid license key."
        else:
            _cfg().set("LICENCE_KEY", key)
            return redirect(url_for("setup_notion"))
    return render_template("setup_licence.html",
        error=error,
        licence_key=_cfg().LICENCE_KEY,
    )


@app.route("/setup/bank", methods=["GET", "POST"])
def setup_bank():
    import glob, os
    error = None
    if request.method == "POST":
        app_id   = request.form.get("eb_app_id", "").strip()
        pem_file = request.files.get("pem_file")
        existing_pem = glob.glob("/app/data/*.pem")
        if not app_id:
            error = "Application ID is required."
        elif not pem_file or not pem_file.filename:
            if not existing_pem:
                error = "Private key file is required."
            else:
                _cfg().set("EB_APP_ID", app_id)
                return redirect(url_for("setup_notion"))
        else:
            pem_path = os.path.join("/app/data", f"{app_id}.pem")
            pem_file.save(pem_path)
            _cfg().set("EB_APP_ID", app_id)
            return redirect(url_for("setup_notion"))
    pem_exists = bool(glob.glob("/app/data/*.pem"))
    return render_template("setup_bank.html",
        error=error,
        eb_app_id=_cfg().EB_APP_ID,
        pem_uploaded=pem_exists,
        active="bank",
    )
@app.route("/setup/notion", methods=["GET", "POST"])
def setup_notion():
    error = None
    if request.method == "POST":
        api_key = request.form.get("notion_api_key", "").strip()
        db_id   = request.form.get("notion_database_id", "").strip()
        # Extract database ID from full Notion URL if pasted
        import re
        m = re.search(r'([a-f0-9]{32})', db_id.replace('-', ''))
        if m:
            db_id = m.group(1)
        if not api_key or not db_id:
            error = "Both fields are required."
        else:
            try:
                from ..notion import verify_database
                _cfg().set("NOTION_API_KEY", api_key)
                _cfg().set("NOTION_DATABASE_ID", db_id)
                if not verify_database():
                    error = "Could not access that Notion database. Check the key and database ID, and make sure the integration is connected to the database."
                    _cfg().set("NOTION_API_KEY", "")
                    _cfg().set("NOTION_DATABASE_ID", "")
                else:
                    return redirect(url_for("setup_notifications"))
            except Exception as e:
                error = f"Notion connection failed: {e}"
    return render_template("setup_notion.html",
        error=error,
        notion_api_key=_cfg().NOTION_API_KEY,
        notion_database_id=_cfg().NOTION_DATABASE_ID,
        is_configured=_is_configured(),
    )

@app.route("/setup/notifications", methods=["GET", "POST"])
def setup_notifications():
    error = None
    if request.method == "POST":
        klartion_url = request.form.get("klartion_url", "").strip().rstrip("/")
        email        = request.form.get("notify_email", "").strip()
        smtp_user    = request.form.get("smtp_user", "").strip()
        smtp_pass    = request.form.get("smtp_password", "").strip()
        if not klartion_url or not email or not smtp_user or not smtp_pass:
            error = "All fields are required."
        else:
            _cfg().set("KLARTION_URL",   klartion_url)
            _cfg().set("NOTIFY_EMAIL",   email)
            _cfg().set("SMTP_USER",      smtp_user)
            _cfg().set("SMTP_PASSWORD",  smtp_pass)
            return redirect(url_for("setup_sync"))
    return render_template("setup_notifications.html",
        error=error,
        klartion_url=_cfg().KLARTION_URL,
        notify_email=_cfg().NOTIFY_EMAIL,
        smtp_user=_cfg().SMTP_USER,
        smtp_password=_cfg().SMTP_PASSWORD,
    )

@app.route("/setup/sync", methods=["GET", "POST"])
def setup_sync():
    error = None
    if request.method == "POST":
        sync_time = request.form.get("sync_time", "08:00").strip()
        sync_frequency = request.form.get("sync_frequency", "24").strip()
        _cfg().set("SYNC_TIME", sync_time)
        _cfg().set("SYNC_FREQUENCY", sync_frequency)
        _start_scheduler_if_ready()
        return redirect(url_for("connect"))
    return render_template("setup_sync.html",
        error=error,
        sync_time=_cfg().SYNC_TIME or "08:00",
        sync_frequency=_cfg().SYNC_FREQUENCY if hasattr(_cfg(), 'SYNC_FREQUENCY') else "24",
        is_configured=_is_configured(),
    )

@app.route("/settings/deactivate", methods=["POST"])
def deactivate_licence():
    from .. import licence
    result = licence.deactivate()
    if result["success"]:
        _cfg().set("LICENCE_KEY", "")
        return redirect(url_for("setup_licence") + "?msg=deactivated")
    return redirect(url_for("status") + "?error=" + (result["error"] or "Deactivation failed."))

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/last-sync")
def last_sync_api():
    return jsonify({"ran_at": db.get_last_sync() or ""})

@app.route("/api/bank-status")
def bank_status():
    return jsonify({"connected": db.get_tokens() is not None})

@app.route("/api/detect-url")
def detect_url():
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host   = request.headers.get("X-Forwarded-Host", request.host)
    return jsonify({"url": f"{scheme}://{host}"})

@app.route("/connect", methods=["GET", "POST"])
def connect():
    error    = None
    auth_url = None
    pending  = db.get_setting("pending_bank_name")

    if request.method == "POST":
        action = request.form.get("action")
        if action == "upload_pem":
            import os
            pem_file = request.files.get("pem_file")
            app_id   = request.form.get("eb_app_id", "").strip()
            if not pem_file or not pem_file.filename:
                error = "Please select a .pem file."
            elif not app_id:
                error = "Application ID is required."
            else:
                pem_path = os.path.join("/app/data", f"{app_id}.pem")
                pem_file.save(pem_path)
                _cfg().set("EB_APP_ID", app_id)
                return redirect(url_for("connect"))
        elif action == "start":
            bank_name    = request.form.get("bank_name", "").strip()
            bank_country = request.form.get("bank_country", "").strip()
            if not bank_name or not bank_country:
                error = "Please select a bank."
            else:
                try:
                    result   = enablebanking.start_auth(bank_name, bank_country)
                    auth_url = result["url"]
                    pending  = bank_name
                except Exception as e:
                    logger.error("Failed to start auth: %s", e)
                    error = f"Could not start bank connection: {e}"
        elif action == "cancel":
            db.set_setting("pending_session_id", "")
            db.set_setting("pending_bank_name", "")
            db.set_setting("pending_bank_country", "")
            return redirect(url_for("connect"))

    import glob
    all_tokens = db.get_all_tokens()
    tokens     = db.get_tokens()  # for backwards compat
    success    = request.args.get("success")
    pem_ready  = bool(glob.glob("/app/data/*.pem"))

    # Fetch bank account limit from licence API
    bank_account_limit = 2
    try:
        import requests as _requests
        key = _cfg().LICENCE_KEY
        if key:
            resp = _requests.post("https://api.klartion.com/info", json={"license_key": key}, timeout=5)
            if resp.status_code == 200:
                bank_account_limit = resp.json().get("bank_account_limit", 2)
    except Exception:
        pass

    return render_template("connect.html",
        error=error,
        success=success,
        auth_url=auth_url,
        tokens=tokens,
        all_tokens=all_tokens,
        pending_bank=pending,
        sync_time=_cfg().SYNC_TIME,
        pem_ready=pem_ready,
        eb_app_id=_cfg().EB_APP_ID,
        bank_account_limit=bank_account_limit,
        bank_slot_url=f"https://buy.stripe.com/4gM9AMg348nt2Y7185cMM04?client_reference_id={_cfg().LICENCE_KEY}",
    )

@app.route("/connect/reauthorise", methods=["POST"])
def reauthorise():
    bank_name    = request.form.get("bank_name", "").strip()
    bank_country = request.form.get("bank_country", "").strip()
    if not bank_name or not bank_country:
        return redirect(url_for("connect"))
    try:
        result   = enablebanking.start_auth(bank_name, bank_country)
        auth_url = result["url"]
    except Exception as e:
        logger.error("Failed to start reauth: %s", e)
        return redirect(url_for("connect") + f"?error=Could not start re-authorisation: {e}")
    return render_template("connect.html",
        error=None,
        success=None,
        auth_url=auth_url,
        tokens=db.get_tokens(),
        pending_bank=bank_name,
        sync_time=_cfg().SYNC_TIME,
    )

@app.route("/callback")
def callback():
    error = request.args.get("error")
    if error:
        return redirect(url_for("connect") + "?error=" + error)
    code  = request.args.get("code", "")
    state = request.args.get("state", "")
    if not code:
        return redirect(url_for("connect") + "?error=missing_code")
    try:
        ok = enablebanking.complete_auth(code=code, state=state)
        if ok:
            _start_scheduler_if_ready()
            import threading
            threading.Thread(target=sync.run, daemon=True).start()
            return redirect(url_for("status"))
        else:
            return redirect(url_for("connect") + "?error=auth_failed")
    except Exception as e:
        logger.error("Callback auth failed: %s", e)
        return redirect(url_for("connect") + "?error=" + str(e))

@app.route("/status")
def status():
    # Revalidate licence on every status page load
    try:
        from .. import licence as _lic
        _lic.validate()
    except Exception:
        pass
    if not _is_configured():
        return redirect(url_for("setup_licence"))
    if not _is_connected():
        return redirect(url_for("connect"))

    tokens     = db.get_tokens()
    all_tokens = db.get_all_tokens()
    page       = request.args.get("page", 1, type=int)
    log_data   = db.get_sync_log_page(page=page, per_page=5)
    syncs      = log_data["syncs"]
    days_left  = enablebanking.check_token_expiry()
    last_sync  = db.get_last_sync()

    licence_sync_failed = False
    val = licence.validate()
    if not val.get("valid") and not val.get("offline"):
        licence_sync_failed = True

    activation_usage = 0
    activation_limit = 2
    try:
        import requests as _requests
        key = _cfg().LICENCE_KEY
        if key:
            resp = _requests.post(
                "https://api.klartion.com/info",
                json={"license_key": key},
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                activation_usage = data.get("activation_usage", 0)
                activation_limit = data.get("activation_limit", 2)
    except Exception:
        pass

    # Fun stats
    import random
    conn = db.get_conn()
    total_tx = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    streak_rows = conn.execute("SELECT status FROM sync_log ORDER BY ran_at DESC LIMIT 100").fetchall()
    conn.close()
    streak = 0
    for r in streak_rows:
        if r["status"] == "success":
            streak += 1
        else:
            break

    fun_messages = [
        "Your finances are in good hands.",
        "Another day, another sync.",
        "Everything's running smoothly.",
        "Your bank called. They said everything's fine.",
        "Transactions delivered. You're welcome.",
        "Syncing like clockwork.",
        "Your Notion database is looking sharp.",
        "All quiet on the banking front.",
        "Nothing to worry about here.",
        "Your data, your machine, your peace of mind.",
    ]
    fun_message = random.choice(fun_messages)

    return render_template("status.html",
        tokens=tokens,
        all_tokens=all_tokens,
        syncs=syncs,
        days_left=days_left,
        last_sync=last_sync,
        sync_time=_cfg().SYNC_TIME,
        notify_email=_cfg().NOTIFY_EMAIL,
        activation_usage=activation_usage,
        activation_limit=activation_limit,
        licence_sync_failed=licence_sync_failed,
        licence_limit_reached=(licence_sync_failed and activation_usage >= activation_limit and activation_limit > 0),
        page=log_data["page"],
        total_pages=log_data["total_pages"],
        update_mode=db.get_setting("update_mode"),
        update_available=db.get_setting("update_available") == "1",
        total_tx=total_tx,
        streak=streak,
        fun_message=fun_message,
    )

@app.route("/sync/now", methods=["POST"])
def sync_now():
    import threading
    threading.Thread(target=sync.run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/sync/reset", methods=["POST"])
def sync_reset():
    db.clear_sync_log()
    conn = db.get_conn()
    conn.execute("DELETE FROM transactions")
    conn.commit()
    conn.close()
    return redirect(url_for("status"))

@app.route("/sync/clear", methods=["POST"])
def clear_sync_log():
    db.clear_sync_log()
    return redirect(url_for("status"))

@app.route("/connect/reset-pem")
def reset_pem():
    import glob, os
    for f in glob.glob("/app/data/*.pem"):
        os.remove(f)
    _cfg().set("EB_APP_ID", "")
    return redirect(url_for("connect"))

@app.route("/disconnect", methods=["POST"])
def disconnect():
    token_id = request.form.get("token_id")
    if token_id:
        db.clear_token_by_id(int(token_id))
    else:
        db.clear_tokens()
    return redirect(url_for("connect"))

# ---------------------------------------------------------------------------
# Update preference + self-update
# ---------------------------------------------------------------------------

@app.route("/update/preference", methods=["POST"])
def update_preference():
    mode = request.form.get("mode", "manual")
    db.set_setting("update_mode", mode)
    return redirect(url_for("status"))

@app.route("/update/check", methods=["GET"])
def update_check():
    import subprocess, os
    if not os.path.exists("/var/run/docker.sock"):
        return jsonify({"available": False})
    try:
        # Get current running image ID
        current = subprocess.run(
            ["docker", "inspect", "--format", "{{.Image}}", CONTAINER_NAME],
            capture_output=True, text=True, timeout=10
        ).stdout.strip()
        # Get latest local image ID (without pulling)
        # Use Docker Hub API to check digest
        import requests as _req
        repo = IMAGE_NAME.split(":")[0]
        tag = IMAGE_NAME.split(":")[1] if ":" in IMAGE_NAME else "latest"
        token_resp = _req.get(f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull", timeout=5)
        token = token_resp.json().get("token", "")
        manifest_resp = _req.head(
            f"https://registry-1.docker.io/v2/{repo}/manifests/{tag}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
            timeout=5
        )
        remote_digest = manifest_resp.headers.get("Docker-Content-Digest", "")
        # Get local image digest
        local_digest = subprocess.run(
            ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", IMAGE_NAME],
            capture_output=True, text=True, timeout=10
        ).stdout.strip()
        # local_digest looks like "daalves/klartion@sha256:abc..."
        local_sha = local_digest.split("@")[-1] if "@" in local_digest else ""
        return jsonify({"available": remote_digest != local_sha and remote_digest != ""})
    except Exception:
        return jsonify({"available": False})

@app.route("/update/run", methods=["POST"])
def update_run():
    import subprocess, os
    if not os.path.exists("/var/run/docker.sock"):
        return jsonify({"error": "Docker socket not mounted."}), 400
    try:
        # Pull and check if image changed
        pull = subprocess.run(
            ["docker", "pull", IMAGE_NAME],
            capture_output=True, text=True, timeout=120
        )
        if "Image is up to date" in pull.stdout or "Image is up to date" in pull.stderr:
            return jsonify({"up_to_date": True})
        # New image pulled — restart
        subprocess.Popen(
            ["sh", "-c", "sleep 2 && cd /compose && docker compose up -d"],
            start_new_session=True
        )
        db.set_setting("update_available", "0")
        return jsonify({"updating": True})
    except FileNotFoundError:
        return jsonify({"error": "Docker CLI not available in container."}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

_banks_cache = None

@app.route("/banks")
def banks():
    global _banks_cache
    if _banks_cache is None:
        try:
            _banks_cache = enablebanking.get_banks()
        except Exception as e:
            logger.error("Failed to fetch banks: %s", e)
            resp = jsonify([])
            resp.headers["Access-Control-Allow-Origin"] = "*"
            return resp
    resp = jsonify(_banks_cache)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

def _start_scheduler_if_ready():
    if _is_configured():
        try:
            from ..scheduler import start as start_scheduler
            import threading
            threading.Thread(target=start_scheduler, daemon=True).start()
        except Exception as e:
            logger.warning("Could not start scheduler: %s", e)

def start(host="0.0.0.0", port=3000):
    app.run(host=host, port=port, debug=False, use_reloader=False)

import logging
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from .. import db, enablebanking, licence, sync

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")

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

@app.route("/setup/notion", methods=["GET", "POST"])
def setup_notion():
    error = None
    if request.method == "POST":
        api_key = request.form.get("notion_api_key", "").strip()
        db_id   = request.form.get("notion_database_id", "").strip()
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
    )

@app.route("/setup/notifications", methods=["GET", "POST"])
def setup_notifications():
    error = None
    if request.method == "POST":
        klartion_url = request.form.get("klartion_url", "").strip().rstrip("/")
        email        = request.form.get("notify_email", "").strip()
        sync_time    = request.form.get("sync_time", "08:00").strip()
        smtp_user    = request.form.get("smtp_user", "").strip()
        smtp_pass    = request.form.get("smtp_password", "").strip()
        if not klartion_url or not email or not smtp_user or not smtp_pass:
            error = "All fields are required."
        else:
            _cfg().set("KLARTION_URL",   klartion_url)
            _cfg().set("NOTIFY_EMAIL",   email)
            _cfg().set("SYNC_TIME",      sync_time)
            _cfg().set("SMTP_USER",      smtp_user)
            _cfg().set("SMTP_PASSWORD",  smtp_pass)
            _start_scheduler_if_ready()
            return redirect(url_for("connect"))
    return render_template("setup_notifications.html",
        error=error,
        klartion_url=_cfg().KLARTION_URL,
        notify_email=_cfg().NOTIFY_EMAIL,
        sync_time=_cfg().SYNC_TIME,
        smtp_user=_cfg().SMTP_USER,
        smtp_password=_cfg().SMTP_PASSWORD,
    )

@app.route("/settings/deactivate", methods=["POST"])
def deactivate_licence():
    from .. import licence
    result = licence.deactivate()
    if result["success"]:
        _cfg().set("LICENCE_KEY", "")
        return redirect(url_for("setup_licence") + "?msg=deactivated")
    return redirect(url_for("status") + "?error=" + (result["error"] or "Deactivation failed."))

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
        if action == "start":
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

    tokens  = db.get_tokens()
    success = request.args.get("success")
    return render_template("connect.html",
        error=error,
        success=success,
        auth_url=auth_url,
        tokens=tokens,
        pending_bank=pending,
        sync_time=_cfg().SYNC_TIME,
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
            return render_template("callback_redirect.html", target=url_for("connect") + "?success=1")
        else:
            return render_template("callback_redirect.html", target=url_for("connect") + "?error=auth_failed")
    except Exception as e:
        logger.error("Callback auth failed: %s", e)
        return render_template("callback_redirect.html", target=url_for("connect") + "?error=" + str(e))

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

    tokens    = db.get_tokens()
    page      = request.args.get("page", 1, type=int)
    log_data  = db.get_sync_log_page(page=page, per_page=5)
    syncs     = log_data["syncs"]
    days_left = enablebanking.check_token_expiry()
    last_sync = db.get_last_sync()

    licence_sync_failed = False
    instance_id = db.get_setting("licence_instance_id")
    if syncs and not instance_id:
        last = syncs[0]
        msg = (last.get("message") or "").lower()
        if last.get("status") == "failure" and ("licence" in msg or "license" in msg):
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

    return render_template("status.html",
        tokens=tokens,
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
    )

@app.route("/sync/now", methods=["POST"])
def sync_now():
    import threading
    threading.Thread(target=sync.run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/sync/clear", methods=["POST"])
def clear_sync_log():
    db.clear_sync_log()
    return redirect(url_for("status"))

@app.route("/disconnect", methods=["POST"])
def disconnect():
    db.clear_tokens()
    return redirect(url_for("connect"))

_banks_cache = None

@app.route("/banks")
def banks():
    global _banks_cache
    if _banks_cache is None:
        try:
            _banks_cache = enablebanking.get_banks()
        except Exception as e:
            logger.error("Failed to fetch banks: %s", e)
            return jsonify([])
    return jsonify(_banks_cache)

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

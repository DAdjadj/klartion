import logging
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from .. import config, db, enablebanking, licence, sync

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = config.SECRET_KEY

def _is_configured() -> bool:
    return not bool(config.validate())

def _is_connected() -> bool:
    return db.get_tokens() is not None

@app.route("/")
def index():
    if not _is_configured():
        return redirect(url_for("setup"))
    if not _is_connected():
        return redirect(url_for("connect"))
    return redirect(url_for("status"))

@app.route("/setup", methods=["GET", "POST"])
def setup():
    error = None
    if request.method == "POST":
        key = request.form.get("licence_key", "").strip()
        result = licence.validate(key)
        if not result["valid"]:
            error = f"Licence key invalid: {result['error']}"
        else:
            missing = config.validate()
            if missing:
                error = f"Missing configuration: {', '.join(missing)}. Please update your .env file and restart."
            else:
                return redirect(url_for("connect"))
    missing = config.validate()
    return render_template("setup.html",
        error=error,
        missing=missing,
        configured=_is_configured(),
        sync_time=config.SYNC_TIME,
        notify_email=config.NOTIFY_EMAIL,
    )

@app.route("/connect", methods=["GET", "POST"])
def connect():
    if not _is_configured():
        return redirect(url_for("setup"))

    error      = None
    auth_url   = None
    banks      = _popular_banks()
    pending_id = db.get_setting("pending_session_id")

    if request.method == "POST":
        action = request.form.get("action")

        if action == "start":
            bank_name    = request.form.get("bank_name", "").strip()
            bank_country = request.form.get("bank_country", "").strip()
            if not bank_name or not bank_country:
                error = "Please select a bank and country."
            else:
                try:
                    result     = enablebanking.start_auth(bank_name, bank_country)
                    auth_url   = result["url"]
                    pending_id = result["session_id"]
                except Exception as e:
                    logger.error("Failed to start auth: %s", e)
                    error = f"Could not start bank connection: {e}"

        elif action == "confirm":
            try:
                ok = enablebanking.poll_auth()
                if ok:
                    return redirect(url_for("connect") + "?success=1")
                else:
                    error = "Bank access not confirmed yet. Please make sure you approved it in your browser, then try again."
                    pending_id = db.get_setting("pending_session_id")
            except Exception as e:
                logger.error("Poll auth failed: %s", e)
                error = f"Could not confirm connection: {e}"

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
        banks=banks,
        tokens=tokens,
        auth_url=auth_url,
        pending_id=pending_id,
        pending_bank=db.get_setting("pending_bank_name"),
        sync_time=config.SYNC_TIME,
    )

@app.route("/status")
def status():
    if not _is_configured():
        return redirect(url_for("setup"))
    if not _is_connected():
        return redirect(url_for("connect"))

    tokens    = db.get_tokens()
    syncs     = db.get_recent_syncs(limit=15)
    days_left = enablebanking.check_token_expiry()
    last_sync = db.get_last_sync()

    return render_template("status.html",
        tokens=tokens,
        syncs=syncs,
        days_left=days_left,
        last_sync=last_sync,
        sync_time=config.SYNC_TIME,
        notify_email=config.NOTIFY_EMAIL,
    )

@app.route("/sync/now", methods=["POST"])
def sync_now():
    import threading
    threading.Thread(target=sync.run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/disconnect", methods=["POST"])
def disconnect():
    db.clear_tokens()
    return redirect(url_for("connect"))

def _popular_banks():
    return [
        {"name": "Revolut",        "country": "LT"},
        {"name": "Monzo",          "country": "GB"},
        {"name": "N26",            "country": "DE"},
        {"name": "Wise",           "country": "BE"},
        {"name": "Millennium BCP", "country": "PT"},
        {"name": "Caixa Geral",    "country": "PT"},
        {"name": "Santander",      "country": "ES"},
        {"name": "ING",            "country": "NL"},
        {"name": "BNP Paribas",    "country": "FR"},
        {"name": "Deutsche Bank",  "country": "DE"},
    ]

def start(host="0.0.0.0", port=3000):
    app.run(host=host, port=port, debug=False, use_reloader=False)

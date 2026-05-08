import logging
import os
from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from .. import db, enablebanking, licence, sync

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")

CONTAINER_NAME = "klartion"
IMAGE_NAME = "daalves/klartion:latest"
APP_VERSION = os.environ.get("APP_VERSION", "dev")

@app.context_processor
def inject_globals():
    return {"app_version": APP_VERSION}

@app.before_request
def load_secret_key():
    from .. import config as cfg
    import os
    app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

@app.before_request
def capture_psu_signals():
    """Record the user's IP and User-Agent on every UI request so that
    background syncs can forward them as PSU-* headers to Enable Banking.
    Comdirect (and other German ASPSPs) lift the 4-calls-per-day cap when
    PSU headers are present."""
    if not request.path.startswith("/static/"):
        from datetime import datetime, timezone
        ip = (request.headers.get("Cf-Connecting-Ip")
              or request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0]).strip()
        ua = (request.headers.get("User-Agent") or "")[:200]
        if ip:
            db.set_setting("psu_ip", ip)
        if ua:
            db.set_setting("psu_user_agent", ua)
        if ip or ua:
            db.set_setting("psu_updated_at", datetime.now(timezone.utc).isoformat())

def _cfg():
    from .. import config
    return config

def _is_configured():
    return _cfg().is_configured()

def _is_connected():
    return db.get_tokens() is not None

def _get_bank_account_limit():
    try:
        info = licence.get_activation_info()
        return max(1, int(info.get("bank_account_limit", 2) or 2))
    except Exception:
        return 2

def _sync_bank_seats(tokens):
    if not _cfg().LICENCE_KEY:
        db.set_setting("license_bank_limit_error", "")
        return {"ok": True, "used": 0, "limit": _get_bank_account_limit()}
    result = licence.sync_bank_seats(tokens)
    if result.get("ok"):
        db.set_setting("license_bank_limit_error", "")
    elif not result.get("network"):
        db.set_setting("license_bank_limit_error", result.get("error", ""))
    return result

def _get_bank_seat_error(tokens=None):
    tokens = tokens if tokens is not None else db.get_all_tokens()
    result = _sync_bank_seats(tokens)
    if result.get("ok") or result.get("network"):
        return None, result
    return result.get("error"), result

def _ensure_global_bank_capacity(tokens, new_seats=1):
    result = _sync_bank_seats(tokens)
    if not result.get("ok"):
        if result.get("network"):
            return "Could not confirm your global bank slot availability right now. Please try again in a moment."
        return result.get("error") or "Bank account limit reached for this licence."
    used = int(result.get("used") or 0)
    limit = int(result.get("limit") or _get_bank_account_limit())
    if used + new_seats > limit:
        return f"Bank account limit reached ({limit}). Disconnect a bank on another machine or add another bank slot before connecting a new one."
    return None

def _claim_bank_seat(token):
    result = licence.claim_bank_seat(token)
    if result.get("ok"):
        db.set_setting("license_bank_limit_error", "")
        return None
    if result.get("network"):
        return "Could not confirm your global bank slot availability right now. Please try again in a moment."
    return result.get("error") or "Bank account limit reached for this licence."

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
        active="license",
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
        active="notion",
    )

@app.route("/setup/notifications", methods=["GET", "POST"])
def setup_notifications():
    error = None
    if request.method == "POST":
        klartion_url = request.form.get("klartion_url", "").strip().rstrip("/")
        email        = request.form.get("notify_email", "").strip()
        notify_on    = request.form.get("notify_on", "all").strip()
        smtp_user    = request.form.get("smtp_user", "").strip()
        smtp_pass    = request.form.get("smtp_password", "").strip()
        smtp_from    = request.form.get("smtp_from", "").strip()
        smtp_host    = request.form.get("smtp_host", "").strip()
        if not klartion_url or (notify_on != "never" and (not email or not smtp_user or not smtp_pass)):
            error = "All fields are required."
        else:
            _cfg().set("KLARTION_URL",   klartion_url)
            _cfg().set("NOTIFY_EMAIL",   email)
            _cfg().set("NOTIFY_ON",      notify_on)
            _cfg().set("SMTP_USER",      smtp_user)
            _cfg().set("SMTP_PASSWORD",  smtp_pass)
            _cfg().set("SMTP_FROM",      smtp_from)
            _cfg().set("SMTP_HOST",      smtp_host)
            return redirect(url_for("setup_sync"))
    return render_template("setup_notifications.html",
        error=error,
        klartion_url=_cfg().KLARTION_URL,
        notify_email=_cfg().NOTIFY_EMAIL,
        notify_on=_cfg().NOTIFY_ON or "all",
        smtp_user=_cfg().SMTP_USER,
        smtp_password=_cfg().SMTP_PASSWORD,
        smtp_from=_cfg().SMTP_FROM,
        smtp_host=_cfg().SMTP_HOST if _cfg().SMTP_HOST != "smtp.mail.me.com" else "",
        active="notifications",
    )

@app.route("/email/test", methods=["POST"])
def test_email():
    try:
        data = request.get_json(silent=True) or {}
        from .. import email_notify
        # If form data provided, use it directly (allows testing before saving)
        if data.get("smtp_user") and data.get("smtp_password") and data.get("notify_email"):
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            msg = MIMEMultipart("alternative")
            msg["Subject"] = "Klartion: test email"
            msg["From"]    = data.get("smtp_from") or data["smtp_user"]
            msg["To"]      = data["notify_email"]
            msg.attach(MIMEText("This is a test email from Klartion. If you're reading this, your email notifications are working correctly.", "plain"))
            host = data.get("smtp_host") or email_notify._smtp_host_for(data["smtp_user"])
            port = int(_cfg().SMTP_PORT or 587)
            with smtplib.SMTP(host, port) as server:
                server.ehlo()
                server.starttls()
                server.login(data["smtp_user"], data["smtp_password"])
                server.sendmail(data.get("smtp_from") or data["smtp_user"], data["notify_email"], msg.as_string())
        else:
            email_notify.send("Klartion: test email", "This is a test email from Klartion. If you're reading this, your email notifications are working correctly.")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/email/unsubscribe-status")
def unsubscribe_status():
    email = _cfg().NOTIFY_EMAIL
    if not email:
        return jsonify({"unsubscribed": False})
    try:
        import requests as _requests
        resp = _requests.post("https://api.klartion.com/is-unsubscribed", json={"email": email}, timeout=5)
        return jsonify({"unsubscribed": resp.ok and resp.json().get("unsubscribed", False)})
    except Exception:
        return jsonify({"unsubscribed": False})

@app.route("/email/unsubscribe", methods=["POST"])
def unsubscribe_email():
    email = _cfg().NOTIFY_EMAIL
    if not email:
        return jsonify({"error": "No notification email configured"}), 400
    try:
        import requests as _requests
        resp = _requests.get(f"https://api.klartion.com/unsubscribe?email={email}", timeout=5)
        if resp.ok:
            return jsonify({"ok": True})
        return jsonify({"error": "Failed to unsubscribe"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/email/resubscribe", methods=["POST"])
def resubscribe_email():
    email = _cfg().NOTIFY_EMAIL
    if not email:
        return jsonify({"error": "No notification email configured"}), 400
    try:
        import requests as _requests
        resp = _requests.post("https://api.klartion.com/resubscribe", json={"email": email}, timeout=5)
        if resp.ok:
            from .. import email_notify
            email_notify._unsubscribed_cache.pop(email, None)
            return jsonify({"ok": True})
        return jsonify({"error": "Failed to resubscribe"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    has_simplefin = any(t.get("provider") == "simplefin" for t in db.get_all_tokens())
    return render_template("setup_sync.html",
        error=error,
        sync_time=_cfg().SYNC_TIME or "08:00",
        sync_frequency=_cfg().SYNC_FREQUENCY if hasattr(_cfg(), 'SYNC_FREQUENCY') else "24",
        is_configured=_is_configured(),
        has_simplefin=has_simplefin,
        active="sync",
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
    from datetime import datetime, timezone, timedelta
    status = "ok"
    details = {}

    # Check last sync (overdue if > frequency + 2h)
    try:
        last_sync = db.get_last_sync()
        details["last_sync"] = last_sync or None
        if last_sync:
            last_dt = datetime.fromisoformat(last_sync.replace("Z", "+00:00") if "Z" in last_sync else last_sync)
            frequency_h = int(_cfg().SYNC_FREQUENCY or 24)
            overdue_threshold = timedelta(hours=frequency_h + 2)
            age = datetime.now(timezone.utc) - last_dt.replace(tzinfo=timezone.utc) if last_dt.tzinfo is None else datetime.now(timezone.utc) - last_dt
            if age > overdue_threshold:
                details["sync_overdue"] = True
                status = "degraded"
    except Exception:
        pass

    # Check bank connection and token expiry
    try:
        all_tokens = db.get_all_tokens()
        details["banks_connected"] = len(all_tokens)
        days_left = enablebanking.check_token_expiry()
        if days_left is not None:
            details["token_expires_in_days"] = days_left
            if days_left <= 0:
                status = "degraded"
    except Exception:
        pass

    # Scheduler job count
    try:
        from ..scheduler import get_job_count
        details["scheduler_jobs"] = get_job_count()
    except Exception:
        pass

    details["version"] = APP_VERSION
    return jsonify({"status": status, **details})

@app.route("/api/version")
def api_version():
    return jsonify({"version": APP_VERSION})

@app.route("/api/last-sync")
def last_sync_api():
    return jsonify({"ran_at": db.get_last_sync() or ""})

@app.route("/api/timezone", methods=["POST"])
def api_timezone():
    from zoneinfo import available_timezones
    data = request.get_json(silent=True) or {}
    tz = data.get("tz", "")
    if not tz or tz not in available_timezones():
        return jsonify({"ok": False}), 400
    if tz != _cfg().TIMEZONE:
        _cfg().set("TIMEZONE", tz)
        from .. import scheduler
        scheduler.start()
    return jsonify({"ok": True})

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
    # Surface ?error= from query string so redirects from /connect/simplefin/claim,
    # /pick-account, and /callback render their messages on this page. The error
    # ends up inside `{{ error }}` in connect.html, which Jinja auto-escapes.
    error    = request.args.get("error") or None
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
            bank_name      = request.form.get("bank_name", "").strip()
            bank_country   = request.form.get("bank_country", "").strip()
            start_sync_date = request.form.get("start_sync_date", "").strip()
            if not bank_name or not bank_country:
                error = "Please select a bank."
            else:
                error = _ensure_global_bank_capacity(db.get_all_tokens(), new_seats=1)
            if not error:
                try:
                    if start_sync_date:
                        db.set_setting("pending_start_sync_date", start_sync_date)
                    db.set_setting("pending_reauth_token_id", "")
                    # Update KLARTION_URL from current request so the OAuth
                    # callback redirects back through the same scheme (HTTPS via Caddy)
                    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
                    host   = request.headers.get("X-Forwarded-Host", request.host)
                    _cfg().set("KLARTION_URL", f"{scheme}://{host}")
                    result   = enablebanking.start_auth(bank_name, bank_country)
                    auth_url = result["url"]
                    pending  = bank_name
                except Exception as e:
                    logger.error("Failed to start auth: %s", e)
                    error = f"Could not start bank connection: {e}"
        elif action == "connect_provider":
            provider_name = request.form.get("provider_name", "").strip()
            if not provider_name:
                error = "Please select a provider."
            else:
                error = _ensure_global_bank_capacity(db.get_all_tokens(), new_seats=1)
            if not error:
                from ..providers import get_provider, PROVIDERS
                if provider_name not in PROVIDERS:
                    error = f"Unknown provider: {provider_name}"
                else:
                    provider = get_provider(provider_name)
                    credentials = {}
                    for field in provider.credential_fields:
                        val = request.form.get(f"cred_{field['key']}", "").strip()
                        if not val:
                            error = f"{field['label']} is required."
                            break
                        credentials[field["key"]] = val

                if not error:
                    try:
                        valid = provider.validate_credentials(credentials)
                        if not valid:
                            error = f"Could not connect to {provider.display_name}. Please check your credentials."
                    except Exception as e:
                        error = f"Could not validate {provider.display_name} credentials: {e}"

                if not error:
                    from .. import crypto
                    encrypted = crypto.encrypt_credentials(credentials)
                    token_id = db.save_provider_token(
                        bank_name=provider.display_name,
                        provider=provider_name,
                        provider_credentials=encrypted,
                    )
                    token = db.get_token_by_id(token_id)
                    seat_error = _claim_bank_seat(token)
                    if seat_error:
                        db.clear_token_by_id(token_id)
                        error = seat_error
                    else:
                        _start_scheduler_if_ready()
                        import threading
                        threading.Thread(target=sync.run, daemon=True).start()
                        return redirect(url_for("connect", success=1))

        elif action == "cancel":
            db.set_setting("pending_session_id", "")
            db.set_setting("pending_bank_name", "")
            db.set_setting("pending_bank_country", "")
            db.set_setting("pending_start_sync_date", "")
            db.set_setting("pending_reauth_token_id", "")
            for key in ("pending_simplefin_access_url", "pending_simplefin_accounts",
                        "pending_simplefin_raw_accounts", "pending_simplefin_start_sync_date",
                        "pending_simplefin_warning", "pending_simplefin_skipped"):
                db.set_setting(key, "")
            return redirect(url_for("connect"))

    import glob
    all_tokens = db.get_all_tokens()
    tokens     = db.get_tokens()  # for backwards compat
    bank_seat_error, bank_seat_result = _get_bank_seat_error(all_tokens)
    success    = request.args.get("success")
    pem_ready  = bool(glob.glob("/app/data/*.pem"))

    # Fetch bank account limit from licence API
    bank_account_limit = int((bank_seat_result or {}).get("limit") or _get_bank_account_limit())
    bank_seat_usage = int((bank_seat_result or {}).get("used") or len(all_tokens))

    from ..providers import get_all_providers
    balance_providers = get_all_providers()

    from datetime import date
    has_simplefin = any(t.get("provider") == "simplefin" for t in all_tokens)
    has_enablebanking = any(
        t.get("provider") not in ("simplefin",) and t.get("sync_mode") != "balance"
        for t in all_tokens
    )
    sf_lapsed = db.get_setting("simplefin_subscription_lapsed") == "1"
    sf_revoked_setting = (db.get_setting("simplefin_access_revoked") or "").strip()
    # Set, not string — multiple tokens can be revoked at once when an
    # access URL is shared across sibling accounts.
    sf_revoked_token_ids = {x for x in sf_revoked_setting.split(",") if x}
    # Resume state: claim succeeded, access URL is saved, but the
    # account-list step never completed. Setup tokens are one-shot, so
    # without a resume CTA the user is stuck.
    sf_pending_resume = bool(
        db.get_setting("pending_simplefin_access_url")
        and not db.get_setting("pending_simplefin_accounts")
    )
    return render_template("connect.html",
        error=error,
        success=success,
        bank_seat_error=bank_seat_error,
        auth_url=auth_url,
        tokens=tokens,
        all_tokens=all_tokens,
        pending_bank=pending,
        sync_time=_cfg().SYNC_TIME,
        pem_ready=pem_ready,
        eb_app_id=_cfg().EB_APP_ID,
        bank_account_limit=bank_account_limit,
        bank_seat_usage=bank_seat_usage,
        bank_slot_url=f"https://buy.stripe.com/4gM9AMg348nt2Y7185cMM04?client_reference_id={_cfg().LICENCE_KEY}",
        today=date.today().isoformat(),
        balance_providers=balance_providers,
        has_simplefin=has_simplefin,
        has_enablebanking=has_enablebanking,
        simplefin_subscription_lapsed=sf_lapsed,
        simplefin_access_revoked_token_ids=sf_revoked_token_ids,
        simplefin_pending_resume=sf_pending_resume,
        active="bank",
    )

@app.route("/connect/reauthorise", methods=["POST"])
def reauthorise():
    token_id = request.form.get("token_id", "").strip()
    bank_name    = request.form.get("bank_name", "").strip()
    bank_country = request.form.get("bank_country", "").strip()
    if not bank_name or not bank_country:
        return redirect(url_for("connect"))
    try:
        db.set_setting("pending_start_sync_date", "")
        db.set_setting("pending_reauth_token_id", token_id)
        result   = enablebanking.start_auth(bank_name, bank_country)
        auth_url = result["url"]
    except Exception as e:
        logger.error("Failed to start reauth: %s", e)
        return redirect(url_for("connect") + f"?error=Could not start re-authorisation: {e}")
    all_tokens = db.get_all_tokens()
    bank_seat_error, bank_seat_result = _get_bank_seat_error(all_tokens)
    bank_account_limit = int((bank_seat_result or {}).get("limit") or _get_bank_account_limit())
    bank_seat_usage = int((bank_seat_result or {}).get("used") or len(all_tokens))
    import glob
    from ..providers import get_all_providers
    from datetime import date
    has_simplefin = any(t.get("provider") == "simplefin" for t in all_tokens)
    has_enablebanking = any(
        t.get("provider") not in ("simplefin",) and t.get("sync_mode") != "balance"
        for t in all_tokens
    )
    sf_revoked_setting = (db.get_setting("simplefin_access_revoked") or "").strip()
    return render_template("connect.html",
        error=None,
        success=None,
        auth_url=auth_url,
        tokens=db.get_tokens(),
        all_tokens=all_tokens,
        bank_seat_error=bank_seat_error,
        pending_bank=bank_name,
        sync_time=_cfg().SYNC_TIME,
        pem_ready=bool(glob.glob("/app/data/*.pem")),
        eb_app_id=_cfg().EB_APP_ID,
        bank_account_limit=bank_account_limit,
        bank_seat_usage=bank_seat_usage,
        bank_slot_url=f"https://buy.stripe.com/4gM9AMg348nt2Y7185cMM04?client_reference_id={_cfg().LICENCE_KEY}",
        today=date.today().isoformat(),
        balance_providers=get_all_providers(),
        has_simplefin=has_simplefin,
        has_enablebanking=has_enablebanking,
        simplefin_subscription_lapsed=db.get_setting("simplefin_subscription_lapsed") == "1",
        simplefin_access_revoked_token_ids={x for x in sf_revoked_setting.split(",") if x},
        simplefin_pending_resume=bool(
            db.get_setting("pending_simplefin_access_url")
            and not db.get_setting("pending_simplefin_accounts")
        ),
        active="bank",
    )

def _finalize_bank_connection(result, account_uid):
    """Save bank tokens, clear pending settings, start scheduler and sync."""
    reauth_token_id = (db.get_setting("pending_reauth_token_id") or "").strip()
    if reauth_token_id:
        token_id = int(reauth_token_id)
        db.save_tokens(
            session_id=result["session_id"],
            access_token=account_uid,
            bank_name=result["bank_name"],
            bank_country=result["bank_country"],
            expires_at=result.get("valid_until", ""),
            token_id=token_id,
        )
    else:
        start_sync_date = db.get_setting("pending_start_sync_date") or ""
        capacity_error = _ensure_global_bank_capacity(db.get_all_tokens(), new_seats=1)
        if capacity_error:
            raise ValueError(capacity_error)
        token_id = db.save_tokens(
            session_id=result["session_id"],
            access_token=account_uid,
            bank_name=result["bank_name"],
            bank_country=result["bank_country"],
            expires_at=result.get("valid_until", ""),
            start_sync_date=start_sync_date,
        )
    token = db.get_token_by_id(token_id)
    seat_error = _claim_bank_seat(token)
    if seat_error:
        if not reauth_token_id:
            db.clear_token_by_id(token_id)
        raise ValueError(seat_error)
    db.set_setting("pending_session_id", "")
    db.set_setting("pending_bank_name", "")
    db.set_setting("pending_bank_country", "")
    db.set_setting("pending_valid_until", "")
    db.set_setting("pending_start_sync_date", "")
    db.set_setting("pending_reauth_token_id", "")
    _start_scheduler_if_ready()
    import threading
    threading.Thread(target=sync.run, daemon=True).start()

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
        result = enablebanking.complete_auth(code=code, state=state)
        accounts = result["accounts"]
        if len(accounts) == 1:
            account_uid = enablebanking.extract_account_uid(accounts[0])
            _finalize_bank_connection(result, account_uid)
            return redirect(url_for("status"))
        else:
            import json
            db.set_setting("pending_auth_session_id", result["session_id"])
            db.set_setting("pending_auth_accounts", json.dumps(accounts))
            db.set_setting("pending_auth_valid_until", result.get("valid_until", ""))
            db.set_setting("pending_auth_bank_name", result.get("bank_name", ""))
            db.set_setting("pending_auth_bank_country", result.get("bank_country", ""))
            return redirect(url_for("pick_account"))
    except Exception as e:
        logger.error("Callback auth failed: %s", e)
        return redirect(url_for("connect") + "?error=" + str(e))

@app.route("/pick-account")
def pick_account():
    import json
    error = request.args.get("error") or None
    sf_accounts_json = db.get_setting("pending_simplefin_accounts")
    if sf_accounts_json:
        accounts = json.loads(sf_accounts_json)
        warning = db.get_setting("pending_simplefin_warning") or ""
        skipped = db.get_setting("pending_simplefin_skipped") or "0"
        try:
            skipped_count = int(skipped)
        except ValueError:
            skipped_count = 0
        from datetime import date
        return render_template("pick_account_simplefin.html",
            accounts=accounts,
            warning=warning,
            skipped=skipped_count,
            error=error,
            today=date.today().isoformat(),
            active="pick-account")
    accounts_json = db.get_setting("pending_auth_accounts")
    if not accounts_json:
        return redirect(url_for("connect"))
    accounts = json.loads(accounts_json)
    return render_template("pick_account.html", accounts=accounts, error=error, active="pick-account")

@app.route("/pick-account", methods=["POST"])
def pick_account_post():
    if db.get_setting("pending_simplefin_access_url"):
        return _finalize_simplefin_connection()

    account_uid = request.form.get("account_uid")
    if not account_uid:
        return redirect(url_for("pick_account"))
    session_id   = db.get_setting("pending_auth_session_id")
    valid_until  = db.get_setting("pending_auth_valid_until")
    bank_name    = db.get_setting("pending_auth_bank_name") or db.get_setting("pending_bank_name")
    bank_country = db.get_setting("pending_auth_bank_country") or db.get_setting("pending_bank_country")
    result = {
        "session_id": session_id,
        "bank_name": bank_name,
        "bank_country": bank_country,
        "valid_until": valid_until,
    }
    try:
        _finalize_bank_connection(result, account_uid)
    except Exception as e:
        return redirect(url_for("connect") + "?error=" + str(e))
    for key in ["pending_auth_session_id", "pending_auth_accounts", "pending_auth_valid_until",
                "pending_auth_bank_name", "pending_auth_bank_country"]:
        db.set_setting(key, "")
    return redirect(url_for("status"))


@app.route("/connect/simplefin/claim", methods=["POST"])
def connect_simplefin_claim():
    """Claim a SimpleFIN setup token, list accounts, stash pending state, redirect to picker.
    Setup tokens are one-shot, so the access URL is encrypted and persisted before
    enumerating accounts — that way a /accounts failure can be retried without
    burning the token."""
    import json
    from .. import simplefin, crypto

    setup_token = request.form.get("simplefin_setup_token", "").strip()
    start_sync_date = request.form.get("start_sync_date", "").strip()
    if not setup_token:
        return redirect(url_for("connect") + "?error=Setup token is required.")

    capacity_error = _ensure_global_bank_capacity(db.get_all_tokens(), new_seats=1)
    if capacity_error:
        return redirect(url_for("connect") + "?error=" + capacity_error)

    try:
        access_url = simplefin.claim_setup_token(setup_token)
    except simplefin.SimpleFinTokenAlreadyClaimed as e:
        logger.warning("SimpleFIN setup token already claimed")
        return redirect(url_for("connect") + "?error=" + str(e))
    except simplefin.SimpleFinError as e:
        # SimpleFinError messages are already credential-scrubbed by simplefin.py.
        logger.error("SimpleFIN claim error: %s", e)
        return redirect(url_for("connect") + "?error=" + str(e))
    except Exception as e:
        # Belt-and-suspenders: scrub any unexpected exception too.
        safe_msg = simplefin.strip_credentials(str(e))
        logger.error("Unexpected SimpleFIN claim error: %s", safe_msg)
        return redirect(url_for("connect") + "?error=Could not claim setup token: " + safe_msg)

    # Persist the access URL immediately so a later failure does not lose it.
    encrypted_url = crypto.encrypt_credentials({"access_url": access_url})
    db.set_setting("pending_simplefin_access_url", encrypted_url)
    if start_sync_date:
        db.set_setting("pending_simplefin_start_sync_date", start_sync_date)
    else:
        db.set_setting("pending_simplefin_start_sync_date", "")

    try:
        # balances_only=True: the picker only needs account names + balances,
        # not transactions. Saves bandwidth and doesn't pre-burn the user's
        # 24/day /accounts budget on data the picker won't display.
        account_set = simplefin.list_accounts(access_url, balances_only=True)
    except simplefin.SimpleFinSubscriptionLapsed as e:
        return redirect(url_for("connect") + "?error=" + str(e))
    except simplefin.SimpleFinAccessRevoked as e:
        db.set_setting("pending_simplefin_access_url", "")
        db.set_setting("pending_simplefin_start_sync_date", "")
        return redirect(url_for("connect") + "?error=" + str(e))
    except Exception as e:
        safe_msg = simplefin.strip_credentials(str(e))
        logger.error("SimpleFIN list_accounts failed: %s", safe_msg)
        return redirect(url_for("connect") + "?error=Could not list accounts. Please try again.")

    usable_count, err_msg = _process_simplefin_account_set(account_set)
    if usable_count == 0:
        db.set_setting("pending_simplefin_access_url", "")
        db.set_setting("pending_simplefin_start_sync_date", "")
        return redirect(url_for("connect") + "?error=" + (err_msg or "No usable accounts."))

    # Avoid /pick-account confusion if EB-style state is also lingering.
    for key in ("pending_auth_session_id", "pending_auth_accounts", "pending_auth_valid_until",
                "pending_auth_bank_name", "pending_auth_bank_country"):
        db.set_setting(key, "")

    return redirect(url_for("pick_account"))


def _process_simplefin_account_set(account_set: dict) -> tuple:
    """Filter raw SimpleFIN accounts and persist the picker-ready shape.
    Returns (usable_count, error_msg). On success, all pending_simplefin_*
    settings except access_url and start_sync_date are populated."""
    import json as _json
    raw = account_set.get("accounts", []) or []
    usable: list = []
    skipped = 0
    for acct in raw:
        currency = acct.get("currency") or ""
        if currency.startswith("http://") or currency.startswith("https://"):
            skipped += 1
            continue
        usable.append(acct)

    if not usable:
        msg = "No usable accounts found"
        if skipped:
            msg += f" ({skipped} skipped due to custom currency)"
        return 0, msg + "."

    errlist = account_set.get("errlist") or account_set.get("errors") or []
    if errlist:
        sanitized_parts: list = []
        for e in errlist:
            if isinstance(e, dict):
                txt = (e.get("msg") or e.get("code") or "").strip()
            elif isinstance(e, str):
                txt = e.strip()
            else:
                txt = ""
            if txt:
                sanitized_parts.append(txt[:200])
        if sanitized_parts:
            db.set_setting(
                "pending_simplefin_warning",
                ("SimpleFIN reported: " + "; ".join(sanitized_parts))[:600],
            )

    pick_accounts: list = []
    for acct in usable:
        org = acct.get("org") or {}
        pick_accounts.append({
            "id": acct.get("id", ""),
            "name": acct.get("name", ""),
            "currency": acct.get("currency", ""),
            "org_name": org.get("name", "") or org.get("domain", ""),
            "balance": acct.get("balance", ""),
        })

    db.set_setting("pending_simplefin_accounts", _json.dumps(pick_accounts))
    db.set_setting("pending_simplefin_raw_accounts", _json.dumps(usable))
    db.set_setting("pending_simplefin_skipped", str(skipped))
    return len(usable), None


@app.route("/connect/simplefin/resume", methods=["POST"])
def connect_simplefin_resume():
    """Retry listing accounts using a previously-claimed access URL.

    Recovers from the case where claim_setup_token() succeeded but
    list_accounts() failed: the access URL is saved encrypted in
    pending_simplefin_access_url but no accounts list exists. The setup
    token is one-shot, so the only way forward is to reuse the saved URL.
    """
    from .. import simplefin, crypto

    encrypted_url = db.get_setting("pending_simplefin_access_url")
    if not encrypted_url:
        return redirect(url_for("connect") + "?error=No pending SimpleFIN session to resume.")
    try:
        creds = crypto.decrypt_credentials(encrypted_url)
        access_url = creds.get("access_url", "") if isinstance(creds, dict) else ""
    except Exception as e:
        for key in ("pending_simplefin_access_url", "pending_simplefin_accounts",
                    "pending_simplefin_raw_accounts", "pending_simplefin_start_sync_date",
                    "pending_simplefin_warning", "pending_simplefin_skipped"):
            db.set_setting(key, "")
        return redirect(url_for("connect") + "?error=Could not recover pending SimpleFIN session.")
    if not access_url:
        for key in ("pending_simplefin_access_url",):
            db.set_setting(key, "")
        return redirect(url_for("connect") + "?error=Pending SimpleFIN session is invalid.")

    try:
        account_set = simplefin.list_accounts(access_url, balances_only=True)
    except simplefin.SimpleFinSubscriptionLapsed as e:
        return redirect(url_for("connect") + "?error=" + str(e))
    except simplefin.SimpleFinAccessRevoked as e:
        for key in ("pending_simplefin_access_url", "pending_simplefin_start_sync_date"):
            db.set_setting(key, "")
        return redirect(url_for("connect") + "?error=" + str(e))
    except Exception as e:
        safe_msg = simplefin.strip_credentials(str(e))
        logger.error("SimpleFIN resume list_accounts failed: %s", safe_msg)
        return redirect(url_for("connect") + "?error=Could not list accounts. Please try again.")

    usable_count, err_msg = _process_simplefin_account_set(account_set)
    if usable_count == 0:
        for key in ("pending_simplefin_access_url", "pending_simplefin_start_sync_date"):
            db.set_setting(key, "")
        return redirect(url_for("connect") + "?error=" + (err_msg or "No usable accounts."))

    return redirect(url_for("pick_account"))


def _finalize_simplefin_connection():
    """Save one token row per picked SimpleFIN account, claiming a license seat each.
    Rolls back saved rows if any seat claim fails."""
    import json
    from .. import crypto

    encrypted_url = db.get_setting("pending_simplefin_access_url")
    raw_accounts_json = db.get_setting("pending_simplefin_raw_accounts")
    if not encrypted_url or not raw_accounts_json:
        return redirect(url_for("connect") + "?error=Pending SimpleFIN session expired. Please regenerate a setup token.")

    selected_ids = request.form.getlist("simplefin_account_ids")
    if not selected_ids:
        return redirect(url_for("pick_account") + "?error=Please pick at least one account.")

    try:
        creds = crypto.decrypt_credentials(encrypted_url)
        access_url = creds.get("access_url", "") if isinstance(creds, dict) else ""
    except Exception as e:
        logger.error("Could not decrypt pending SimpleFIN access URL: %s", e)
        for key in ("pending_simplefin_access_url", "pending_simplefin_accounts",
                    "pending_simplefin_raw_accounts", "pending_simplefin_start_sync_date",
                    "pending_simplefin_warning", "pending_simplefin_skipped"):
            db.set_setting(key, "")
        return redirect(url_for("connect") + "?error=Could not recover pending SimpleFIN session.")
    if not access_url:
        return redirect(url_for("connect") + "?error=Pending SimpleFIN session is invalid.")

    raw_accounts = {a.get("id"): a for a in json.loads(raw_accounts_json) if a.get("id")}

    # If the user is reconnecting after access was revoked, replace the stale
    # token rows. Preserve their transaction namespace by account ID so
    # duplicate detection survives a SimpleFIN conn_id change on reconnect.
    revoked_setting = (db.get_setting("simplefin_access_revoked") or "").strip()
    revoked_ids = {x for x in revoked_setting.split(",") if x}
    preserved_connection_ids: dict = {}
    for tid_str in revoked_ids:
        try:
            token = db.get_token_by_id(int(tid_str))
            if token:
                account_id = token.get("provider_account_id") or ""
                connection_id = (
                    token.get("provider_connection_id")
                    or db.get_simplefin_connection_id_from_transactions(account_id)
                )
                if account_id and connection_id:
                    preserved_connection_ids[account_id] = connection_id
                db.clear_token_by_id(int(tid_str))
        except (TypeError, ValueError):
            continue
    if revoked_ids:
        db.set_setting("simplefin_access_revoked", "")

    capacity_error = _ensure_global_bank_capacity(db.get_all_tokens(), new_seats=len(selected_ids))
    if capacity_error:
        return redirect(url_for("connect") + "?error=" + capacity_error)

    start_sync_date = db.get_setting("pending_simplefin_start_sync_date") or ""

    saved_token_ids: list = []
    for account_id in selected_ids:
        acct = raw_accounts.get(account_id)
        if not acct:
            continue
        org = acct.get("org") or {}
        org_name = org.get("name") or org.get("domain") or ""
        bank_name = (acct.get("name") or org_name or "SimpleFIN account")[:200]
        token_id = db.save_simplefin_token(
            bank_name=bank_name,
            bank_country="",
            provider_account_id=account_id,
            provider_credentials=encrypted_url,
            start_sync_date=start_sync_date,
            provider_connection_id=preserved_connection_ids.get(account_id) or acct.get("conn_id") or "default",
        )
        token = db.get_token_by_id(token_id)
        seat_error = _claim_bank_seat(token)
        if seat_error:
            db.clear_token_by_id(token_id)
            for tid in saved_token_ids:
                db.clear_token_by_id(tid)
            return redirect(url_for("connect") + "?error=" + seat_error)
        saved_token_ids.append(token_id)

    for key in ("pending_simplefin_access_url", "pending_simplefin_accounts",
                "pending_simplefin_raw_accounts", "pending_simplefin_start_sync_date",
                "pending_simplefin_warning", "pending_simplefin_skipped"):
        db.set_setting(key, "")
    db.set_setting("simplefin_subscription_lapsed", "")
    db.set_setting("simplefin_access_revoked", "")

    _start_scheduler_if_ready()
    import threading
    threading.Thread(target=sync.run, daemon=True).start()
    return redirect(url_for("status", success=1))

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
    bank_seat_error, bank_seat_result = _get_bank_seat_error(all_tokens)
    page       = request.args.get("page", 1, type=int)
    log_data   = db.get_sync_log_page(page=page, per_page=5)
    syncs      = log_data["syncs"]
    days_left  = enablebanking.check_token_expiry()
    last_sync  = db.get_last_sync()

    licence_sync_failed = False
    val = licence.validate()
    if not val.get("valid") and not val.get("offline"):
        licence_sync_failed = True

    act_info = licence.get_activation_info()
    activation_usage = act_info["usage"]
    activation_limit = act_info["limit"]

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

    # Review prompt logic
    show_review_prompt = False
    if not act_info.get("is_trial", False) and not db.get_setting("review_dismissed") and not db.get_setting("review_submitted"):
        first_sync = db.get_setting("first_sync_date")
        if not first_sync and syncs:
            # Fall back to oldest sync log entry
            conn = db.get_conn()
            row = conn.execute("SELECT MIN(ran_at) FROM sync_log").fetchone()
            conn.close()
            first_sync = row[0] if row and row[0] else None
        if first_sync:
            try:
                from datetime import datetime
                first_dt = datetime.fromisoformat(first_sync.replace("Z", "+00:00") if "Z" in first_sync else first_sync)
                if (datetime.now() - first_dt.replace(tzinfo=None)).days >= 7:
                    show_review_prompt = True
            except Exception:
                pass

    # Build balance info from tokens
    balances = []
    for t in all_tokens:
        if t.get("last_balance"):
            try:
                balances.append({
                    "bank": t.get("bank_name", "Unknown"),
                    "amount": float(t["last_balance"]),
                    "currency": t.get("last_balance_currency", "EUR"),
                })
            except (ValueError, TypeError):
                pass

    has_simplefin = any(t.get("provider") == "simplefin" for t in all_tokens)
    has_enablebanking = any(
        t.get("provider") not in ("simplefin",) and t.get("sync_mode") != "balance"
        for t in all_tokens
    )
    sf_lapsed = db.get_setting("simplefin_subscription_lapsed") == "1"
    sf_revoked_setting = (db.get_setting("simplefin_access_revoked") or "").strip()
    sf_revoked_token_ids = {x for x in sf_revoked_setting.split(",") if x}
    return render_template("status.html",
        tokens=tokens,
        all_tokens=all_tokens,
        balances=balances,
        syncs=syncs,
        days_left=days_left,
        last_sync=last_sync,
        sync_time=_cfg().SYNC_TIME,
        sync_frequency=_cfg().SYNC_FREQUENCY or "24",
        sync_times=_get_sync_times(),
        timezone=_cfg().TIMEZONE or "",
        notify_email=_cfg().NOTIFY_EMAIL,
        activation_usage=activation_usage,
        activation_limit=activation_limit,
        bank_seat_usage=(bank_seat_result or {}).get("used", act_info.get("bank_seat_usage", 0)),
        bank_account_limit=(bank_seat_result or {}).get("limit", act_info.get("bank_account_limit", 2)),
        is_trial=act_info.get("is_trial", False),
        trial_expires_at=act_info.get("expires_at", "")[:10] if act_info.get("expires_at") else None,
        licence_sync_failed=licence_sync_failed,
        licence_limit_reached=(licence_sync_failed and activation_usage >= activation_limit and activation_limit > 0),
        bank_seat_error=bank_seat_error,
        page=log_data["page"],
        total_pages=log_data["total_pages"],
        update_mode=db.get_setting("update_mode"),
        update_available=db.get_setting("update_available") == "1",
        total_tx=total_tx,
        streak=streak,
        fun_message=fun_message,
        show_review_prompt=show_review_prompt,
        has_simplefin=has_simplefin,
        has_enablebanking=has_enablebanking,
        simplefin_subscription_lapsed=sf_lapsed,
        simplefin_access_revoked_token_ids=sf_revoked_token_ids,
        active="status",
    )

_sync_running = False

@app.route("/sync/now", methods=["POST"])
def sync_now():
    global _sync_running
    import threading
    _sync_running = True
    def _run():
        global _sync_running
        try:
            sync.run()
        finally:
            _sync_running = False
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/api/sync-status")
def sync_status():
    return jsonify({"running": _sync_running})

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
    result = _sync_bank_seats(db.get_all_tokens())
    if not result.get("ok") and not result.get("network"):
        logger.warning("Failed to release Klartion bank seat: %s", result.get("error"))
    return redirect(url_for("connect"))

@app.route("/reset-sync", methods=["POST"])
def reset_sync():
    token_id   = request.form.get("token_id")
    reset_date = request.form.get("reset_date", "").strip()
    if token_id:
        updates = {"last_sync_at": ""}
        if reset_date:
            updates["start_sync_date"] = reset_date
        db.update_token_fields(int(token_id), **updates)
        logger.info("Reset sync state for token %s (start_date=%s)", token_id, reset_date)
    return redirect(url_for("connect"))

@app.route("/toggle-skip-pending", methods=["POST"])
def toggle_skip_pending():
    token_id = request.form.get("token_id")
    if token_id:
        skip = 1 if request.form.get("skip_pending") == "1" else 0
        db.update_token_fields(int(token_id), skip_pending=skip)
        logger.info("Set skip_pending=%s for token %s", skip, token_id)
    return redirect(url_for("connect"))

# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------

@app.route("/review/dismiss", methods=["POST"])
def review_dismiss():
    db.set_setting("review_dismissed", "1")
    return redirect(url_for("status"))

@app.route("/review/submit", methods=["POST"])
def review_submit():
    import requests as _requests
    rating      = request.form.get("rating", "").strip()
    review_text = request.form.get("review", "").strip()
    name        = request.form.get("name", "").strip()
    key         = _cfg().LICENCE_KEY
    if not rating or not review_text or not key:
        return redirect(url_for("status"))
    try:
        resp = _requests.post("https://api.klartion.com/review", json={
            "license_key": key,
            "name": name or None,
            "rating": int(rating),
            "review": review_text,
        }, timeout=10)
        if resp.status_code in (200, 201):
            db.set_setting("review_submitted", "1")
        else:
            logger.warning("Review submit failed: %s %s", resp.status_code, resp.text)
            db.set_setting("review_submitted", "1")
    except Exception as e:
        logger.error("Review submit error: %s", e)
        db.set_setting("review_submitted", "1")
    return redirect(url_for("status"))

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

def _sanitize_logs(text):
    import re
    text = re.sub(r'\b([A-Z]{2}\d{2})\w{4,}(\w{4})\b', r'\1****\2', text)
    text = re.sub(r'\b(\w{2})\w*(@\w+\.\w+)', r'\1***\2', text)
    text = re.sub(r"\[?\{'account_id':.*?\}\]?", '[account data redacted]', text)
    return text

@app.route("/api/logs")
def api_logs():
    import subprocess
    lines = request.args.get("lines", "200")
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", lines, CONTAINER_NAME],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr
        output = _sanitize_logs(output)
        version = subprocess.run(
            ["docker", "inspect", "--format", "{{.Image}}", CONTAINER_NAME],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()[:19].replace("sha256:", "")
        return jsonify({"logs": output, "version": version})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
        accept = ", ".join([
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        ])
        manifest_resp = _req.head(
            f"https://registry-1.docker.io/v2/{repo}/manifests/{tag}",
            headers={"Authorization": f"Bearer {token}", "Accept": accept},
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
    # Try Watchtower first, fall back to helper container
    import requests as _requests
    try:
        resp = _requests.get(
            "http://klartion-watchtower:8080/v1/update",
            headers={"Authorization": "Bearer klartion-update"},
            timeout=120,
        )
        logger.info("Watchtower update response: %s %s", resp.status_code, resp.text.strip())
        if resp.status_code == 200:
            db.set_setting("update_available", "0")
            return jsonify({"updating": True})
    except Exception:
        logger.info("Watchtower not available, falling back to helper container")
    # Fallback: pull image and spawn a helper container to run docker compose
    import subprocess, os, json as _json
    if not os.path.exists("/var/run/docker.sock"):
        return jsonify({"error": "Docker socket not mounted."}), 400
    try:
        subprocess.run(["docker", "pull", IMAGE_NAME], capture_output=True, text=True, timeout=120)
        mounts_json = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Mounts}}", CONTAINER_NAME],
            capture_output=True, text=True, timeout=5
        ).stdout.strip()
        compose_host_path = ""
        for m in _json.loads(mounts_json or "[]"):
            if m.get("Destination") == "/compose":
                compose_host_path = m["Source"]
                break
        if not compose_host_path:
            return jsonify({"error": "Could not update. Try running: docker compose pull && docker compose up -d"}), 400
        compose_file = f"{compose_host_path}/docker-compose.yml"
        result = subprocess.run([
            "docker", "run", "-d", "--rm",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", f"{compose_host_path}:{compose_host_path}:ro",
            IMAGE_NAME, "sh", "-c",
            f"sleep 2 && docker compose -f '{compose_file}' up -d --force-recreate",
        ], capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            logger.error("Failed to start update helper: %s", result.stderr.strip())
            return jsonify({"error": "Failed to start update. Try running: docker compose pull && docker compose up -d"}), 500
        db.set_setting("update_available", "0")
        return jsonify({"updating": True})
    except Exception as e:
        logger.error("Fallback update failed: %s", e)
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

def _get_sync_times():
    sync_time = _cfg().SYNC_TIME or "08:00"
    frequency = int(_cfg().SYNC_FREQUENCY or "24")
    if frequency == 0:
        return "Manual only"
    try:
        h, m = int(sync_time.split(":")[0]), int(sync_time.split(":")[1])
    except Exception:
        h, m = 8, 0
    times = []
    for i in range(0, 24, frequency):
        t_h = (h + i) % 24
        times.append(f"{t_h:02d}:{m:02d}")
    return ", ".join(times)

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

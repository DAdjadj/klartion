import hashlib
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from . import config, db, enablebanking, notion, email_notify, licence, crypto, simplefin

logger = logging.getLogger(__name__)

_sync_lock = threading.Lock()

def _is_booked_status(status: str) -> bool:
    return (status or "").upper() in {"BOOK", "BOOKED"}

def _scoped_tx_id(account_uid: str, tx: dict) -> str:
    return f"{account_uid}:{_get_tx_id(tx)}"

def run(trigger: str = "unknown"):
    """
    Main sync orchestrator. Called by the scheduler daily.
    Uses a lock to prevent duplicate concurrent syncs (e.g. catch-up + post-auth
    sync both firing at the same time, which wastes Enable Banking API calls).
    Returns (success: bool, tx_count: int, message: str)
    """
    if not _sync_lock.acquire(blocking=False):
        logger.info("Sync already in progress, skipping duplicate run. (trigger=%s)", trigger)
        return True, 0, "Skipped (already running)"
    try:
        return _run_impl(trigger)
    finally:
        _sync_lock.release()


def _run_impl(trigger: str):
    """Internal sync implementation — callers must hold _sync_lock."""
    logger.info("Starting sync run... (trigger=%s)", trigger)

    # 1. Licence check
    result = licence.validate()
    if not result["valid"]:
        msg = f"Licence invalid: {result['error']}"
        logger.error(msg)
        db.log_sync("failure", message=msg)
        email_notify.send_failure(msg)
        return False, 0, msg

    # 2. Load all connected bank accounts
    all_tokens = db.get_all_tokens()
    if not all_tokens:
        msg = "No bank connection found. Please connect your bank."
        logger.error(msg)
        db.log_sync("failure", message=msg)
        email_notify.send_failure(msg)
        return False, 0, msg

    seat_result = licence.sync_bank_seats(all_tokens)
    if not seat_result.get("ok"):
        if seat_result.get("network"):
            logger.warning("Bank seat verification skipped: %s", seat_result.get("error"))
        else:
            msg = seat_result.get("error") or "Bank account limit reached for this licence."
            logger.error(msg)
            db.set_setting("license_bank_limit_error", msg)
            db.log_sync("failure", message=msg)
            email_notify.send_failure(msg)
            return False, 0, msg
    else:
        db.set_setting("license_bank_limit_error", "")

    # 2b. Ensure Balance property exists on Notion database
    notion.ensure_balance_property()

    # 2c. Learn category rules from Notion (user's manual edits)
    try:
        learned_rules = notion.fetch_category_rules()
        if learned_rules:
            db.save_category_rules(learned_rules)
    except Exception as e:
        logger.warning("Could not refresh category rules: %s", e)
    category_rules = db.get_category_rules()

    total_written = 0
    errors = []
    balance_lines = []

    sf_data = _prefetch_simplefin_data(all_tokens) if any(t.get("provider") == "simplefin" for t in all_tokens) else {}

    for i, tokens in enumerate(all_tokens):
        if i > 0:
            time.sleep(2)
        sync_mode = tokens.get("sync_mode")
        provider = tokens.get("provider")
        if sync_mode == "balance":
            success, count, label = _sync_balance_token(tokens)
            if success:
                total_written += count
                balance_lines.append(label)
            else:
                errors.append(label)
            continue
        if provider == "simplefin":
            written, token_errors, token_balance_lines = _sync_simplefin_token(tokens, category_rules, sf_data)
        else:
            written, token_errors, token_balance_lines = _sync_enablebanking_token(tokens, category_rules)
        total_written += written
        errors.extend(token_errors)
        balance_lines.extend(token_balance_lines)

    # 11. Log and notify
    if errors:
        msg = f"{total_written} transactions written. Errors: {'; '.join(errors)}"
        db.log_sync("partial" if total_written > 0 else "failure", tx_count=total_written, message=msg)
        email_notify.send_failure(msg)
    else:
        db.log_sync("success", tx_count=total_written)
        email_notify.send_success(total_written, balance_lines=balance_lines)

    logger.info("Sync complete. %d transactions written.", total_written)

    # 12. Check for updates silently
    try:
        _check_for_update()
    except Exception:
        pass

    return len(errors) == 0, total_written, "OK"


def _sync_balance_token(tokens: dict):
    """
    Sync a balance-only provider token. Fetches the portfolio value,
    archives the previous Notion row for this provider, and creates a
    new row with today's balance. This keeps one row per provider in the
    Notion database representing the current portfolio value.
    Returns (success: bool, tx_count: int, label: str).
    """
    from .providers import get_provider, PROVIDERS
    provider_name = tokens.get("provider", "")
    bank_label = tokens.get("bank_name", provider_name)
    token_id = tokens["id"]

    if provider_name not in PROVIDERS:
        return False, 0, f"{bank_label}: Unknown provider '{provider_name}'"

    try:
        provider = get_provider(provider_name)
    except Exception as e:
        return False, 0, f"{bank_label}: {e}"

    try:
        credentials = crypto.decrypt_credentials(tokens.get("provider_credentials", ""))
    except Exception as e:
        logger.error("Failed to decrypt credentials for %s: %s", bank_label, e)
        return False, 0, f"{bank_label}: Could not decrypt credentials"

    try:
        balance = provider.get_balance(credentials)
        currency = provider.get_currency(credentials)
    except Exception as e:
        logger.error("Failed to fetch balance from %s: %s", bank_label, e)
        return False, 0, f"{bank_label}: {e}"

    balance_float = float(balance)
    logger.info("%s balance: %s %s", bank_label, balance_float, currency)

    # Archive the previous balance row in Notion for this provider
    tx_id_prefix = f"provider:{provider_name}:"
    known = db.get_known_tx_ids(tx_id_prefix=tx_id_prefix)
    for old_tx_id in known:
        conn = db.get_conn()
        row = conn.execute(
            "SELECT notion_page_id FROM transactions WHERE tx_id = ?", (old_tx_id,)
        ).fetchone()
        conn.close()
        if row and row["notion_page_id"]:
            try:
                from notion_client import Client
                client = Client(auth=config.NOTION_API_KEY)
                client.pages.update(page_id=row["notion_page_id"], archived=True)
            except Exception as e:
                logger.warning("Could not archive old balance page %s: %s", row["notion_page_id"], e)
        conn = db.get_conn()
        conn.execute("DELETE FROM transactions WHERE tx_id = ?", (old_tx_id,))
        conn.commit()
        conn.close()

    # Write a single row to Notion with the current portfolio value
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tx_id = f"provider:{provider_name}:{today}"
    tx_count = 0
    if balance_float != 0:
        tx = {
            "tx_id": tx_id,
            "date": today,
            "amount": balance_float,
            "currency": currency,
            "merchant": provider.display_name,
            "category": "Investment",
            "reference": f"{provider.display_name} portfolio value",
            "direction": "in",
            "status": "Cleared",
            "bank_name": provider.display_name,
            "balance": balance_float,
        }
        try:
            notion_page_id = notion.write_transaction(tx)
            db.upsert_transaction(tx_id=tx_id, notion_page_id=notion_page_id, status="cleared")
            tx_count = 1
        except Exception as e:
            logger.error("Failed to write balance to Notion for %s: %s", bank_label, e)
            return False, 0, f"{bank_label}: Failed to write to Notion: {e}"

    db.update_token_fields(token_id, last_balance=str(balance_float), last_balance_currency=currency)
    db.update_token_fields(token_id, last_sync_at=datetime.now(timezone.utc).isoformat())

    label = f"{bank_label}: {balance_float:,.2f} {currency}"
    logger.info("Synced %s", label)
    return True, tx_count, label


def _sync_enablebanking_token(tokens: dict, category_rules: dict) -> tuple[int, list, list]:
    """Sync one Enable Banking token (one row per linked account).
    Returns (written, errors, balance_lines)."""
    written = 0
    errors: list = []
    balance_lines: list = []

    bank_label = f"{tokens.get('bank_name', 'Unknown')} ({tokens.get('bank_country', '')})"
    token_id = tokens["id"]
    session_id = tokens["session_id"]
    account_uid = tokens.get("access_token")

    if tokens.get("expires_at"):
        try:
            expires = datetime.fromisoformat(tokens["expires_at"].replace("Z", "+00:00"))
            days_left = max(0, (expires - datetime.now(timezone.utc)).days)
            if days_left <= 14:
                email_notify.send_token_expiry_warning(tokens.get("bank_name", "your bank"), days_left)
        except Exception:
            pass

    if not account_uid:
        errors.append(f"{bank_label}: No account UID found")
        return written, errors, balance_lines

    last_sync_at = tokens.get("last_sync_at")
    start_sync_date = tokens.get("start_sync_date") or db.get_setting("start_sync_date")
    if last_sync_at:
        parsed_last_sync = datetime.fromisoformat(last_sync_at.replace("Z", "+00:00") if "Z" in last_sync_at else last_sync_at)
        date_from = (parsed_last_sync - timedelta(days=2)).strftime("%Y-%m-%d")
    elif start_sync_date:
        date_from = start_sync_date
    else:
        date_from = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    date_to = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    logger.info("Syncing %s: %s to %s", bank_label, date_from, date_to)
    session_state = enablebanking.get_session_status(session_id)
    logger.info("EB session probe %s: %s", bank_label, session_state)
    # Log time since authorization — key diagnostic for Comdirect failures
    auth_time_str = session_state.get("authorized") or ""
    if auth_time_str:
        try:
            auth_dt = datetime.fromisoformat(auth_time_str.replace("Z", "+00:00"))
            secs_since_auth = int((datetime.now(timezone.utc) - auth_dt).total_seconds())
            logger.info("Time since authorization: %ds (%dm %ds)", secs_since_auth, secs_since_auth // 60, secs_since_auth % 60)
        except Exception:
            pass

    try:
        all_transactions = enablebanking.get_transactions(session_id, account_uid, date_from, date_to)
    except Exception as e:
        import re
        err = re.sub(r" for url: https?://\S+", "", str(e))
        errors.append(f"{bank_label}: {err}")
        logger.error("Failed to fetch transactions for %s: %s", bank_label, err)
        return written, errors, balance_lines

    logger.info("Fetched %d transactions from %s", len(all_transactions), bank_label)

    tx_prefix = f"{account_uid}:"
    known_ids = db.get_known_tx_ids(tx_id_prefix=tx_prefix)
    new_transactions = [t for t in all_transactions if _scoped_tx_id(account_uid, t) not in known_ids]
    if tokens.get("skip_pending"):
        before = len(new_transactions)
        new_transactions = [t for t in new_transactions if _is_booked_status(t.get("status"))]
        if before != len(new_transactions):
            logger.info("Skipped %d pending transactions (skip_pending enabled)", before - len(new_transactions))
    logger.info("%d new transactions after deduplication", len(new_transactions))

    _reconcile_pending(account_uid, all_transactions)

    current_balance = None
    current_balance_currency = None
    try:
        balances = enablebanking.get_balances(session_id, account_uid)
        current_balance, current_balance_currency = _extract_balance(balances)
        if current_balance is not None:
            db.update_token_fields(token_id, last_balance=str(current_balance), last_balance_currency=current_balance_currency)
            balance_lines.append(f"{tokens.get('bank_name', 'Unknown')}: {current_balance:,.2f} {current_balance_currency}")
            logger.info("Balance for %s: %s %s", bank_label, current_balance, current_balance_currency)
    except Exception as e:
        logger.warning("Could not fetch balance for %s: %s", bank_label, e)

    for tx in new_transactions:
        try:
            normalised = _normalise(tx, category_rules=category_rules)
            if not normalised["date"]:
                logger.warning(
                    "Skipping transaction with no date (likely an unposted card pre-authorisation): %s",
                    _get_tx_id(tx),
                )
                continue
            normalised["bank_name"] = tokens.get("bank_name", "")
            if current_balance is not None:
                normalised["balance"] = current_balance
            notion_page_id = notion.write_transaction(normalised)
            db.upsert_transaction(
                tx_id=_scoped_tx_id(account_uid, tx),
                notion_page_id=notion_page_id,
                status=normalised["status"].lower(),
            )
            written += 1
        except Exception as e:
            logger.error("Failed to write transaction %s: %s", _get_tx_id(tx), e)

    db.update_token_fields(token_id, last_sync_at=datetime.now(timezone.utc).isoformat())
    logger.info("Synced %d transactions from %s", written, bank_label)

    return written, errors, balance_lines


def _simplefin_access_url_hash(access_url: str) -> str:
    return hashlib.sha256(access_url.encode()).hexdigest()[:16]


def _simplefin_decrypt_access_url(token: dict) -> str:
    creds = crypto.decrypt_credentials(token.get("provider_credentials") or "")
    return creds.get("access_url", "") if isinstance(creds, dict) else ""


def _simplefin_token_date_from(token: dict) -> datetime:
    """Compute the date_from cutoff for a SimpleFIN token using the same
    rules as EB: last_sync_at - 2 days, else start_sync_date, else 30 days ago."""
    last_sync_at = token.get("last_sync_at")
    if last_sync_at:
        parsed = datetime.fromisoformat(last_sync_at.replace("Z", "+00:00") if "Z" in last_sync_at else last_sync_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed - timedelta(days=2)
    start_sync_date = token.get("start_sync_date") or db.get_setting("start_sync_date")
    if start_sync_date:
        try:
            return datetime.strptime(start_sync_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc) - timedelta(days=30)


def _prefetch_simplefin_data(all_tokens: list) -> dict:
    """For each unique SimpleFIN access URL, make one fetch_accounts_chunked
    call covering the widest date range any sibling token needs. Returns
    {url_hash: {ok: bool, account_set?: dict, error?: str, msg?: str}}."""
    groups: dict = {}
    for token in all_tokens:
        if token.get("provider") != "simplefin":
            continue
        try:
            access_url = _simplefin_decrypt_access_url(token)
        except Exception as e:
            logger.error("SimpleFIN prefetch: could not decrypt token %s: %s", token.get("id"), e)
            continue
        if not access_url:
            continue
        url_hash = _simplefin_access_url_hash(access_url)
        groups.setdefault(url_hash, {"access_url": access_url, "tokens": []})
        groups[url_hash]["tokens"].append(token)

    results: dict = {}
    for url_hash, group in groups.items():
        access_url = group["access_url"]
        tokens_in_group = group["tokens"]
        earliest_from = min(_simplefin_token_date_from(t) for t in tokens_in_group)
        # Push end-date 7 days into the future. Some institutions (Capital One,
        # Chase) post pre-authorization transactions with a `posted` timestamp
        # in the future; a "now" cutoff would skip them on the day they're
        # authorized and have to wait for them to drift back below now.
        date_to_dt = datetime.now(timezone.utc) + timedelta(days=7)
        try:
            account_set = simplefin.fetch_accounts_chunked(
                access_url,
                start_epoch=int(earliest_from.timestamp()),
                end_epoch=int(date_to_dt.timestamp()),
            )
            results[url_hash] = {"ok": True, "account_set": account_set}
        except simplefin.SimpleFinSubscriptionLapsed as e:
            db.set_setting("simplefin_subscription_lapsed", "1")
            results[url_hash] = {"ok": False, "error": "subscription_lapsed", "msg": str(e)}
        except simplefin.SimpleFinAccessRevoked as e:
            # When a shared access URL is revoked, EVERY sibling token is
            # affected. Persist the full set as a comma-separated list so
            # the UI badges all of them, not just the last one written.
            existing = (db.get_setting("simplefin_access_revoked") or "").strip()
            revoked_ids = {x for x in existing.split(",") if x}
            for t in tokens_in_group:
                revoked_ids.add(str(t["id"]))
            db.set_setting("simplefin_access_revoked", ",".join(sorted(revoked_ids)))
            results[url_hash] = {"ok": False, "error": "access_revoked", "msg": str(e)}
        except Exception as e:
            # Scrub defensively in case any non-SimpleFinError escape ever
            # carries an embedded access URL through str(e).
            safe_msg = simplefin.strip_credentials(str(e))
            logger.error("SimpleFIN prefetch failed for %s: %s", url_hash, safe_msg)
            results[url_hash] = {"ok": False, "error": "other", "msg": safe_msg}
    if groups and len(results) == len(groups) and all(r.get("ok") for r in results.values()):
        db.set_setting("simplefin_subscription_lapsed", "")
    return results


def _reconcile_pending_simplefin(prefix: str, account: dict):
    """SimpleFIN-aware version of _reconcile_pending. The "booked" predicate
    is `pending != True`; tx IDs are namespaced via the supplied prefix."""
    pending = db.get_pending_transactions(tx_id_prefix=prefix)
    if not pending:
        return
    transactions = account.get("transactions", [])
    fetched_ids = {f"{prefix}{t.get('id')}" for t in transactions if t.get("id")}
    booked_ids = {f"{prefix}{t.get('id')}" for t in transactions if t.get("id") and not t.get("pending")}

    for record in pending:
        tx_id = record["tx_id"]
        notion_page_id = record["notion_page_id"]
        if tx_id in booked_ids:
            notion.update_transaction_status(notion_page_id, "Cleared")
            db.upsert_transaction(tx_id, notion_page_id, "cleared")
            logger.info("Marked transaction %s as Cleared", tx_id)
        elif tx_id not in fetched_ids:
            notion.update_transaction_status(notion_page_id, "Cancelled")
            db.upsert_transaction(tx_id, notion_page_id, "cancelled")
            logger.info("Marked transaction %s as Cancelled", tx_id)


def _sync_simplefin_token(tokens: dict, category_rules: dict, sf_data: dict) -> tuple[int, list, list]:
    """Sync one SimpleFIN account token. Pulls its account+transactions from
    the pre-fetched sf_data dict (one /accounts call per access URL per run)."""
    written = 0
    errors: list = []
    balance_lines: list = []

    bank_label = tokens.get("bank_name", "Unknown")
    token_id = tokens["id"]
    account_id = (tokens.get("provider_account_id") or "").strip()

    if not account_id:
        errors.append(f"{bank_label}: SimpleFIN account ID missing.")
        return written, errors, balance_lines

    try:
        access_url = _simplefin_decrypt_access_url(tokens)
    except Exception as e:
        logger.error("SimpleFIN: failed to decrypt credentials for %s: %s", bank_label, e)
        errors.append(f"{bank_label}: could not decrypt SimpleFIN credentials.")
        return written, errors, balance_lines

    if not access_url:
        errors.append(f"{bank_label}: SimpleFIN access URL missing.")
        return written, errors, balance_lines

    url_hash = _simplefin_access_url_hash(access_url)
    group_result = sf_data.get(url_hash)
    if not group_result:
        errors.append(f"{bank_label}: SimpleFIN data not prefetched.")
        return written, errors, balance_lines

    if not group_result.get("ok"):
        err_kind = group_result.get("error", "other")
        msg = group_result.get("msg", "")
        if err_kind == "subscription_lapsed":
            errors.append(f"{bank_label}: SimpleFIN subscription lapsed. Renew at bridge.simplefin.org.")
        elif err_kind == "access_revoked":
            errors.append(f"{bank_label}: SimpleFIN access revoked. Regenerate a setup token at bridge.simplefin.org.")
        else:
            errors.append(f"{bank_label}: SimpleFIN: {msg}")
        return written, errors, balance_lines

    account_set = group_result["account_set"]
    account = next((a for a in account_set.get("accounts", []) if a.get("id") == account_id), None)
    if account is None:
        errors.append(f"{bank_label}: SimpleFIN account {account_id} not in response.")
        return written, errors, balance_lines

    # Surface errlist entries that mention this account or its connection.
    # The current spec uses dict entries with `code` / `msg` / `account_id` /
    # `conn_id`. The deprecated v1 format uses bare strings ("Connection to X
    # may need attention", "Payment required.") — substring-match those
    # against the bank label so the right account gets the right warning.
    conn_id = account.get("conn_id") or "default"
    for err in account_set.get("errlist", []) or []:
        if isinstance(err, dict):
            if err.get("account_id") == account_id or err.get("conn_id") == conn_id:
                sanitized = (err.get("msg") or err.get("code") or "")[:200]
                if sanitized:
                    errors.append(f"{bank_label}: {sanitized}")
        elif isinstance(err, str):
            lowered = err.lower()
            if (
                bank_label.lower() in lowered
                or "needs attention" in lowered
                or "may need attention" in lowered
            ):
                errors.append(f"{bank_label}: {err.strip()[:200]}")

    stored_namespace_conn_id = (tokens.get("provider_connection_id") or "").strip()
    namespace_conn_id = stored_namespace_conn_id or db.get_simplefin_connection_id_from_transactions(account_id)
    if not namespace_conn_id:
        namespace_conn_id = conn_id
    if namespace_conn_id and not stored_namespace_conn_id:
        db.update_token_fields(token_id, provider_connection_id=namespace_conn_id)
    prefix = f"simplefin:{namespace_conn_id}:{account_id}:"

    # Filter transactions to this token's date range — the prefetch may have
    # used a wider window covering a sibling token's earlier start date.
    # Per spec, `posted` may be 0 for pending transactions; never filter those
    # out by date. Use transacted_at as a fallback when present.
    date_from_dt = _simplefin_token_date_from(tokens)
    date_from_epoch = int(date_from_dt.timestamp())
    raw_transactions = []
    for t in account.get("transactions", []) or []:
        posted = t.get("posted")
        transacted_at = t.get("transacted_at")
        if isinstance(posted, (int, float)) and posted > 0:
            if posted >= date_from_epoch:
                raw_transactions.append(t)
        elif isinstance(transacted_at, (int, float)) and transacted_at > 0:
            if transacted_at >= date_from_epoch:
                raw_transactions.append(t)
        else:
            # No usable timestamp — pending/unknown date. Keep it; downstream
            # dedup by tx id will prevent duplicates on later syncs.
            raw_transactions.append(t)

    logger.info("SimpleFIN %s: %d transactions in window since %s", bank_label, len(raw_transactions), date_from_dt.date())

    known_ids = db.get_known_tx_ids(tx_id_prefix=prefix)
    new_transactions = [
        t for t in raw_transactions
        if t.get("id") and f"{prefix}{t['id']}" not in known_ids
    ]
    if tokens.get("skip_pending"):
        before = len(new_transactions)
        new_transactions = [t for t in new_transactions if not t.get("pending")]
        if before != len(new_transactions):
            logger.info("Skipped %d pending SimpleFIN transactions (skip_pending enabled)", before - len(new_transactions))
    logger.info("%d new SimpleFIN transactions for %s after dedup", len(new_transactions), bank_label)

    _reconcile_pending_simplefin(prefix, account)

    # Balance — skip entirely for custom-currency accounts (URL-style currency
    # like points/miles). They're already filtered at pick time, but be
    # defensive in case one slipped through. Writing a custom-currency balance
    # as if it were USD would mislead users.
    current_balance = None
    raw_currency = account.get("currency") or "USD"
    if raw_currency.startswith("http"):
        logger.info("SimpleFIN %s: skipping balance display for custom-currency account", bank_label)
    else:
        current_balance_currency = raw_currency
        balance_str = account.get("balance")
        if balance_str is not None:
            try:
                current_balance = float(Decimal(str(balance_str)))
                db.update_token_fields(
                    token_id,
                    last_balance=str(current_balance),
                    last_balance_currency=current_balance_currency,
                )
                balance_lines.append(f"{bank_label}: {current_balance:,.2f} {current_balance_currency}")
            except (InvalidOperation, ValueError, TypeError):
                current_balance = None

    for tx in new_transactions:
        try:
            normalised = _normalise_simplefin(tx, account, category_rules=category_rules)
            normalised["bank_name"] = bank_label
            if current_balance is not None:
                normalised["balance"] = current_balance
            scoped = f"{prefix}{tx['id']}"
            normalised["tx_id"] = scoped
            notion_page_id = notion.write_transaction(normalised)
            db.upsert_transaction(
                tx_id=scoped,
                notion_page_id=notion_page_id,
                status=normalised["status"].lower(),
            )
            written += 1
        except Exception as e:
            logger.error("Failed to write SimpleFIN tx %s: %s", tx.get("id"), e)

    db.update_token_fields(token_id, last_sync_at=datetime.now(timezone.utc).isoformat())
    logger.info("Synced %d SimpleFIN transactions from %s", written, bank_label)

    return written, errors, balance_lines


def _reconcile_pending(account_uid: str, all_transactions: list):
    """
    Check previously imported pending transactions against the new batch.
    Update Notion rows that have been cleared or cancelled.
    """
    pending = db.get_pending_transactions(tx_id_prefix=f"{account_uid}:")
    if not pending:
        return

    booked_ids  = {_scoped_tx_id(account_uid, t) for t in all_transactions if _is_booked_status(t.get("status"))}
    fetched_ids = {_scoped_tx_id(account_uid, t) for t in all_transactions}

    for record in pending:
        tx_id          = record["tx_id"]
        notion_page_id = record["notion_page_id"]

        if tx_id in booked_ids:
            # Transaction has settled
            notion.update_transaction_status(notion_page_id, "Cleared")
            db.upsert_transaction(tx_id, notion_page_id, "cleared")
            logger.info("Marked transaction %s as Cleared", tx_id)
        elif tx_id not in fetched_ids:
            # Transaction disappeared (declined/cancelled)
            notion.update_transaction_status(notion_page_id, "Cancelled")
            db.upsert_transaction(tx_id, notion_page_id, "cancelled")
            logger.info("Marked transaction %s as Cancelled", tx_id)


def _get_tx_id(tx: dict) -> str:
    return (
        tx.get("transaction_id")
        or tx.get("entry_reference")
        or tx.get("reference")
        or f"{tx.get('booking_date', '')}-{tx.get('transaction_amount', {}).get('amount', '')}"
    )


def _normalise(tx: dict, category_rules: dict = None) -> dict:
    """
    Normalise an Enable Banking transaction into Klartion's internal format.
    """
    amount_obj = tx.get("transaction_amount") or {}
    amount     = float(amount_obj.get("amount", 0) or 0)
    currency   = amount_obj.get("currency", "EUR")
    indicator  = tx.get("credit_debit_indicator", "DBIT")
    direction  = "in" if indicator == "CRDT" else "out"
    amount     = abs(amount)

    # Direction: DBIT = debit (money out), CRDT = credit (money in)
    indicator = tx.get("credit_debit_indicator", "DBIT")
    if indicator == "DBIT":
        merchant = (
            (tx.get("creditor") or {}).get("name")
            or tx.get("creditor_name")
            or (tx.get("remittance_information") or [None])[0]
            or tx.get("remittance_information_unstructured")
            or "Unknown"
        )
    else:
        merchant = (
            (tx.get("debtor") or {}).get("name")
            or tx.get("debtor_name")
            or (tx.get("remittance_information") or [None])[0]
            or tx.get("remittance_information_unstructured")
            or "Unknown"
        )

    reference = tx.get("remittance_information_unstructured") or tx.get("end_to_end_id") or ""
    bank_category = (tx.get("bank_transaction_code") or {}).get("code") or tx.get("proprietary_bank_transaction_code") or ""

    # Smart categorisation: use learned rules from user's Notion edits,
    # fall back to bank code, then "Uncategorised"
    if category_rules and merchant in category_rules:
        category = category_rules[merchant]
    else:
        category = bank_category or "Uncategorised"

    date      = tx.get("booking_date") or tx.get("value_date") or tx.get("transaction_date") or ""
    status    = "Cleared" if _is_booked_status(tx.get("status")) else "Pending"

    return {
        "tx_id":     _get_tx_id(tx),
        "date":      date,
        "amount":    amount,
        "currency":  currency,
        "merchant":  merchant,
        "category":  category,
        "reference": reference,
        "direction": direction,
        "status":    status,
    }


def _normalise_simplefin(tx: dict, account: dict, category_rules: dict = None) -> dict:
    """Normalise a SimpleFIN transaction into Klartion's internal format.
    Mirrors _normalise() but reads SimpleFIN's signed-string amounts,
    epoch-second posted timestamps, and single description field."""
    amount_str = tx.get("amount", "0")
    try:
        amount_dec = Decimal(amount_str)
    except (InvalidOperation, TypeError, ValueError):
        # Some institutions emit malformed amounts with thousand separators
        # like "1,234.56" — strip and retry once before giving up.
        try:
            amount_dec = Decimal(str(amount_str).replace(",", ""))
        except (InvalidOperation, TypeError, ValueError, AttributeError):
            amount_dec = Decimal(0)
    direction = "in" if amount_dec >= 0 else "out"
    amount = float(abs(amount_dec))

    # Use the account's currency, falling back to USD if absent (Chime is
    # known to emit empty currency). Custom currencies (URL-style) shouldn't
    # reach here — they're filtered at pick time — but be defensive: if a
    # URL slips through, label as USD rather than write a URL into Notion.
    raw_currency = account.get("currency") or "USD"
    currency = "USD" if raw_currency.startswith("http") else raw_currency

    description = (tx.get("description") or "").strip() or "Unknown"
    merchant = description[:200]

    bank_category = ""
    extra = tx.get("extra")
    if isinstance(extra, dict):
        bank_category = extra.get("category") or ""

    if category_rules and merchant in category_rules:
        category = category_rules[merchant]
    else:
        category = bank_category or "Uncategorised"

    # Per spec, `posted` may be 0 for pending transactions. Fall back to
    # `transacted_at` (the actual occurrence time) when posted is missing
    # or zero, so pending transactions still get a meaningful date.
    posted = tx.get("posted")
    transacted_at = tx.get("transacted_at")
    if isinstance(posted, (int, float)) and posted > 0:
        ts = posted
    elif isinstance(transacted_at, (int, float)) and transacted_at > 0:
        ts = transacted_at
    else:
        ts = None
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d") if ts is not None else ""

    # Pending detection: if the explicit `pending` field is present (true OR
    # false), trust it. Otherwise fall back to "posted == 0 means pending".
    # Some institutions emit pending=false on cleared transactions while
    # also sending posted=0; treating that as pending would mark legitimately
    # cleared transactions as pending in Notion.
    if "pending" in tx:
        is_pending = bool(tx.get("pending"))
    else:
        is_pending = isinstance(posted, (int, float)) and posted == 0
    status = "Pending" if is_pending else "Cleared"

    return {
        "tx_id":     tx.get("id", ""),
        "date":      date,
        "amount":    amount,
        "currency":  currency,
        "merchant":  merchant,
        "category":  category,
        "reference": "",
        "direction": direction,
        "status":    status,
    }


def _extract_balance(balances: list) -> tuple:
    """
    Pick the most useful balance from the list returned by Enable Banking.
    Prefers closing booked (CLBD), then expected (XPCD), then any available.
    Returns (amount, currency) or (None, None).
    """
    if not balances:
        return None, None
    preferred_types = ["CLBD", "closingBooked", "XPCD", "expected", "ITAV", "interimAvailable"]
    for btype in preferred_types:
        for b in balances:
            if b.get("balance_type") == btype:
                amt = b.get("balance_amount", {})
                return float(amt.get("amount", 0)), amt.get("currency", "EUR")
    # Fallback: first balance
    amt = balances[0].get("balance_amount", {})
    return float(amt.get("amount", 0)), amt.get("currency", "EUR")


def _check_for_update():
    """Check Docker Hub for a newer image and store result in DB."""
    import subprocess, os, requests as _req
    if not os.path.exists("/var/run/docker.sock"):
        return
    repo = "daalves/klartion"
    tag = "latest"
    token_resp = _req.get(f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull", timeout=5)
    token = token_resp.json().get("token", "")
    manifest_resp = _req.head(
        f"https://registry-1.docker.io/v2/{repo}/manifests/{tag}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
        timeout=5
    )
    remote_digest = manifest_resp.headers.get("Docker-Content-Digest", "")
    local_digest = subprocess.run(
        ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", f"{repo}:{tag}"],
        capture_output=True, text=True, timeout=10
    ).stdout.strip()
    local_sha = local_digest.split("@")[-1] if "@" in local_digest else ""
    update_available = remote_digest != local_sha and remote_digest != ""
    db.set_setting("update_available", "1" if update_available else "0")
    if update_available:
        logger.info("Update available for %s", repo)

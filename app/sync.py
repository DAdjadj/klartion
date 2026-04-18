import logging
from datetime import datetime, timedelta, timezone
from . import config, db, enablebanking, notion, email_notify, licence, crypto

logger = logging.getLogger(__name__)

def _is_booked_status(status: str) -> bool:
    return (status or "").upper() in {"BOOK", "BOOKED"}

def _scoped_tx_id(account_uid: str, tx: dict) -> str:
    return f"{account_uid}:{_get_tx_id(tx)}"

def run():
    """
    Main sync orchestrator. Called by the scheduler daily.
    Returns (success: bool, tx_count: int, message: str)
    """
    logger.info("Starting sync run...")

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

    for tokens in all_tokens:
        # Handle balance-only providers separately
        if tokens.get("sync_mode") == "balance":
            success, count, label = _sync_balance_token(tokens)
            if success:
                total_written += count
                balance_lines.append(label)
            else:
                errors.append(label)
            continue

        bank_label = f"{tokens.get('bank_name', 'Unknown')} ({tokens.get('bank_country', '')})"
        token_id = tokens["id"]
        session_id = tokens["session_id"]
        account_uid = tokens.get("access_token")

        # 3. Check token expiry warning (14 days)
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
            continue

        # 4. Determine date range
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

        # 5. Fetch transactions
        try:
            all_transactions = enablebanking.get_transactions(session_id, account_uid, date_from, date_to)
        except Exception as e:
            import re
            err = re.sub(r" for url: https?://\S+", "", str(e))
            errors.append(f"{bank_label}: {err}")
            logger.error("Failed to fetch transactions for %s: %s", bank_label, err)
            continue

        logger.info("Fetched %d transactions from %s", len(all_transactions), bank_label)

        # 6. Deduplicate
        tx_prefix = f"{account_uid}:"
        known_ids = db.get_known_tx_ids(tx_id_prefix=tx_prefix)
        new_transactions = [t for t in all_transactions if _scoped_tx_id(account_uid, t) not in known_ids]
        logger.info("%d new transactions after deduplication", len(new_transactions))

        # 7. Reconcile pending
        _reconcile_pending(account_uid, all_transactions)

        # 8. Fetch account balance (before writing so we can attach to transactions)
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

        # 9. Write to Notion
        written = 0
        for tx in new_transactions:
            try:
                normalised = _normalise(tx, category_rules=category_rules)
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
        total_written += written
        logger.info("Synced %d transactions from %s", written, bank_label)

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

    date      = tx.get("booking_date") or tx.get("value_date") or ""
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

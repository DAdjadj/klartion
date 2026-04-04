import logging
from datetime import datetime, timedelta, timezone
from . import config, db, enablebanking, notion, email_notify, licence

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

    total_written = 0
    errors = []
    balance_lines = []

    for tokens in all_tokens:
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

        # 8. Write to Notion
        written = 0
        for tx in new_transactions:
            try:
                normalised = _normalise(tx)
                normalised["bank_name"] = tokens.get("bank_name", "")
                notion_page_id = notion.write_transaction(normalised)
                db.upsert_transaction(
                    tx_id=_scoped_tx_id(account_uid, tx),
                    notion_page_id=notion_page_id,
                    status=normalised["status"].lower(),
                )
                written += 1
            except Exception as e:
                logger.error("Failed to write transaction %s: %s", _get_tx_id(tx), e)

        # 9. Fetch account balance
        try:
            balances = enablebanking.get_balances(session_id, account_uid)
            balance_amount, balance_currency = _extract_balance(balances)
            if balance_amount is not None:
                db.update_token_fields(token_id, last_balance=str(balance_amount), last_balance_currency=balance_currency)
                balance_lines.append(f"{tokens.get('bank_name', 'Unknown')}: {balance_amount:,.2f} {balance_currency}")
                logger.info("Balance for %s: %s %s", bank_label, balance_amount, balance_currency)
        except Exception as e:
            logger.warning("Could not fetch balance for %s: %s", bank_label, e)

        db.update_token_fields(token_id, last_sync_at=datetime.now(timezone.utc).isoformat())
        total_written += written
        logger.info("Synced %d transactions from %s", written, bank_label)

    # 10. Log and notify
    if errors:
        msg = f"{total_written} transactions written. Errors: {'; '.join(errors)}"
        db.log_sync("partial" if total_written > 0 else "failure", tx_count=total_written, message=msg)
        email_notify.send_failure(msg)
    else:
        db.log_sync("success", tx_count=total_written)
        email_notify.send_success(total_written, balance_lines=balance_lines)

    logger.info("Sync complete. %d transactions written.", total_written)

    # 10. Check for updates silently
    try:
        _check_for_update()
    except Exception:
        pass

    return len(errors) == 0, total_written, "OK"


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


def _normalise(tx: dict) -> dict:
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
    category  = (tx.get("bank_transaction_code") or {}).get("code") or tx.get("proprietary_bank_transaction_code") or "Uncategorised"
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

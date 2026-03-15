import logging
from datetime import datetime, timedelta, timezone
from . import config, db, enablebanking, notion, email_notify, licence

logger = logging.getLogger(__name__)

def run():
    """
    Main sync orchestrator. Called by the scheduler daily.
    Returns (success: bool, tx_count: int, message: str)
    """
    logger.info("Starting sync run...")

    # 1. Licence check
    result = {"valid": True, "error": None}  # DEV: skip licence
    if not result["valid"]:
        msg = f"Licence invalid: {result['error']}"
        logger.error(msg)
        db.log_sync("failure", message=msg)
        email_notify.send_failure(msg)
        return False, 0, msg

    # 2. Load tokens
    tokens = db.get_tokens()
    if not tokens:
        msg = "No bank connection found. Please connect your bank at http://localhost:3000"
        logger.error(msg)
        db.log_sync("failure", message=msg)
        email_notify.send_failure(msg)
        return False, 0, msg

    session_id = tokens["session_id"]

    # 3. Check token expiry warning (14 days)
    days_left = enablebanking.check_token_expiry()
    if days_left is not None and days_left <= 14:
        email_notify.send_token_expiry_warning(tokens.get("bank_name", "your bank"), days_left)

    # 4. Determine date range (last sync - 2 days buffer, or 30 days if first run)
    last_sync = db.get_last_sync()
    if last_sync:
        date_from = (datetime.fromisoformat(last_sync) - timedelta(days=2)).strftime("%Y-%m-%d")
    else:
        date_from = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    date_to = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    logger.info("Fetching transactions from %s to %s", date_from, date_to)

    # 5. Get account UID from stored tokens (set during OAuth)
    account_uid = tokens.get("access_token")
    if not account_uid:
        msg = "No account UID found. Please reconnect your bank."
        logger.error(msg)
        db.log_sync("failure", message=msg)
        email_notify.send_failure(msg)
        return False, 0, msg

    # 6. Fetch transactions directly by account UID
    try:
        all_transactions = enablebanking.get_transactions(session_id, account_uid, date_from, date_to)
    except Exception as e:
        msg = f"Failed to fetch transactions from Enable Banking: {e}"
        logger.error(msg)
        db.log_sync("failure", message=msg)
        email_notify.send_failure(msg)
        return False, 0, msg

    logger.info("Fetched %d transactions from Enable Banking", len(all_transactions))

    # 7. Deduplicate against known transaction IDs
    known_ids = db.get_known_tx_ids()
    new_transactions = [t for t in all_transactions if _get_tx_id(t) not in known_ids]
    logger.info("%d new transactions after deduplication", len(new_transactions))

    # 8. Handle pending transaction updates
    _reconcile_pending(all_transactions)

    # 9. Write new transactions to Notion
    written = 0
    for tx in new_transactions:
        try:
            normalised = _normalise(tx)
            notion_page_id = notion.write_transaction(normalised)
            db.upsert_transaction(
                tx_id=normalised["tx_id"],
                notion_page_id=notion_page_id,
                status=normalised["status"],
            )
            written += 1
        except Exception as e:
            logger.error("Failed to write transaction %s: %s", _get_tx_id(tx), e)

    # 10. Log and notify
    db.log_sync("success", tx_count=written)
    email_notify.send_success(written)
    logger.info("Sync complete. %d transactions written.", written)
    return True, written, "OK"


def _reconcile_pending(all_transactions: list):
    """
    Check previously imported pending transactions against the new batch.
    Update Notion rows that have been cleared or cancelled.
    """
    pending = db.get_pending_transactions()
    if not pending:
        return

    booked_ids  = {_get_tx_id(t) for t in all_transactions if t.get("status") == "booked"}
    fetched_ids = {_get_tx_id(t) for t in all_transactions}

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
    status    = "Cleared" if tx.get("status") in ("booked", "BOOK") else "Pending"

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

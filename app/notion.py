import logging
from notion_client import Client
from . import config

logger = logging.getLogger(__name__)

def _client():
    return Client(auth=config.NOTION_API_KEY)

def write_transaction(tx: dict) -> str:
    """
    Write a single transaction to the Notion database.
    Returns the Notion page ID.

    Expected tx keys:
        tx_id, date, amount, currency, merchant, category,
        reference, direction, status
    """
    notion = _client()

    amount = tx.get("amount", 0)
    if tx.get("direction") == "out":
        amount = -abs(amount)
    else:
        amount = abs(amount)

    properties = {
        "Date": {
            "date": {"start": tx.get("date", "")}
        },
        "Amount": {
            "number": amount
        },
        "Currency": {
            "rich_text": [{"text": {"content": tx.get("currency", "")}}]
        },
        "Merchant": {
            "title": [{"text": {"content": tx.get("merchant", "Unknown")}}]
        },
        "Category": {
            "select": {"name": tx.get("category", "Uncategorised")}
        },
        "Reference": {
            "rich_text": [{"text": {"content": tx.get("reference", "")}}]
        },
        "Direction": {
            "select": {"name": tx.get("direction", "out")}
        },
        "Status": {
            "select": {"name": tx.get("status", "Cleared")}
        },
        "Transaction ID": {
            "rich_text": [{"text": {"content": tx.get("tx_id", "")}}]
        },
    }

    page = notion.pages.create(
        parent={"database_id": config.NOTION_DATABASE_ID},
        properties=properties,
    )
    return page["id"]

def update_transaction_status(notion_page_id: str, status: str, amount: float = None):
    """
    Update an existing Notion page's status (e.g. Pending -> Cleared or Cancelled).
    Optionally correct the amount if it changed on settlement.
    """
    notion = _client()
    properties = {
        "Status": {"select": {"name": status}}
    }
    if amount is not None:
        properties["Amount"] = {"number": amount}
    notion.pages.update(page_id=notion_page_id, properties=properties)

def verify_database() -> bool:
    """
    Check the Notion database exists and is accessible.
    Returns True if accessible.
    """
    try:
        notion = _client()
        notion.databases.retrieve(database_id=config.NOTION_DATABASE_ID)
        return True
    except Exception as e:
        logger.error("Notion database check failed: %s", e)
        return False

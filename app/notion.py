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
        "Bank": {
            "rich_text": [{"text": {"content": tx.get("bank_name", "")}}]
        },
    }

    if tx.get("balance") is not None:
        properties["Balance"] = {"number": tx["balance"]}

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

def ensure_balance_property():
    """Add a Balance number property to the database if it doesn't exist."""
    try:
        notion = _client()
        db_info = notion.databases.retrieve(database_id=config.NOTION_DATABASE_ID)
        if "Balance" not in db_info.get("properties", {}):
            notion.databases.update(
                database_id=config.NOTION_DATABASE_ID,
                properties={"Balance": {"number": {"format": "number"}}}
            )
            logger.info("Added Balance property to Notion database")
    except Exception as e:
        logger.warning("Could not ensure Balance property: %s", e)


def fetch_category_rules() -> dict:
    """
    Query the Notion database for transactions with non-default categories.
    Returns a dict of {merchant_name: category}.
    Most recent transaction wins if a merchant has multiple categories.
    """
    notion = _client()
    rules = {}
    has_more = True
    start_cursor = None
    while has_more:
        kwargs = {
            "database_id": config.NOTION_DATABASE_ID,
            "filter": {
                "property": "Category",
                "select": {"does_not_equal": "Uncategorised"},
            },
            "sorts": [{"property": "Date", "direction": "descending"}],
            "page_size": 100,
        }
        if start_cursor:
            kwargs["start_cursor"] = start_cursor
        try:
            resp = notion.databases.query(**kwargs)
        except Exception as e:
            logger.warning("Could not fetch category rules from Notion: %s", e)
            break
        for page in resp.get("results", []):
            props = page.get("properties", {})
            merchant_prop = props.get("Merchant", {}).get("title", [])
            category_prop = props.get("Category", {}).get("select")
            if not merchant_prop or not category_prop:
                continue
            merchant = merchant_prop[0].get("text", {}).get("content", "").strip()
            category = category_prop.get("name", "").strip()
            if merchant and category and merchant not in rules:
                rules[merchant] = category
        has_more = resp.get("has_more", False)
        start_cursor = resp.get("next_cursor")
    logger.info("Loaded %d category rules from Notion", len(rules))
    return rules


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

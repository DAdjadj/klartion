import logging
import threading
import time
from notion_client import Client
from notion_client.errors import HTTPResponseError
from . import config

logger = logging.getLogger(__name__)

# Notion allows about 3 requests a second per connection and answers 429 with a
# Retry-After header above that. One shared client, calls spaced out, and a retry
# on 429/503 keep a large first import from losing rows. Only 429 and 503 are
# retried: both mean Notion did not handle the request, so a retried page
# create can't make a duplicate row.
_MIN_GAP_SECONDS = 0.35
_RETRY_STATUSES = (429, 503)
_MAX_ATTEMPTS = 5
_lock = threading.Lock()
_shared = {"token": None, "client": None, "last_call": 0.0}


def _client():
    from . import notion_auth
    notion_auth.refresh()
    token = config.NOTION_API_KEY
    with _lock:
        if _shared["client"] is None or _shared["token"] != token:
            _shared["client"] = Client(auth=token)
            _shared["token"] = token
        return _shared["client"]


def _retry_delay(err: HTTPResponseError, attempt: int) -> float:
    try:
        return min(float(err.headers.get("retry-after")), 60.0)
    except (TypeError, ValueError):
        return float(2 ** attempt)


def _call(fn, *args, **kwargs):
    """Run one Notion request, paced and retried when Notion is busy."""
    for attempt in range(_MAX_ATTEMPTS):
        with _lock:
            wait = _shared["last_call"] + _MIN_GAP_SECONDS - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _shared["last_call"] = time.monotonic()
        try:
            return fn(*args, **kwargs)
        except HTTPResponseError as e:
            if e.status not in _RETRY_STATUSES or attempt == _MAX_ATTEMPTS - 1:
                raise
            delay = _retry_delay(e, attempt)
            logger.info("Notion is busy (%s), retrying in %.0f s", e.status, delay)
            time.sleep(delay)

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

    page = _call(notion.pages.create,
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
    _call(notion.pages.update, page_id=notion_page_id, properties=properties)

# Columns Klartion writes to. The title column is handled separately because
# every Notion database already has one (usually called "Name").
REQUIRED_PROPERTIES = {
    "Date": {"date": {}},
    "Amount": {"number": {"format": "number"}},
    "Currency": {"rich_text": {}},
    "Category": {"select": {}},
    "Reference": {"rich_text": {}},
    "Direction": {"select": {}},
    "Status": {"select": {}},
    "Transaction ID": {"rich_text": {}},
    "Bank": {"rich_text": {}},
    "Balance": {"number": {"format": "number"}},
}

def ensure_schema() -> str:
    """
    Make the database match what Klartion writes: rename the title column to
    Merchant and add any missing columns, so an empty database works without
    a template. Existing columns of the wrong type are reported, never changed.
    Returns an error message, or "" when the database is ready.
    """
    notion = _client()
    props = _call(notion.databases.retrieve, database_id=config.NOTION_DATABASE_ID).get("properties", {})
    updates = {}

    title_name = next((n for n, p in props.items() if p.get("type") == "title"), None)
    if "Merchant" not in props and title_name:
        updates[props[title_name]["id"]] = {"name": "Merchant"}
    elif "Merchant" in props and props["Merchant"].get("type") != "title":
        return 'The "Merchant" column must be the title column of the database.'

    wrong = []
    for name, schema in REQUIRED_PROPERTIES.items():
        if name not in props:
            updates[name] = schema
        elif props[name].get("type") != next(iter(schema)):
            wrong.append(f'"{name}" should be {next(iter(schema)).replace("rich_text", "text")}')
    if wrong:
        return "Some columns have the wrong type: " + ", ".join(wrong) + ". Rename or delete them and try again."

    if updates:
        _call(notion.databases.update, database_id=config.NOTION_DATABASE_ID, properties=updates)
        logger.info("Added or renamed Notion columns: %s", ", ".join(updates))
    return ""


DATABASE_TITLE = "Klartion Transactions"

def _has_klartion_columns(db_info: dict) -> bool:
    return "Transaction ID" in db_info.get("properties", {})

def find_or_create_database(page_id: str = None) -> str:
    """
    Pick the database to write to after Sign in with Notion, creating one if
    needed. page_id is the copy of Klartion's template, when the user chose it.
    Returns the database ID.
    """
    notion = _client()

    if page_id:
        # The template may itself be a database, or a page holding one.
        try:
            notion.databases.retrieve(database_id=page_id)
            return page_id
        except Exception:
            pass
        children = notion.blocks.children.list(block_id=page_id).get("results", [])
        for block in children:
            if block.get("type") == "child_database":
                return block["id"]
        return _create_database(notion, page_id)

    # The user picked their own pages instead of the template.
    shared = notion.search(filter={"property": "object", "value": "database"}).get("results", [])
    if shared:
        return next((d for d in shared if _has_klartion_columns(d)), shared[0])["id"]
    pages = notion.search(filter={"property": "object", "value": "page"}).get("results", [])
    if not pages:
        raise ValueError("Notion did not share any page with Klartion. Click Connect Notion again and keep "
                         "\"Use a template provided by the developer\" selected.")
    top = next((p for p in pages if p.get("parent", {}).get("type") == "workspace"), pages[0])
    return _create_database(notion, top["id"])

def _create_database(notion, page_id: str) -> str:
    properties = {"Merchant": {"title": {}}, **REQUIRED_PROPERTIES}
    db_info = notion.databases.create(
        parent={"type": "page_id", "page_id": page_id},
        title=[{"type": "text", "text": {"content": DATABASE_TITLE}}],
        properties=properties,
    )
    logger.info("Created Notion database %s", db_info["id"])
    return db_info["id"]


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
            resp = _call(notion.databases.query, **kwargs)
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
        _call(notion.databases.retrieve, database_id=config.NOTION_DATABASE_ID)
        return True
    except Exception as e:
        logger.error("Notion database check failed: %s", e)
        return False

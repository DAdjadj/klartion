"""SimpleFIN Bridge integration. Peer to enablebanking.py.

User flow:
  1. User signs up at bridge.simplefin.org and links their bank.
  2. SimpleFIN issues a one-shot Setup Token (base64-encoded claim URL).
  3. User pastes the Setup Token into Klartion.
  4. claim_setup_token() exchanges it for a long-lived Access URL.
  5. The Access URL embeds basic-auth credentials and is used daily to
     fetch accounts and transactions from /accounts.

Reference: https://www.simplefin.org/protocol.html (v2.0.0-draft)
"""

import base64
import logging
import re
from urllib.parse import unquote, urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30

# Defensive: chunk wide date ranges into 90-day windows. The protocol spec
# doesn't document a hard cap, but bounded windows let a wide initial backfill
# recover gracefully from transient errors per chunk.
MAX_RANGE_SECONDS = 90 * 86400


# Match `https://user:pass@` (or http://) so we can scrub embedded credentials
# from error/log text before it leaves this module. Setup-token URLs and
# access URLs both embed credentials inline, so any exception raised by
# `requests` (HTTPError, ConnectionError, Timeout) leaks them via str(e)
# without this scrub.
_CRED_URL_RE = re.compile(r"https?://[^/\s@]*?:[^/\s@]*?@")


def strip_credentials(text) -> str:
    """Replace `https?://user:pass@` patterns with `https://[redacted]@`."""
    if not isinstance(text, str):
        text = str(text)
    return _CRED_URL_RE.sub("https://[redacted]@", text)


class SimpleFinError(Exception):
    """Base exception for SimpleFIN integration failures."""


class SimpleFinSubscriptionLapsed(SimpleFinError):
    """402 Payment Required: bridge.simplefin.org subscription is not active."""


class SimpleFinTokenAlreadyClaimed(SimpleFinError):
    """403 on /claim: token reused or invalid. Per spec, treat as possibly compromised."""


class SimpleFinAccessRevoked(SimpleFinError):
    """403 on /accounts: access URL revoked. User must regenerate at bridge.simplefin.org."""


def claim_setup_token(setup_token: str) -> str:
    """Exchange a one-shot setup token for a long-lived access URL.

    Setup tokens are base64-encoded claim URLs. POSTing to the decoded URL
    returns the access URL as the response body. Tokens can only be claimed
    once; subsequent attempts return 403.
    """
    cleaned = (setup_token or "").strip()
    if not cleaned:
        raise SimpleFinError("Setup token is empty.")

    # Pre-flight format checks. Setup-token / access-URL confusion is the
    # #1 onboarding ticket across SimpleFIN-consuming apps — give clear
    # messages instead of "not valid base64" or "already used".
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        if "@" in cleaned and ":" in cleaned.split("@", 1)[0].split("//", 1)[-1]:
            raise SimpleFinError(
                "That looks like a SimpleFIN access URL, not a setup token. "
                "If you already claimed your token, the access URL has been "
                "saved — generate a new setup token at bridge.simplefin.org "
                "if you need to reconnect."
            )
        raise SimpleFinError(
            "That looks like a URL, not a setup token. SimpleFIN setup "
            "tokens are short base64-encoded strings. Generate one at "
            "bridge.simplefin.org and paste the code itself, not the URL."
        )

    try:
        claim_url = base64.b64decode(cleaned, validate=False).decode("utf-8").strip()
    except Exception as exc:
        raise SimpleFinError(f"Setup token is not valid base64: {exc}")
    if not claim_url.startswith("https://"):
        raise SimpleFinError("Setup token did not decode to an https:// URL.")

    # Send `data=b""` explicitly so `requests` sets a Content-Length: 0
    # body on this empty POST — some servers require it.
    try:
        resp = requests.post(
            claim_url,
            data=b"",
            headers={"Content-Length": "0"},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        raise SimpleFinError(strip_credentials(str(exc))) from None
    if resp.status_code == 403:
        raise SimpleFinTokenAlreadyClaimed(
            "This setup token has already been used. "
            "Generate a new one at bridge.simplefin.org."
        )
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise SimpleFinError(strip_credentials(str(exc))) from None

    access_url = resp.text.strip()
    if not access_url.startswith("https://"):
        raise SimpleFinError("Claim response was not a valid https:// access URL.")
    return access_url


def parse_access_url(access_url: str) -> tuple[str, str, str]:
    """Split an access URL of the form https://USERNAME:PASSWORD@host/path
    into (base_url_without_credentials, username, password)."""
    parsed = urlparse(access_url)
    if not parsed.username or not parsed.password:
        raise SimpleFinError("Access URL is missing embedded credentials.")
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    # Strip any trailing slash on the path so we don't produce a doubled slash
    # when appending '/accounts' to the base URL.
    path = (parsed.path or "").rstrip("/")
    base = f"{parsed.scheme}://{netloc}{path}"
    return base, unquote(parsed.username), unquote(parsed.password)


def _accounts_call(access_url: str, params: dict) -> dict:
    """Single GET /accounts call. Always sends version=2.

    Wraps every requests-level exception so the credentials embedded in the
    access URL never leak via str(e) into logs or user-visible error pages.
    """
    base, user, password = parse_access_url(access_url)
    final_params = {"version": "2"}
    final_params.update(params)
    try:
        resp = requests.get(
            f"{base}/accounts",
            params=final_params,
            auth=(user, password),
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.exceptions.RequestException as exc:
        raise SimpleFinError(strip_credentials(str(exc))) from None
    if resp.status_code == 402:
        raise SimpleFinSubscriptionLapsed(
            "Your SimpleFIN Bridge subscription is not active. "
            "Renew at bridge.simplefin.org to resume syncing."
        )
    if resp.status_code == 403:
        raise SimpleFinAccessRevoked(
            "SimpleFIN access has been revoked. "
            "Regenerate a setup token at bridge.simplefin.org."
        )
    # Treat 5xx as service unavailable — SimpleFIN's bridge has had
    # multi-day outages and returns HTML maintenance pages, which would
    # otherwise surface as "not valid JSON".
    if 500 <= resp.status_code < 600:
        raise SimpleFinError(
            "SimpleFIN is currently unavailable. Try again in a few minutes."
        )
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise SimpleFinError(strip_credentials(str(exc))) from None
    try:
        data = resp.json()
    except ValueError as exc:
        raise SimpleFinError(f"SimpleFIN response was not valid JSON: {exc}") from None

    # Some SimpleFIN deployments return 200 + {"errors": ["Payment required."]}
    # instead of HTTP 402 when a subscription lapses. Detect that here so
    # the caller doesn't silently see "no usable accounts found".
    legacy_errors = data.get("errors") or []
    if any(
        isinstance(e, str) and "payment required" in e.lower()
        for e in legacy_errors
    ):
        raise SimpleFinSubscriptionLapsed(
            "Your SimpleFIN Bridge subscription is not active. "
            "Renew at bridge.simplefin.org to resume syncing."
        )
    return data


def list_accounts(
    access_url: str,
    include_pending: bool = True,
    balances_only: bool = False,
) -> dict:
    """Fetch the full Account Set with no date range. Used at claim time to
    enumerate accounts the user can pick. Returns the raw parsed JSON:
      {"errlist": [...], "connections": [...], "accounts": [...]}

    With balances_only=True, SimpleFIN omits transactions from the response —
    saves bandwidth and doesn't pre-burn the user's first daily fetch on
    transaction data the picker page won't display anyway.
    """
    params: dict = {}
    if balances_only:
        params["balances-only"] = "1"
    elif include_pending:
        params["pending"] = "1"
    return _accounts_call(access_url, params)


def get_transactions(
    access_url: str,
    account_id: str,
    date_from_epoch: int,
    date_to_epoch: int,
    include_pending: bool = True,
) -> list:
    """Fetch transactions for one account in [date_from_epoch, date_to_epoch],
    chunking transparently into 90-day windows. Returns a flat list of raw
    SimpleFIN transaction dicts, deduplicated by id."""
    if date_to_epoch < date_from_epoch:
        raise SimpleFinError("end date is before start date")

    transactions: list = []
    seen_ids: set[str] = set()

    cursor = date_from_epoch
    while cursor < date_to_epoch:
        chunk_end = min(cursor + MAX_RANGE_SECONDS, date_to_epoch)
        params = {
            "account": account_id,
            "start-date": str(cursor),
            "end-date": str(chunk_end),
        }
        if include_pending:
            params["pending"] = "1"
        data = _accounts_call(access_url, params)
        for acct in data.get("accounts", []):
            if acct.get("id") != account_id:
                continue
            for tx in acct.get("transactions", []):
                tid = tx.get("id")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    transactions.append(tx)
        cursor = chunk_end

    return transactions

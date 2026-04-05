import logging
import uuid
from decimal import Decimal, ROUND_HALF_UP
import requests
from .base import BalanceProvider

log = logging.getLogger(__name__)

ETORO_API = "https://public-api.etoro.com/api/v1"


def _usd_to_eur() -> Decimal:
    """Fetch current USD to EUR rate from Frankfurter (ECB data)."""
    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest?from=USD&to=EUR",
            timeout=10,
        )
        resp.raise_for_status()
        rate = Decimal(str(resp.json()["rates"]["EUR"]))
        log.info("USD/EUR rate: %s", rate)
        return rate
    except Exception as e:
        log.warning("Could not fetch USD/EUR rate: %s", e)
        return Decimal("0.90")


class EtoroProvider(BalanceProvider):
    name = "etoro"
    display_name = "eToro"
    credential_fields = [
        {"key": "api_key", "label": "Public API Key", "type": "password",
         "help": "Your eToro account must be verified first. Then go to Settings \u2192 Trading \u2192 API Key Management and copy your Public API Key.",
         "help_url": "https://www.etoro.com/settings/trade"},
        {"key": "user_key", "label": "User Key", "type": "password",
         "help": "In the same API Key Management page, generate a User Key with 'Read' permission for your Real account. Requires SMS verification."},
    ]

    def _headers(self, credentials: dict) -> dict:
        return {
            "x-api-key": credentials["api_key"],
            "x-user-key": credentials["user_key"],
            "x-request-id": str(uuid.uuid4()),
        }

    def validate_credentials(self, credentials: dict) -> bool:
        if not credentials.get("api_key") or not credentials.get("user_key"):
            return False
        try:
            resp = requests.get(
                f"{ETORO_API}/trading/info/real/pnl",
                headers=self._headers(credentials),
                timeout=15,
            )
            return resp.status_code == 200
        except Exception as e:
            log.warning("eToro credential validation failed: %s", e)
            return False

    def get_balance(self, credentials: dict) -> Decimal:
        resp = requests.get(
            f"{ETORO_API}/trading/info/real/pnl",
            headers=self._headers(credentials),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        portfolio = data.get("clientPortfolio", {})

        # eToro API returns all amounts in USD.
        # Total Value (USD) = Cash + Invested + Unrealised P&L
        credit = Decimal(str(portfolio.get("credit", 0)))
        unrealized_pnl = Decimal(str(portfolio.get("unrealizedPnL", 0)))
        positions = portfolio.get("positions", [])
        invested = sum(
            Decimal(str(p.get("initialAmountInDollars", 0))) for p in positions
        )
        total_usd = credit + invested + unrealized_pnl

        # Convert USD to EUR (eToro displays EUR for EU accounts)
        rate = _usd_to_eur()
        total_eur = (total_usd * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        log.info("eToro: %s USD * %s = %s EUR", total_usd, rate, total_eur)
        return total_eur

    def get_currency(self, credentials: dict) -> str:
        return "EUR"

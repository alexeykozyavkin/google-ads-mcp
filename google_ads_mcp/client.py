"""Minimal Google Ads REST client and business-safe tool functions."""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from functools import lru_cache
from typing import Any

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account

_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
_DEFAULT_API_VERSION = "v25"
_CUSTOMER_ID_RE = re.compile(r"^\d{10}$")
_API_VERSION_RE = re.compile(r"^v\d+$")
_CONVERSION_ACTION_RE = re.compile(
    r"^customers/(?P<customer>\d{10})/conversionActions/(?P<action>\d+)$"
)
_CLICK_ID_FIELDS = {
    "GCLID": "gclid",
    "GBRAID": "gbraid",
    "WBRAID": "wbraid",
}


class GoogleAdsError(RuntimeError):
    """A sanitized Google Ads API failure."""


def _normalize_customer_id(value: str, *, field: str = "customer_id") -> str:
    normalized = value.replace("-", "").replace(" ", "").strip()
    if not _CUSTOMER_ID_RE.fullmatch(normalized):
        raise ValueError(f"{field} must contain exactly 10 digits.")
    return normalized


def _validate_date(value: str, *, field: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO date in YYYY-MM-DD format.") from exc


def _validate_date_range(start_date: str, end_date: str) -> tuple[str, str]:
    start = _validate_date(start_date, field="start_date")
    end = _validate_date(end_date, field="end_date")
    if start > end:
        raise ValueError("start_date must be on or before end_date.")
    return start, end


def _normalize_datetime(value: str, *, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field} must be an ISO date-time with an explicit UTC offset."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset.")
    rendered = parsed.strftime("%Y-%m-%d %H:%M:%S%z")
    return f"{rendered[:-2]}:{rendered[-2:]}"


def _validate_limit(value: int, *, maximum: int = 1000) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("row_limit must be an integer.")
    if not 1 <= value <= maximum:
        raise ValueError(f"row_limit must be between 1 and {maximum}.")
    return value


def _gaql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class GoogleAdsRestClient:
    """Small REST wrapper that uses service-account OAuth credentials."""

    def __init__(
        self,
        *,
        developer_token: str,
        credentials: Any,
        login_customer_id: str | None = None,
        api_version: str = _DEFAULT_API_VERSION,
        session: requests.Session | None = None,
    ):
        if not developer_token.strip():
            raise RuntimeError("Set GOOGLE_ADS_DEVELOPER_TOKEN.")
        if not _API_VERSION_RE.fullmatch(api_version):
            raise RuntimeError("GOOGLE_ADS_API_VERSION must look like v25.")
        self.developer_token = developer_token.strip()
        self.credentials = credentials
        self.login_customer_id = (
            _normalize_customer_id(login_customer_id, field="login_customer_id")
            if login_customer_id
            else None
        )
        self.api_version = api_version
        self.session = session or requests.Session()
        self.base_url = f"https://googleads.googleapis.com/{api_version}"

    @classmethod
    def from_environment(cls) -> "GoogleAdsRestClient":
        credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if not credentials_path:
            raise RuntimeError(
                "Set GOOGLE_APPLICATION_CREDENTIALS to the materialized service-account JSON."
            )
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=[_ADS_SCOPE],
        )
        return cls(
            developer_token=os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
            credentials=credentials,
            login_customer_id=os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or None,
            api_version=os.environ.get("GOOGLE_ADS_API_VERSION", _DEFAULT_API_VERSION),
        )

    def _headers(self) -> dict[str, str]:
        if not getattr(self.credentials, "valid", False):
            self.credentials.refresh(GoogleAuthRequest())
        headers = {
            "Authorization": f"Bearer {self.credentials.token}",
            "developer-token": self.developer_token,
            "Content-Type": "application/json",
        }
        if self.login_customer_id:
            headers["login-customer-id"] = self.login_customer_id
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=json_body,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise GoogleAdsError("Google Ads API request failed to connect.") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.ok:
            return payload

        message = f"Google Ads API returned HTTP {response.status_code}."
        request_id = response.headers.get("request-id")
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                message = str(error["message"])
            elif payload.get("message"):
                message = str(payload["message"])
        if request_id:
            message = f"{message} Request ID: {request_id}."
        raise GoogleAdsError(message)

    def search(self, customer_id: str, query: str) -> list[dict[str, Any]]:
        customer = _normalize_customer_id(customer_id)
        payload = self.request(
            "POST",
            f"/customers/{customer}/googleAds:searchStream",
            json_body={"query": query},
        )
        batches = payload if isinstance(payload, list) else [payload]
        rows: list[dict[str, Any]] = []
        for batch in batches:
            if isinstance(batch, dict) and isinstance(batch.get("results"), list):
                rows.extend(batch["results"])
        return rows


@lru_cache(maxsize=1)
def _client() -> GoogleAdsRestClient:
    return GoogleAdsRestClient.from_environment()


def list_accessible_customers() -> dict[str, Any]:
    """List customer resource names directly accessible to the service account."""

    payload = _client().request("GET", "/customers:listAccessibleCustomers")
    resource_names = []
    if isinstance(payload, dict):
        resource_names = payload.get("resourceNames") or []
    return {
        "customers": [
            {
                "resource_name": value,
                "customer_id": str(value).rsplit("/", 1)[-1],
            }
            for value in resource_names
        ],
        "count": len(resource_names),
    }


def list_linked_accounts(
    manager_customer_id: str,
    row_limit: int = 1000,
) -> dict[str, Any]:
    """List a manager account and its directly linked client accounts."""

    manager = _normalize_customer_id(
        manager_customer_id,
        field="manager_customer_id",
    )
    limit = _validate_limit(row_limit)
    query = f"""
        SELECT
          customer_client.client_customer,
          customer_client.id,
          customer_client.descriptive_name,
          customer_client.currency_code,
          customer_client.time_zone,
          customer_client.manager,
          customer_client.level,
          customer_client.status,
          customer_client.test_account,
          customer_client.hidden
        FROM customer_client
        WHERE customer_client.level <= 1
        ORDER BY customer_client.level, customer_client.descriptive_name
        LIMIT {limit}
    """
    rows = _client().search(manager, query)
    return {
        "manager_customer_id": manager,
        "rows": rows,
        "count": len(rows),
        "note": "Level 0 is the manager itself; level 1 contains directly linked accounts.",
    }


def get_customer_details(customer_id: str) -> dict[str, Any]:
    """Return account metadata and conversion tracking configuration."""

    customer = _normalize_customer_id(customer_id)
    query = """
        SELECT
          customer.id,
          customer.descriptive_name,
          customer.currency_code,
          customer.time_zone,
          customer.manager,
          customer.test_account,
          customer.conversion_tracking_setting.google_ads_conversion_customer,
          customer.conversion_tracking_setting.conversion_tracking_status
        FROM customer
        LIMIT 1
    """
    rows = _client().search(customer, query)
    return {"customer_id": customer, "rows": rows, "count": len(rows)}


def get_campaign_performance(
    customer_id: str,
    start_date: str,
    end_date: str,
    row_limit: int = 100,
) -> dict[str, Any]:
    """Return campaign-level traffic, cost, and conversion performance."""

    customer = _normalize_customer_id(customer_id)
    start, end = _validate_date_range(start_date, end_date)
    limit = _validate_limit(row_limit)
    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          campaign.advertising_channel_type,
          campaign.bidding_strategy_type,
          metrics.impressions,
          metrics.clicks,
          metrics.ctr,
          metrics.average_cpc,
          metrics.cost_micros,
          metrics.conversions,
          metrics.conversions_value
        FROM campaign
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND campaign.status != 'REMOVED'
        ORDER BY metrics.cost_micros DESC
        LIMIT {limit}
    """
    rows = _client().search(customer, query)
    return {
        "customer_id": customer,
        "start_date": start,
        "end_date": end,
        "rows": rows,
        "count": len(rows),
    }


def get_search_terms(
    customer_id: str,
    start_date: str,
    end_date: str,
    row_limit: int = 200,
) -> dict[str, Any]:
    """Return paid-search queries with campaign and ad-group performance."""

    customer = _normalize_customer_id(customer_id)
    start, end = _validate_date_range(start_date, end_date)
    limit = _validate_limit(row_limit)
    query = f"""
        SELECT
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          search_term_view.search_term,
          segments.search_term_match_type,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions,
          metrics.conversions_value
        FROM search_term_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND campaign.status != 'REMOVED'
        ORDER BY metrics.clicks DESC
        LIMIT {limit}
    """
    rows = _client().search(customer, query)
    return {
        "customer_id": customer,
        "start_date": start,
        "end_date": end,
        "rows": rows,
        "count": len(rows),
    }


def lookup_gclid(customer_id: str, gclid: str, click_date: str) -> dict[str, Any]:
    """Map a GCLID to its Google Ads campaign/ad group for one click date."""

    customer = _normalize_customer_id(customer_id)
    checked_date = _validate_date(click_date, field="click_date")
    if not isinstance(gclid, str) or not gclid.strip() or len(gclid) > 512:
        raise ValueError("gclid must be a non-empty string of at most 512 characters.")
    escaped = _gaql_string(gclid.strip())
    query = f"""
        SELECT
          customer.id,
          customer.descriptive_name,
          campaign.id,
          campaign.name,
          ad_group.id,
          ad_group.name,
          click_view.gclid,
          click_view.ad_group_ad,
          click_view.keyword_info.text,
          click_view.keyword_info.match_type,
          segments.date,
          segments.device,
          segments.click_type
        FROM click_view
        WHERE segments.date = '{checked_date}'
          AND click_view.gclid = '{escaped}'
        LIMIT 10
    """
    rows = _client().search(customer, query)
    return {
        "customer_id": customer,
        "click_date": checked_date,
        "gclid": gclid.strip(),
        "rows": rows,
        "count": len(rows),
        "note": "click_view requires the exact click date and supports only the last 90 days.",
    }


def list_conversion_actions(
    customer_id: str,
    enabled_only: bool = True,
) -> dict[str, Any]:
    """List conversion actions, including resource names used for uploads."""

    customer = _normalize_customer_id(customer_id)
    status_filter = "WHERE conversion_action.status = 'ENABLED'" if enabled_only else ""
    query = f"""
        SELECT
          conversion_action.id,
          conversion_action.name,
          conversion_action.status,
          conversion_action.type,
          conversion_action.category,
          conversion_action.resource_name,
          conversion_action.primary_for_goal
        FROM conversion_action
        {status_filter}
        ORDER BY conversion_action.name
        LIMIT 1000
    """
    rows = _client().search(customer, query)
    return {"customer_id": customer, "rows": rows, "count": len(rows)}


def upload_offline_conversion(
    customer_id: str,
    conversion_action_resource_name: str,
    click_id: str,
    click_id_type: str,
    conversion_date_time: str,
    conversion_value: float,
    currency_code: str,
    order_id: str,
    validate_only: bool = True,
    confirm_write: bool = False,
    ad_user_data_consent: str | None = None,
) -> dict[str, Any]:
    """Validate or upload one CRM/offline conversion with explicit safeguards."""

    customer = _normalize_customer_id(customer_id)
    match = _CONVERSION_ACTION_RE.fullmatch(conversion_action_resource_name.strip())
    if not match:
        raise ValueError(
            "conversion_action_resource_name must look like "
            "customers/1234567890/conversionActions/123."
        )
    if not isinstance(click_id, str) or not click_id.strip() or len(click_id) > 512:
        raise ValueError(
            "click_id must be a non-empty string of at most 512 characters."
        )
    click_type = click_id_type.upper().strip()
    if click_type not in _CLICK_ID_FIELDS:
        raise ValueError("click_id_type must be GCLID, GBRAID, or WBRAID.")
    if isinstance(conversion_value, bool) or not isinstance(
        conversion_value, (int, float)
    ):
        raise ValueError("conversion_value must be numeric.")
    if conversion_value < 0:
        raise ValueError(
            "conversion_value cannot be negative. Do not upload bad leads as "
            "negative conversions; use a separate qualification action or adjustment."
        )
    currency = currency_code.upper().strip()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency_code must be a three-letter ISO code such as USD.")
    if not isinstance(order_id, str) or not order_id.strip() or len(order_id) > 256:
        raise ValueError("order_id must be a non-empty stable CRM identifier.")
    if not validate_only and not confirm_write:
        raise ValueError(
            "Set confirm_write=true together with validate_only=false to perform "
            "a real conversion upload. Run validate_only=true first."
        )

    conversion: dict[str, Any] = {
        _CLICK_ID_FIELDS[click_type]: click_id.strip(),
        "conversionAction": conversion_action_resource_name.strip(),
        "conversionDateTime": _normalize_datetime(
            conversion_date_time,
            field="conversion_date_time",
        ),
        "conversionValue": float(conversion_value),
        "currencyCode": currency,
        "orderId": order_id.strip(),
        "conversionEnvironment": "WEB",
    }
    if ad_user_data_consent:
        consent = ad_user_data_consent.upper().strip()
        if consent not in {"GRANTED", "DENIED", "UNSPECIFIED"}:
            raise ValueError(
                "ad_user_data_consent must be GRANTED, DENIED, or UNSPECIFIED."
            )
        conversion["consent"] = {"adUserData": consent}

    payload = _client().request(
        "POST",
        f"/customers/{customer}:uploadClickConversions",
        json_body={
            "conversions": [conversion],
            "partialFailure": True,
            "validateOnly": bool(validate_only),
        },
    )
    return {
        "customer_id": customer,
        "mode": "validation_only" if validate_only else "uploaded",
        "order_id": order_id.strip(),
        "response": payload,
    }

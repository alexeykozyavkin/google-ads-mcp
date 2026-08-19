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
_DATA_MANAGER_SCOPE = "https://www.googleapis.com/auth/datamanager"
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


def _error_envelopes(payload: Any) -> list[dict[str, Any]]:
    """Return REST error envelopes from regular and searchStream responses."""

    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _error_code_label(error_code: Any) -> str | None:
    """Render a GoogleAdsFailure errorCode without exposing trigger values."""

    if not isinstance(error_code, dict):
        return None
    for category, value in error_code.items():
        if isinstance(value, str) and value:
            return f"{category}.{value}"
        if isinstance(value, dict):
            nested = _error_code_label(value)
            if nested:
                return f"{category}.{nested}"
    return None


def _google_ads_error_message(
    status_code: int,
    payload: Any,
    header_request_id: str | None,
) -> str:
    """Build a bounded, sanitized diagnostic from a Google Ads REST failure."""

    status: str | None = None
    top_message: str | None = None
    request_id = header_request_id
    failures: list[str] = []

    for envelope in _error_envelopes(payload):
        error = envelope.get("error")
        if not isinstance(error, dict):
            continue
        if not status and isinstance(error.get("status"), str):
            status = error["status"]
        if not top_message and isinstance(error.get("message"), str):
            top_message = error["message"]

        details = error.get("details")
        if not isinstance(details, list):
            continue
        for detail in details:
            if not isinstance(detail, dict):
                continue
            if not request_id and isinstance(detail.get("requestId"), str):
                request_id = detail["requestId"]
            errors = detail.get("errors")
            if not isinstance(errors, list):
                continue
            for failure in errors:
                if not isinstance(failure, dict):
                    continue
                label = _error_code_label(failure.get("errorCode"))
                detail_message = failure.get("message")
                rendered = label or "GoogleAdsFailure"
                if isinstance(detail_message, str) and detail_message:
                    rendered = f"{rendered}: {detail_message}"
                if rendered not in failures:
                    failures.append(rendered)
                if len(failures) >= 4:
                    break

    message = f"Google Ads API returned HTTP {status_code}"
    if status:
        message += f" ({status})"
    message += "."
    if top_message:
        message += f" {top_message}"
    if failures:
        message += " " + " | ".join(failures)
    if request_id:
        message += f" Request ID: {request_id}."
    return message


def _data_manager_error_message(status_code: int, payload: Any) -> str:
    """Build a bounded diagnostic from a Data Manager REST failure."""

    status: str | None = None
    top_message: str | None = None
    details: list[str] = []

    for envelope in _error_envelopes(payload):
        error = envelope.get("error")
        if not isinstance(error, dict):
            continue
        if not status and isinstance(error.get("status"), str):
            status = error["status"]
        if not top_message and isinstance(error.get("message"), str):
            top_message = error["message"]

        raw_details = error.get("details")
        if not isinstance(raw_details, list):
            continue
        for detail in raw_details:
            if not isinstance(detail, dict):
                continue
            reason = detail.get("reason")
            if isinstance(reason, str) and reason and reason not in details:
                details.append(reason)
            violations = detail.get("fieldViolations")
            if not isinstance(violations, list):
                continue
            for violation in violations:
                if not isinstance(violation, dict):
                    continue
                field = violation.get("field")
                description = violation.get("description")
                rendered = "Field violation"
                if isinstance(field, str) and field:
                    rendered += f" at {field}"
                if isinstance(description, str) and description:
                    rendered += f": {description}"
                if rendered not in details:
                    details.append(rendered)
                if len(details) >= 4:
                    break

    message = f"Data Manager API returned HTTP {status_code}"
    if status:
        message += f" ({status})"
    message += "."
    if top_message:
        message += f" {top_message}"
    if details:
        message += " " + " | ".join(details[:4])
    return message


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


def _normalize_rfc3339_datetime(value: str, *, field: str) -> str:
    """Normalize an offset-aware timestamp for the Data Manager API."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field} must be an ISO date-time with an explicit UTC offset."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset.")
    rendered = parsed.isoformat(timespec="seconds")
    return rendered.replace("+00:00", "Z")


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

        request_id = response.headers.get("request-id")
        message = _google_ads_error_message(
            response.status_code,
            payload,
            request_id,
        )
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


class DataManagerRestClient:
    """Small Data Manager REST wrapper using the same service-account key."""

    def __init__(
        self,
        *,
        credentials: Any,
        login_customer_id: str | None = None,
        session: requests.Session | None = None,
    ):
        self.credentials = credentials
        self.login_customer_id = (
            _normalize_customer_id(login_customer_id, field="login_customer_id")
            if login_customer_id
            else None
        )
        self.session = session or requests.Session()
        self.base_url = "https://datamanager.googleapis.com/v1"

    @classmethod
    def from_environment(cls) -> "DataManagerRestClient":
        credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
        if not credentials_path:
            raise RuntimeError(
                "Set GOOGLE_APPLICATION_CREDENTIALS to the materialized service-account JSON."
            )
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=[_DATA_MANAGER_SCOPE],
        )
        return cls(
            credentials=credentials,
            login_customer_id=os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID") or None,
        )

    def _headers(self) -> dict[str, str]:
        if not getattr(self.credentials, "valid", False):
            self.credentials.refresh(GoogleAuthRequest())
        return {
            "Authorization": f"Bearer {self.credentials.token}",
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=self._headers(),
                json=json_body,
                params=params,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise GoogleAdsError("Data Manager API request failed to connect.") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.ok:
            return payload

        raise GoogleAdsError(_data_manager_error_message(response.status_code, payload))


@lru_cache(maxsize=1)
def _client() -> GoogleAdsRestClient:
    return GoogleAdsRestClient.from_environment()


@lru_cache(maxsize=1)
def _data_manager_client() -> DataManagerRestClient:
    return DataManagerRestClient.from_environment()


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
    """Validate or ingest one CRM conversion through the Data Manager API."""

    customer = _normalize_customer_id(customer_id)
    match = _CONVERSION_ACTION_RE.fullmatch(conversion_action_resource_name.strip())
    if not match:
        raise ValueError(
            "conversion_action_resource_name must look like "
            "customers/1234567890/conversionActions/123."
        )
    action_customer = match.group("customer")
    if action_customer != customer:
        raise ValueError(
            "customer_id must match the customer in conversion_action_resource_name."
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

    event: dict[str, Any] = {
        "adIdentifiers": {_CLICK_ID_FIELDS[click_type]: click_id.strip()},
        "eventTimestamp": _normalize_rfc3339_datetime(
            conversion_date_time,
            field="conversion_date_time",
        ),
        "conversionValue": float(conversion_value),
        "currency": currency,
        "transactionId": order_id.strip(),
        "eventSource": "WEB",
    }
    if ad_user_data_consent:
        consent = ad_user_data_consent.upper().strip()
        if consent not in {"GRANTED", "DENIED", "UNSPECIFIED"}:
            raise ValueError(
                "ad_user_data_consent must be GRANTED, DENIED, or UNSPECIFIED."
            )
        consent_values = {
            "GRANTED": "CONSENT_GRANTED",
            "DENIED": "CONSENT_DENIED",
            "UNSPECIFIED": "CONSENT_STATUS_UNSPECIFIED",
        }
        event["consent"] = {"adUserData": consent_values[consent]}

    data_manager = _data_manager_client()
    destination: dict[str, Any] = {
        "operatingAccount": {
            "accountType": "GOOGLE_ADS",
            "accountId": customer,
        },
        "productDestinationId": match.group("action"),
    }
    if data_manager.login_customer_id:
        destination["loginAccount"] = {
            "accountType": "GOOGLE_ADS",
            "accountId": data_manager.login_customer_id,
        }

    payload = data_manager.request(
        "POST",
        "/events:ingest",
        json_body={
            "destinations": [destination],
            "events": [event],
            "validateOnly": bool(validate_only),
        },
    )
    request_id = payload.get("requestId") if isinstance(payload, dict) else None
    return {
        "customer_id": customer,
        "mode": "validation_only" if validate_only else "submitted",
        "order_id": order_id.strip(),
        "request_id": request_id,
        "response": payload,
        "note": (
            "Validation only: no conversion was written. Repeat the identical payload "
            "with validate_only=false and confirm_write=true after validation succeeds."
            if validate_only
            else "Accepted asynchronously. Check request_id with "
            "get_offline_conversion_upload_status before treating the upload as complete."
        ),
    }


def get_offline_conversion_upload_status(request_id: str) -> dict[str, Any]:
    """Retrieve asynchronous Data Manager diagnostics for a real upload."""

    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string.")
    normalized = request_id.strip()
    if len(normalized) > 512:
        raise ValueError("request_id must be at most 512 characters.")
    payload = _data_manager_client().request(
        "GET",
        "/requestStatus:retrieve",
        params={"requestId": normalized},
    )
    return {
        "request_id": normalized,
        "response": payload,
        "note": (
            "Diagnostics are available only for successful non-validation requests "
            "and can remain PROCESSING while Google finishes ingestion."
        ),
    }

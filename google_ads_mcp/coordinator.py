"""MCP tool declarations and dispatch for Google Ads."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from mcp import types as mcp_types
from mcp.server.lowlevel import Server

from google_ads_mcp.client import (
    GoogleAdsError,
    get_campaign_performance,
    get_customer_details,
    get_search_terms,
    list_accessible_customers,
    list_linked_accounts,
    list_conversion_actions,
    lookup_gclid,
    upload_offline_conversion,
)

app = Server(name="Google Ads MCP Server")

_READ_ONLY = mcp_types.ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

_WRITE = mcp_types.ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

_CUSTOMER_ID = {
    "type": "string",
    "pattern": "^[0-9 -]{10,14}$",
    "description": "10-digit Google Ads customer ID; hyphens are accepted.",
}

_DATE = {"type": "string", "format": "date"}

mcp_tools = [
    mcp_types.Tool(
        name="list_accessible_customers",
        title="List accessible Google Ads accounts",
        description=(
            "List Google Ads customer resource names directly accessible to the "
            "configured service account. Call this first when customer IDs are "
            "unknown. Manager child accounts may require follow-up account queries. "
            "This tool is read-only."
        ),
        inputSchema={"type": "object", "additionalProperties": False, "properties": {}},
        annotations=_READ_ONLY,
    ),
    mcp_types.Tool(
        name="list_linked_accounts",
        title="List accounts linked to a Google Ads manager",
        description=(
            "List a manager account and the advertising accounts directly linked "
            "beneath it, including their names, IDs, currencies, time zones, and "
            "statuses. Use this after list_accessible_customers when access is "
            "provided through an MCC. Read-only."
        ),
        inputSchema={
            "type": "object",
            "additionalProperties": False,
            "required": ["manager_customer_id"],
            "properties": {
                "manager_customer_id": _CUSTOMER_ID,
                "row_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 1000,
                },
            },
        },
        annotations=_READ_ONLY,
    ),
    mcp_types.Tool(
        name="get_customer_details",
        title="Get Google Ads account details",
        description=(
            "Return account name, currency, time zone, manager/test flags, and "
            "conversion tracking status for one Google Ads customer. Read-only."
        ),
        inputSchema={
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id"],
            "properties": {"customer_id": _CUSTOMER_ID},
        },
        annotations=_READ_ONLY,
    ),
    mcp_types.Tool(
        name="get_campaign_performance",
        title="Get campaign performance",
        description=(
            "Return campaign-level impressions, clicks, CTR, CPC, cost, conversions, "
            "and conversion value for an inclusive date range. Removed campaigns "
            "are excluded. Read-only."
        ),
        inputSchema={
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id", "start_date", "end_date"],
            "properties": {
                "customer_id": _CUSTOMER_ID,
                "start_date": _DATE,
                "end_date": _DATE,
                "row_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 100,
                },
            },
        },
        annotations=_READ_ONLY,
    ),
    mcp_types.Tool(
        name="get_search_terms",
        title="Get paid-search terms",
        description=(
            "Return actual paid-search queries with campaign, ad group, match type, "
            "traffic, cost, and conversion metrics for an inclusive date range. "
            "Read-only."
        ),
        inputSchema={
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id", "start_date", "end_date"],
            "properties": {
                "customer_id": _CUSTOMER_ID,
                "start_date": _DATE,
                "end_date": _DATE,
                "row_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 200,
                },
            },
        },
        annotations=_READ_ONLY,
    ),
    mcp_types.Tool(
        name="lookup_gclid",
        title="Map a GCLID to its ad click",
        description=(
            "Resolve a CRM GCLID to its Google Ads customer, campaign, ad group, ad, "
            "keyword, match type, device, and click type. Google click_view requires "
            "the exact click date and supports dates within the last 90 days. Read-only."
        ),
        inputSchema={
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id", "gclid", "click_date"],
            "properties": {
                "customer_id": _CUSTOMER_ID,
                "gclid": {"type": "string", "minLength": 1, "maxLength": 512},
                "click_date": _DATE,
            },
        },
        annotations=_READ_ONLY,
    ),
    mcp_types.Tool(
        name="list_conversion_actions",
        title="List conversion actions",
        description=(
            "List Google Ads conversion actions and their exact resource names. "
            "Use the returned resource_name for offline uploads; callers do not need "
            "to match a free-form conversion name. Read-only."
        ),
        inputSchema={
            "type": "object",
            "additionalProperties": False,
            "required": ["customer_id"],
            "properties": {
                "customer_id": _CUSTOMER_ID,
                "enabled_only": {"type": "boolean", "default": True},
            },
        },
        annotations=_READ_ONLY,
    ),
    mcp_types.Tool(
        name="upload_offline_conversion",
        title="Validate or upload an offline conversion",
        description=(
            "Validate or upload one CRM conversion using a GCLID, GBRAID, or WBRAID. "
            "The default validate_only=true performs no write. Before a real upload, "
            "first validate the identical payload, then explicitly set "
            "validate_only=false and confirm_write=true. order_id must be a stable "
            "unique CRM identifier. Do not represent bad leads as negative values; "
            "upload qualified stages as separate actions or use adjustments."
        ),
        inputSchema={
            "type": "object",
            "additionalProperties": False,
            "required": [
                "customer_id",
                "conversion_action_resource_name",
                "click_id",
                "click_id_type",
                "conversion_date_time",
                "conversion_value",
                "currency_code",
                "order_id",
            ],
            "properties": {
                "customer_id": _CUSTOMER_ID,
                "conversion_action_resource_name": {
                    "type": "string",
                    "pattern": "^customers/[0-9]{10}/conversionActions/[0-9]+$",
                },
                "click_id": {"type": "string", "minLength": 1, "maxLength": 512},
                "click_id_type": {
                    "type": "string",
                    "enum": ["GCLID", "GBRAID", "WBRAID"],
                },
                "conversion_date_time": {
                    "type": "string",
                    "description": "ISO date-time with explicit UTC offset.",
                },
                "conversion_value": {"type": "number", "minimum": 0},
                "currency_code": {
                    "type": "string",
                    "pattern": "^[A-Za-z]{3}$",
                },
                "order_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "ad_user_data_consent": {
                    "type": "string",
                    "enum": ["GRANTED", "DENIED", "UNSPECIFIED"],
                },
                "validate_only": {"type": "boolean", "default": True},
                "confirm_write": {"type": "boolean", "default": False},
            },
        },
        annotations=_WRITE,
    ),
]

_TOOL_MAP: dict[str, Callable[..., dict[str, Any]]] = {
    "list_accessible_customers": list_accessible_customers,
    "list_linked_accounts": list_linked_accounts,
    "get_customer_details": get_customer_details,
    "get_campaign_performance": get_campaign_performance,
    "get_search_terms": get_search_terms,
    "lookup_gclid": lookup_gclid,
    "list_conversion_actions": list_conversion_actions,
    "upload_offline_conversion": upload_offline_conversion,
}


@app.list_tools()
async def list_tools() -> list[mcp_types.Tool]:
    """Return the fixed Google Ads tool list."""

    return mcp_tools


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[mcp_types.Content]:
    """Dispatch a tool call without blocking the event loop."""

    function = _TOOL_MAP.get(name)
    if function is None:
        result = {"error": f"Tool '{name}' is not implemented by this server."}
    else:
        try:
            result = await asyncio.to_thread(function, **(arguments or {}))
        except (GoogleAdsError, RuntimeError, TypeError, ValueError) as exc:
            print(f"Google Ads MCP tool '{name}' failed: {exc}", file=sys.stderr)
            result = {"error": str(exc), "tool": name}

    return [
        mcp_types.TextContent(
            type="text",
            text=json.dumps(result, indent=2, ensure_ascii=False),
        )
    ]

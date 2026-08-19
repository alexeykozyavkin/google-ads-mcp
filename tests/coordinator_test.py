"""Tests for the MCP tool surface."""

import asyncio
import json
import unittest
from unittest import mock

from google_ads_mcp import coordinator


class CoordinatorTest(unittest.TestCase):
    def test_exposes_expected_tools_and_only_one_write_tool(self):
        names = {tool.name for tool in coordinator.mcp_tools}

        self.assertEqual(
            names,
            {
                "list_accessible_customers",
                "list_linked_accounts",
                "get_customer_details",
                "get_campaign_performance",
                "get_search_terms",
                "lookup_gclid",
                "list_conversion_actions",
                "upload_offline_conversion",
            },
        )
        write_tools = [
            tool for tool in coordinator.mcp_tools if not tool.annotations.readOnlyHint
        ]
        self.assertEqual(
            [tool.name for tool in write_tools], ["upload_offline_conversion"]
        )
        self.assertFalse(write_tools[0].annotations.destructiveHint)
        self.assertFalse(write_tools[0].annotations.idempotentHint)

    def test_unknown_tool_returns_structured_error(self):
        result = asyncio.run(coordinator.call_tool("not_a_tool", {}))
        payload = json.loads(result[0].text)

        self.assertIn("not implemented", payload["error"])

    def test_dispatches_without_exposing_exception_details(self):
        with mock.patch.dict(
            coordinator._TOOL_MAP,
            {"get_customer_details": mock.Mock(side_effect=ValueError("bad input"))},
        ):
            result = asyncio.run(
                coordinator.call_tool(
                    "get_customer_details",
                    {"customer_id": "bad"},
                )
            )
        payload = json.loads(result[0].text)

        self.assertEqual(payload["error"], "bad input")
        self.assertEqual(payload["tool"], "get_customer_details")


if __name__ == "__main__":
    unittest.main()

"""Tests for Google Ads REST helpers and safety controls."""

import unittest
from unittest import mock

from google_ads_mcp import client


class FakeAdsClient:
    def __init__(self):
        self.queries = []
        self.requests = []

    def search(self, customer_id, query):
        self.queries.append((customer_id, query))
        return [{"mock": "row"}]

    def request(self, method, path, json_body=None):
        self.requests.append((method, path, json_body))
        if path == "/customers:listAccessibleCustomers":
            return {"resourceNames": ["customers/1234567890"]}
        return {"jobId": "42", "results": []}


class ReportingToolsTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAdsClient()
        self.patch = mock.patch.object(client, "_client", return_value=self.fake)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_lists_accessible_customers(self):
        result = client.list_accessible_customers()

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["customers"][0]["customer_id"], "1234567890")

    def test_lists_directly_linked_accounts_from_manager(self):
        result = client.list_linked_accounts("123-456-7890", 25)

        customer, query = self.fake.queries[-1]
        self.assertEqual(customer, "1234567890")
        self.assertIn("FROM customer_client", query)
        self.assertIn("customer_client.level <= 1", query)
        self.assertIn("LIMIT 25", query)
        self.assertEqual(result["count"], 1)
        self.assertIn("level 1", result["note"].lower())

    def test_campaign_query_normalizes_customer_and_dates(self):
        result = client.get_campaign_performance(
            "123-456-7890",
            "2026-08-01",
            "2026-08-18",
            25,
        )

        customer, query = self.fake.queries[-1]
        self.assertEqual(customer, "1234567890")
        self.assertIn("segments.date BETWEEN '2026-08-01' AND '2026-08-18'", query)
        self.assertIn("LIMIT 25", query)
        self.assertEqual(result["count"], 1)

    def test_rejects_invalid_ranges_and_limits(self):
        with self.assertRaises(ValueError):
            client.get_search_terms(
                "1234567890",
                "2026-08-18",
                "2026-08-01",
            )
        with self.assertRaises(ValueError):
            client.get_campaign_performance(
                "1234567890",
                "2026-08-01",
                "2026-08-18",
                1001,
            )

    def test_gclid_lookup_requires_click_date(self):
        result = client.lookup_gclid(
            "1234567890",
            "CjwK-test'value",
            "2026-08-06",
        )

        _, query = self.fake.queries[-1]
        self.assertIn("segments.date = '2026-08-06'", query)
        self.assertIn("CjwK-test\\'value", query)
        self.assertIn("last 90 days", result["note"])


class ConversionUploadTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAdsClient()
        self.patch = mock.patch.object(client, "_client", return_value=self.fake)
        self.patch.start()
        self.arguments = {
            "customer_id": "123-456-7890",
            "conversion_action_resource_name": (
                "customers/1234567890/conversionActions/987654321"
            ),
            "click_id": "CjwK-test",
            "click_id_type": "GCLID",
            "conversion_date_time": "2026-08-18T15:30:00+07:00",
            "conversion_value": 250.0,
            "currency_code": "usd",
            "order_id": "CRM-OPP-42",
        }

    def tearDown(self):
        self.patch.stop()

    def test_validation_only_is_default(self):
        result = client.upload_offline_conversion(**self.arguments)

        method, path, payload = self.fake.requests[-1]
        conversion = payload["conversions"][0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/customers/1234567890:uploadClickConversions")
        self.assertTrue(payload["validateOnly"])
        self.assertTrue(payload["partialFailure"])
        self.assertEqual(conversion["gclid"], "CjwK-test")
        self.assertEqual(conversion["currencyCode"], "USD")
        self.assertEqual(conversion["orderId"], "CRM-OPP-42")
        self.assertEqual(conversion["conversionDateTime"], "2026-08-18 15:30:00+07:00")
        self.assertEqual(result["mode"], "validation_only")

    def test_real_upload_requires_explicit_confirmation(self):
        with self.assertRaisesRegex(ValueError, "confirm_write=true"):
            client.upload_offline_conversion(
                **self.arguments,
                validate_only=False,
            )

        result = client.upload_offline_conversion(
            **self.arguments,
            validate_only=False,
            confirm_write=True,
        )
        self.assertEqual(result["mode"], "uploaded")

    def test_rejects_negative_value_and_missing_timezone(self):
        negative = dict(self.arguments, conversion_value=-1)
        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            client.upload_offline_conversion(**negative)

        no_timezone = dict(
            self.arguments,
            conversion_date_time="2026-08-18T15:30:00",
        )
        with self.assertRaisesRegex(ValueError, "explicit UTC offset"):
            client.upload_offline_conversion(**no_timezone)


if __name__ == "__main__":
    unittest.main()

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


class FakeDataManagerClient:
    def __init__(self, login_customer_id="9876543210"):
        self.login_customer_id = login_customer_id
        self.requests = []

    def request(self, method, path, json_body=None, params=None):
        self.requests.append((method, path, json_body, params))
        if path == "/requestStatus:retrieve":
            return {"requestStatusPerDestination": [{"requestStatus": "SUCCESS"}]}
        return {"requestId": "dm-request-42", "fieldWarnings": []}


class FakeResponse:
    def __init__(self, *, status_code, payload, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class RestErrorDiagnosticsTest(unittest.TestCase):
    def test_extracts_google_ads_failure_from_search_stream_array(self):
        response = FakeResponse(
            status_code=403,
            headers={"request-id": "header-request-id"},
            payload=[
                {
                    "error": {
                        "code": 403,
                        "message": "The caller does not have permission",
                        "status": "PERMISSION_DENIED",
                        "details": [
                            {
                                "@type": (
                                    "type.googleapis.com/google.ads.googleads.v25."
                                    "errors.GoogleAdsFailure"
                                ),
                                "requestId": "body-request-id",
                                "errors": [
                                    {
                                        "errorCode": {
                                            "authorizationError": (
                                                "USER_PERMISSION_DENIED"
                                            )
                                        },
                                        "message": (
                                            "User doesn't have permission to access "
                                            "customer."
                                        ),
                                        "trigger": {
                                            "stringValue": "must-not-appear-in-log"
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                }
            ],
        )
        session = mock.Mock()
        session.request.return_value = response
        credentials = mock.Mock(valid=True, token="token")
        ads_client = client.GoogleAdsRestClient(
            developer_token="developer-token",
            credentials=credentials,
            session=session,
        )

        with self.assertRaises(client.GoogleAdsError) as caught:
            ads_client.request("POST", "/customers/1234567890/googleAds:searchStream")

        message = str(caught.exception)
        self.assertIn("HTTP 403 (PERMISSION_DENIED)", message)
        self.assertIn("authorizationError.USER_PERMISSION_DENIED", message)
        self.assertIn("User doesn't have permission", message)
        self.assertIn("Request ID: header-request-id", message)
        self.assertNotIn("must-not-appear-in-log", message)

    def test_uses_request_id_from_failure_body_when_header_is_missing(self):
        message = client._google_ads_error_message(
            403,
            {
                "error": {
                    "status": "PERMISSION_DENIED",
                    "details": [{"requestId": "body-request-id", "errors": []}],
                }
            },
            None,
        )

        self.assertIn("Request ID: body-request-id", message)

    def test_data_manager_client_uses_only_oauth_headers(self):
        response = FakeResponse(
            status_code=200,
            payload={"requestId": "dm-request-42"},
        )
        session = mock.Mock()
        session.request.return_value = response
        credentials = mock.Mock(valid=True, token="token")
        dm_client = client.DataManagerRestClient(
            credentials=credentials,
            login_customer_id="987-654-3210",
            session=session,
        )

        dm_client.request("POST", "/events:ingest", json_body={"events": []})

        headers = session.request.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer token")
        self.assertNotIn("developer-token", headers)
        self.assertNotIn("login-customer-id", headers)

    def test_data_manager_error_includes_field_violation(self):
        message = client._data_manager_error_message(
            400,
            {
                "error": {
                    "status": "INVALID_ARGUMENT",
                    "message": "Invalid request.",
                    "details": [
                        {
                            "fieldViolations": [
                                {
                                    "field": "events[0].eventTimestamp",
                                    "description": "Timestamp is outside the window.",
                                }
                            ]
                        }
                    ],
                }
            },
        )

        self.assertIn("HTTP 400 (INVALID_ARGUMENT)", message)
        self.assertIn("events[0].eventTimestamp", message)
        self.assertIn("outside the window", message)


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
        self.fake = FakeDataManagerClient()
        self.patch = mock.patch.object(
            client,
            "_data_manager_client",
            return_value=self.fake,
        )
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

        method, path, payload, params = self.fake.requests[-1]
        destination = payload["destinations"][0]
        event = payload["events"][0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/events:ingest")
        self.assertIsNone(params)
        self.assertTrue(payload["validateOnly"])
        self.assertEqual(
            destination["operatingAccount"],
            {"accountType": "GOOGLE_ADS", "accountId": "1234567890"},
        )
        self.assertEqual(
            destination["loginAccount"],
            {"accountType": "GOOGLE_ADS", "accountId": "9876543210"},
        )
        self.assertEqual(destination["productDestinationId"], "987654321")
        self.assertEqual(event["adIdentifiers"]["gclid"], "CjwK-test")
        self.assertEqual(event["currency"], "USD")
        self.assertEqual(event["transactionId"], "CRM-OPP-42")
        self.assertEqual(event["eventTimestamp"], "2026-08-18T15:30:00+07:00")
        self.assertEqual(event["eventSource"], "WEB")
        self.assertEqual(result["mode"], "validation_only")
        self.assertEqual(result["request_id"], "dm-request-42")

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
        self.assertEqual(result["mode"], "submitted")

    def test_maps_consent_to_data_manager_enum(self):
        client.upload_offline_conversion(
            **self.arguments,
            ad_user_data_consent="granted",
        )

        event = self.fake.requests[-1][2]["events"][0]
        self.assertEqual(
            event["consent"],
            {"adUserData": "CONSENT_GRANTED"},
        )

    def test_rejects_mismatched_conversion_customer(self):
        mismatched = dict(
            self.arguments,
            conversion_action_resource_name=(
                "customers/9999999999/conversionActions/987654321"
            ),
        )

        with self.assertRaisesRegex(ValueError, "must match"):
            client.upload_offline_conversion(**mismatched)

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

    def test_retrieves_async_upload_status(self):
        result = client.get_offline_conversion_upload_status("dm-request-42")

        method, path, payload, params = self.fake.requests[-1]
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/requestStatus:retrieve")
        self.assertIsNone(payload)
        self.assertEqual(params, {"requestId": "dm-request-42"})
        self.assertEqual(result["request_id"], "dm-request-42")


if __name__ == "__main__":
    unittest.main()

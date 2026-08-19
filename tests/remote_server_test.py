"""Tests for hosted MCP authentication and OAuth metadata."""

import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from google_ads_mcp import remote_server


class Auth0ConfigurationTest(unittest.TestCase):
    def test_loads_complete_configuration(self):
        with mock.patch.dict(
            os.environ,
            {
                "AUTH0_DOMAIN": "tenant.us.auth0.com",
                "AUTH0_AUDIENCE": "https://ads.example.com/",
            },
            clear=True,
        ):
            configuration = remote_server.get_auth0_configuration()

        self.assertIsNotNone(configuration)
        self.assertEqual(configuration.audience, "https://ads.example.com")
        self.assertEqual(configuration.scope, "ads:manage")

    def test_rejects_partial_configuration(self):
        with mock.patch.dict(
            os.environ,
            {"AUTH0_DOMAIN": "tenant.us.auth0.com"},
            clear=True,
        ):
            with self.assertRaises(RuntimeError):
                remote_server.get_auth0_configuration()


class RemoteAuthMiddlewareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

    def _token(self, scope: str) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iss": "https://tenant.us.auth0.com/",
                "aud": "https://ads.example.com",
                "sub": "auth0|test-user",
                "iat": now,
                "exp": now + 300,
                "scope": scope,
            },
            self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    def test_requires_valid_token_and_ads_scope(self):
        async def endpoint(_):
            return JSONResponse({"ok": True})

        application = Starlette(routes=[Route("/mcp", endpoint)])
        with mock.patch.dict(
            os.environ,
            {
                "AUTH0_DOMAIN": "tenant.us.auth0.com",
                "AUTH0_AUDIENCE": "https://ads.example.com",
                "AUTH0_SCOPE": "ads:manage",
            },
            clear=True,
        ):
            middleware = remote_server.RemoteAuthMiddleware(application)
            middleware.auth0_verifier.jwks_client.get_signing_key_from_jwt = (
                lambda _: SimpleNamespace(key=self.private_key.public_key())
            )
            with TestClient(middleware) as test_client:
                missing = test_client.get("/mcp")
                wrong = test_client.get(
                    "/mcp",
                    headers={"Authorization": f"Bearer {self._token('other:read')}"},
                )
                valid = test_client.get(
                    "/mcp",
                    headers={"Authorization": f"Bearer {self._token('ads:manage')}"},
                )

        self.assertEqual(missing.status_code, 401)
        self.assertIn("resource_metadata=", missing.headers["www-authenticate"])
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(valid.status_code, 200)


if __name__ == "__main__":
    unittest.main()

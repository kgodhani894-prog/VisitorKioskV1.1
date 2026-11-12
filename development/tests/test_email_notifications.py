import os
import sys
import unittest
import base64
import importlib.util
import tempfile
import types
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from kiosk_server import notifications


class _AcceptedResponse:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class EmailNotificationTests(unittest.TestCase):
    def test_oauth_setup_requires_company_https_callback_and_host_certificate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            certificate = root / "public-certificate.pem"
            private_key = root / "private-key.pem"
            certificate.write_text("-----BEGIN CERTIFICATE-----\npublic\n", encoding="ascii")
            private_key.write_text("-----BEGIN PRIVATE KEY-----\nsynthetic\n", encoding="ascii")
            env = {
                "M365_TENANT_ID": "synthetic-tenant",
                "M365_CLIENT_ID": "synthetic-client",
                "M365_CERTIFICATE_PATH": str(certificate),
                "M365_CERTIFICATE_THUMBPRINT": "synthetic-thumbprint",
                "M365_PRIVATE_KEY_PATH": str(private_key),
                "M365_REDIRECT_URI": "https://kiosk.example.internal/oauth/microsoft/callback",
                "KIOSK_SESSION_SECRET": "synthetic-long-host-session-secret",
            }
            with patch.dict(os.environ, env, clear=False):
                self.assertTrue(all(notifications.microsoft365_oauth_setup_status().values()))
                with patch.dict(os.environ, {"M365_REDIRECT_URI": "http://kiosk.example.internal/oauth/microsoft/callback"}):
                    self.assertFalse(notifications.microsoft365_oauth_setup_status()["redirect_ready"])

    def test_authorization_flow_requests_delegated_mail_send_and_form_post(self):
        captured = {}

        class FakeConfidentialClient:
            def __init__(self, client_id, *, authority, client_credential, token_cache):
                captured.update(client_id=client_id, authority=authority, credential=client_credential)

            def initiate_auth_code_flow(self, **kwargs):
                captured.update(kwargs)
                return {"auth_uri": "https://login.microsoftonline.com/tenant/authorize", "state": kwargs["state"]}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            certificate = root / "public-certificate.pem"
            private_key = root / "private-key.pem"
            certificate.write_text("-----BEGIN CERTIFICATE-----\npublic\n", encoding="ascii")
            private_key.write_text("-----BEGIN PRIVATE KEY-----\nsynthetic\n", encoding="ascii")
            env = {
                "M365_TENANT_ID": "synthetic-tenant",
                "M365_CLIENT_ID": "synthetic-client",
                "M365_CERTIFICATE_PATH": str(certificate),
                "M365_CERTIFICATE_THUMBPRINT": "synthetic-thumbprint",
                "M365_PRIVATE_KEY_PATH": str(private_key),
                "M365_REDIRECT_URI": "https://kiosk.example.internal/oauth/microsoft/callback",
                "KIOSK_SESSION_SECRET": "synthetic-long-host-session-secret",
            }
            fake_msal = types.SimpleNamespace(ConfidentialClientApplication=FakeConfidentialClient)
            with patch.dict(os.environ, env, clear=False), patch.dict(sys.modules, {"msal": fake_msal}):
                flow = notifications.initiate_microsoft365_auth_flow("state-from-test")
        self.assertEqual(flow["auth_uri"], "https://login.microsoftonline.com/tenant/authorize")
        self.assertEqual(captured["redirect_uri"], env["M365_REDIRECT_URI"])
        self.assertEqual(captured["response_mode"], "form_post")
        self.assertEqual(captured["prompt"], "select_account")
        self.assertIn(notifications.GRAPH_MAIL_SEND_SCOPE, captured["scopes"])

    @unittest.skipUnless(importlib.util.find_spec("cryptography"), "cryptography is installed with the app requirements")
    def test_msal_token_cache_is_encrypted_and_requires_the_same_host_secret(self):
        # Import before patching sys.modules: patch.dict restores the old module
        # table and would otherwise discard crypto modules imported in the block.
        from cryptography.fernet import Fernet  # noqa: F401

        class FakeSerializableTokenCache:
            serialized_value = None
            has_state_changed = True

            def serialize(self):
                return "synthetic-refresh-token-value"

            def deserialize(self, value):
                self.serialized_value = value

        fake_msal = types.SimpleNamespace(SerializableTokenCache=FakeSerializableTokenCache)
        with tempfile.TemporaryDirectory() as temporary, patch.dict(sys.modules, {"msal": fake_msal}):
            root = Path(temporary)
            cache = FakeSerializableTokenCache()
            notifications._save_token_cache(cache, root, "synthetic-long-host-session-secret")
            saved_path = root / "data" / notifications.TOKEN_CACHE_NAME
            encrypted = saved_path.read_bytes()
            self.assertNotIn(b"synthetic-refresh-token-value", encrypted)
            loaded = notifications._load_token_cache(root, "synthetic-long-host-session-secret")
            self.assertEqual(loaded.serialized_value, "synthetic-refresh-token-value")
            with self.assertRaises(ValueError):
                notifications._load_token_cache(root, "another-synthetic-host-secret")

    def test_email_readiness_requires_a_pem_private_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            certificate = root / "public-certificate.pem"
            private_key = root / "private-key.pem"
            combined = root / "combined.pem"
            certificate.write_text("-----BEGIN CERTIFICATE-----\npublic\n", encoding="ascii")
            private_key.write_text("-----BEGIN PRIVATE KEY-----\nsynthetic\n", encoding="ascii")
            combined.write_text(
                "-----BEGIN CERTIFICATE-----\npublic\n-----BEGIN PRIVATE KEY-----\nsynthetic\n",
                encoding="ascii",
            )
            values = {
                "M365_TENANT_ID": "tenant",
                "M365_CLIENT_ID": "client",
                "M365_SENDER_ADDRESS": "sender@example.com",
                "M365_CERTIFICATE_PATH": str(certificate),
                "M365_CERTIFICATE_THUMBPRINT": "thumbprint",
                "M365_PRIVATE_KEY_PATH": "",
                "M365_GRAPH_SCOPE": notifications.GRAPH_SCOPE,
            }
            with patch.dict(os.environ, values, clear=False):
                status = notifications.microsoft365_configuration_status()
                self.assertTrue(status["certificate_configured"])
                self.assertFalse(status["private_key_configured"])

                with patch.dict(os.environ, {"M365_PRIVATE_KEY_PATH": str(private_key)}):
                    status = notifications.microsoft365_configuration_status()
                self.assertTrue(status["private_key_configured"])

                with patch.dict(os.environ, {
                    "M365_CERTIFICATE_PATH": str(combined),
                    "M365_PRIVATE_KEY_PATH": "",
                }):
                    status = notifications.microsoft365_configuration_status()
                self.assertTrue(status["private_key_configured"])

    def test_access_token_uses_private_pem_and_expected_graph_scope(self):
        captured = {}

        class FakeConfidentialClient:
            def __init__(self, client_id, *, authority, client_credential):
                captured["client_id"] = client_id
                captured["authority"] = authority
                captured["client_credential"] = client_credential

            def acquire_token_for_client(self, *, scopes):
                captured["scopes"] = scopes
                return {"access_token": "synthetic-access-token"}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            certificate = root / "public-certificate.pem"
            private_key = root / "private-key.pem"
            certificate.write_text("-----BEGIN CERTIFICATE-----\npublic\n", encoding="ascii")
            private_key.write_text("-----BEGIN PRIVATE KEY-----\nsynthetic-private-key\n", encoding="ascii")
            values = {
                "M365_TENANT_ID": "synthetic-tenant",
                "M365_CLIENT_ID": "synthetic-client",
                "M365_SENDER_ADDRESS": "sender@example.invalid",
                "M365_CERTIFICATE_PATH": str(certificate),
                "M365_CERTIFICATE_THUMBPRINT": "synthetic-thumbprint",
                "M365_PRIVATE_KEY_PATH": str(private_key),
                "M365_GRAPH_SCOPE": notifications.GRAPH_SCOPE,
            }
            fake_msal = types.SimpleNamespace(ConfidentialClientApplication=FakeConfidentialClient)
            with patch.dict(os.environ, values, clear=False), patch.dict(sys.modules, {"msal": fake_msal}):
                token = notifications._access_token()

        self.assertEqual(token, "synthetic-access-token")
        self.assertEqual(captured["client_id"], "synthetic-client")
        self.assertEqual(captured["authority"], "https://login.microsoftonline.com/synthetic-tenant")
        self.assertEqual(captured["client_credential"], {
            "private_key": "-----BEGIN PRIVATE KEY-----\nsynthetic-private-key\n",
            "thumbprint": "synthetic-thumbprint",
        })
        self.assertEqual(captured["scopes"], [notifications.GRAPH_SCOPE])

    def test_message_contains_plain_and_escaped_html(self):
        subject, plain, rich = notifications.email_message({
            "first_name": "A<",
            "last_name": "B&",
            "company": "C>",
            "kiosk_id": "Main Lobby",
            "created_at": "2026-09-04 12:00:00 UTC",
        })
        self.assertEqual(subject, "Visitor waiting at Elite RF")
        self.assertIn("A< B&", plain)
        self.assertIn("A&lt; B&amp;", rich)
        self.assertIn("C&gt;", rich)
        self.assertIn("Please report to reception.", plain)

    def test_graph_202_is_success_and_uses_multipart_mime(self):
        with patch.dict(os.environ, {"M365_SENDER_ADDRESS": "alerts@example.com"}, clear=False), \
             patch.object(notifications, "_access_token", return_value="test-token"), \
             patch.object(notifications.urllib.request, "urlopen", return_value=_AcceptedResponse()) as urlopen:
            status, detail = notifications.send_email(
                {"email": "host@example.com"},
                "Visitor waiting at Elite RF",
                "plain visitor notice",
                "<p>rich visitor notice</p>",
            )
        self.assertEqual(status, "sent")
        self.assertIn("accepted", detail.lower())
        request = urlopen.call_args.args[0]
        self.assertEqual(request.headers["Content-type"], "text/plain")
        mime_payload = base64.b64decode(request.data)
        self.assertIn(b"plain visitor notice", mime_payload)
        self.assertIn(b"rich visitor notice", mime_payload)

    def test_connected_mailbox_sends_as_me_to_selected_employee(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "data" / notifications.TOKEN_CACHE_NAME
            cache.parent.mkdir()
            cache.write_bytes(b"encrypted-test-cache")
            with patch.object(notifications, "_delegated_access_token", return_value=("delegated-token", "alerts@example.com")), \
                 patch.object(notifications, "_access_token", side_effect=AssertionError("app-only sender must not be used")), \
                 patch.object(notifications.urllib.request, "urlopen", return_value=_AcceptedResponse()) as urlopen:
                status, _ = notifications.send_email(
                    {"email": "employee@example.com"}, "Subject", "plain", "<p>html</p>",
                    runtime_root=root, session_secret="synthetic-session-secret",
                )
        self.assertEqual(status, "sent")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://graph.microsoft.com/v1.0/me/sendMail")
        mime_payload = base64.b64decode(request.data)
        self.assertIn(b"To: employee@example.com", mime_payload)
        self.assertNotIn(b"From:", mime_payload)

    def test_graph_rate_limit_is_pending(self):
        error = HTTPError("https://graph.microsoft.com", 429, "private response", {}, None)
        with patch.dict(os.environ, {"M365_SENDER_ADDRESS": "alerts@example.com"}, clear=False), \
             patch.object(notifications, "_access_token", return_value="test-token"), \
             patch.object(notifications.urllib.request, "urlopen", side_effect=error):
            status, detail = notifications.send_email(
                {"email": "host@example.com"}, "Subject", "plain", "<p>html</p>"
            )
        self.assertEqual(status, "pending")
        self.assertNotIn("private response", detail)


if __name__ == "__main__":
    unittest.main()

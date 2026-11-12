from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from kiosk_server import microsoft365_setup, notifications


@unittest.skipUnless(importlib.util.find_spec("cryptography"), "cryptography application dependency is not installed")
class Microsoft365SetupCryptoTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp())
        self.secret = "unit-test-session-secret-with-more-than-32-bytes"
        self.base_settings = {
            "tenant_id": "elite-rf.onmicrosoft.com",
            "client_id": "12345678-1234-1234-1234-1234567890ab",
            "redirect_uri": "https://visitors.example.internal/oauth/microsoft/callback",
        }
        microsoft365_setup.save_settings(self.tempdir, self.secret, dict(self.base_settings))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_generated_certificate_and_settings_are_encrypted_at_rest(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        result = microsoft365_setup.generate_certificate(self.tempdir, self.secret)
        settings_path = self.tempdir / "data" / microsoft365_setup.SETTINGS_NAME
        encrypted_bytes = settings_path.read_bytes()
        self.assertNotIn(b"elite-rf.onmicrosoft.com", encrypted_bytes)
        self.assertNotIn(b"PRIVATE KEY", encrypted_bytes)
        settings = microsoft365_setup.load_settings(self.tempdir, self.secret)
        self.assertEqual(result["thumbprint"], settings["certificate_thumbprint"])
        self.assertEqual(settings["tenant_id"], self.base_settings["tenant_id"])
        certificate_der = microsoft365_setup.public_certificate(self.tempdir, self.secret)
        certificate = x509.load_der_x509_certificate(certificate_der)
        private_key = serialization.load_pem_private_key(settings["private_key"].encode("ascii"), password=None)
        self.assertEqual(certificate.fingerprint(hashes.SHA1()).hex().upper(), result["thumbprint"])
        self.assertEqual(certificate.public_key().public_numbers(), private_key.public_key().public_numbers())
        status = microsoft365_setup.setup_status(self.tempdir, self.secret)
        self.assertTrue(status["certificate_ready"])
        self.assertNotIn("private_key", status)

        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        other_pem = other_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            microsoft365_setup.install_certificate(self.tempdir, self.secret, certificate_der, other_pem)

    def test_company_issued_certificate_upload_validates_pair_and_encrypts_it(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "IT-approved kiosk application")])
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=180))
            .sign(key, hashes.SHA256())
        )
        cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        result = microsoft365_setup.install_certificate(self.tempdir, self.secret, cert_pem, key_pem)
        self.assertEqual(result["thumbprint"], certificate.fingerprint(hashes.SHA1()).hex().upper())
        self.assertEqual(microsoft365_setup.public_certificate(self.tempdir, self.secret), certificate.public_bytes(serialization.Encoding.DER))
        encrypted = (self.tempdir / "data" / microsoft365_setup.SETTINGS_NAME).read_bytes()
        self.assertNotIn(key_pem.splitlines()[0], encrypted)

    def test_microsoft_signin_uses_saved_redirect_and_mail_send_scope(self):
        class FakeApplication:
            def initiate_auth_code_flow(self, **kwargs):
                self.kwargs = kwargs
                return {"auth_uri": "https://login.microsoftonline.com/tenant/authorize", "state": kwargs["state"]}

        empty_environment = {key: "" for key in (
            "M365_TENANT_ID", "M365_CLIENT_ID", "M365_REDIRECT_URI", "M365_CERTIFICATE_PATH",
            "M365_CERTIFICATE_THUMBPRINT", "M365_PRIVATE_KEY_PATH", "KIOSK_SESSION_SECRET",
        )}
        certificate = microsoft365_setup.generate_certificate(self.tempdir, self.secret)
        application = FakeApplication()
        with patch.dict(os.environ, empty_environment, clear=False), \
             patch("kiosk_server.notifications._msal_application", return_value=application):
            flow = notifications.initiate_microsoft365_auth_flow("state-value", self.tempdir, self.secret)
        self.assertEqual(flow["auth_uri"], "https://login.microsoftonline.com/tenant/authorize")
        self.assertEqual(application.kwargs["redirect_uri"], self.base_settings["redirect_uri"])
        self.assertIn("https://graph.microsoft.com/Mail.Send", application.kwargs["scopes"])
        self.assertEqual(application.kwargs["response_mode"], "form_post")
        self.assertTrue(certificate["thumbprint"])

    def test_successful_signin_saves_mailbox_for_delegated_sending(self):
        from cryptography.fernet import Fernet  # noqa: F401
        import json
        import sys
        import types

        class FakeSerializableTokenCache:
            def __init__(self):
                self.username = ""
                self.has_state_changed = False

            def serialize(self):
                return json.dumps({"username": self.username})

            def deserialize(self, serialized):
                self.username = json.loads(serialized)["username"]

            def get_accounts(self):
                return [{"username": self.username}] if self.username else []

        class FakeConfidentialClient:
            def __init__(self, client_id, *, authority, client_credential, token_cache):
                self.token_cache = token_cache

            def acquire_token_by_auth_code_flow(self, flow, auth_response):
                self.token_cache.username = "visitor-alerts@elite-rf.example"
                self.token_cache.has_state_changed = True
                return {
                    "access_token": "simulated-access-token",
                    "id_token_claims": {
                        "preferred_username": self.token_cache.username,
                        "tid": "elite-rf.onmicrosoft.com",
                    },
                }

            def acquire_token_silent(self, scopes, *, account):
                self.silent_scopes = scopes
                self.silent_account = account
                return {"access_token": "simulated-refreshed-token"}

        fake_msal = types.SimpleNamespace(
            SerializableTokenCache=FakeSerializableTokenCache,
            ConfidentialClientApplication=FakeConfidentialClient,
        )
        microsoft365_setup.generate_certificate(self.tempdir, self.secret)
        with patch.dict(sys.modules, {"msal": fake_msal}):
            connected_account = notifications.complete_microsoft365_auth_flow(
                {"state": "simulated-state"},
                {"state": "simulated-state", "code": "simulated-auth-code"},
                self.tempdir,
                self.secret,
            )
            connection = notifications.microsoft365_connection_status(self.tempdir, self.secret)
            access_token, sender_account = notifications._delegated_access_token(self.tempdir, self.secret)

        self.assertEqual(connected_account, "visitor-alerts@elite-rf.example")
        self.assertTrue(connection["connected"])
        self.assertEqual(connection["account"], connected_account)
        self.assertEqual(access_token, "simulated-refreshed-token")
        self.assertEqual(sender_account, connected_account)
        token_file = self.tempdir / "data" / notifications.TOKEN_CACHE_NAME
        self.assertNotIn(b"visitor-alerts@elite-rf.example", token_file.read_bytes())


if __name__ == "__main__":
    unittest.main()

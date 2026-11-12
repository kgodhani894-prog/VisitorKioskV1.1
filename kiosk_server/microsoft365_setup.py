"""Protected dashboard-managed setup for the delegated Microsoft 365 sender."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

SETTINGS_NAME = ".microsoft365-dashboard-settings"
CALLBACK_PATH = "/oauth/microsoft/callback"
TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,251}[A-Za-z0-9]$")
CLIENT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _fernet(session_secret: str):
    secret = str(session_secret or "").encode("utf-8")
    if len(secret) < 32:
        raise ValueError("The host needs a strong session secret before Microsoft 365 setup can be saved.")
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise ValueError("Microsoft 365 setup encryption is unavailable; reinstall the application requirements.") from exc
    key = hashlib.sha256(b"Elite RF Microsoft 365 dashboard setup v1\0" + secret).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _settings_path(runtime_root: Path) -> Path:
    return Path(runtime_root) / "data" / SETTINGS_NAME


def load_settings(runtime_root: Path, session_secret: str) -> dict:
    path = _settings_path(runtime_root)
    if not path.is_file():
        return {}
    try:
        raw = _fernet(session_secret).decrypt(path.read_bytes())
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Saved Microsoft 365 setup cannot be opened. Re-enter the setup or restore the original host session secret.") from exc
    if not isinstance(value, dict):
        raise ValueError("Saved Microsoft 365 setup is invalid.")
    return value


def save_settings(runtime_root: Path, session_secret: str, settings: dict) -> None:
    path = _settings_path(runtime_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _fernet(session_secret).encrypt((json.dumps(settings, sort_keys=True) + "\n").encode("utf-8"))
    descriptor, temporary_name = tempfile.mkstemp(prefix=".m365-setup-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def validate_app_settings(tenant_id: str, client_id: str, redirect_uri: str) -> dict[str, str]:
    tenant = str(tenant_id or "").strip()
    client = str(client_id or "").strip()
    redirect = str(redirect_uri or "").strip()
    if not TENANT_PATTERN.fullmatch(tenant) or tenant in {".", ".."}:
        raise ValueError("Enter the Microsoft Entra tenant ID or verified tenant domain supplied by IT.")
    if not CLIENT_ID_PATTERN.fullmatch(client):
        raise ValueError("Enter the application (client) ID supplied by IT.")
    if len(redirect) > 2048:
        raise ValueError("The HTTPS callback address is too long.")
    parsed = urlsplit(redirect)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("The HTTPS callback address has an invalid port.") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != CALLBACK_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"Enter the exact HTTPS callback URL ending in {CALLBACK_PATH}.")
    return {"tenant_id": tenant, "client_id": client, "redirect_uri": redirect}


def generate_certificate(runtime_root: Path, session_secret: str) -> dict[str, str]:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise ValueError("Certificate generation is unavailable; reinstall the application requirements.") from exc

    settings = load_settings(runtime_root, session_secret)
    if not settings.get("tenant_id") or not settings.get("client_id") or not settings.get("redirect_uri"):
        raise ValueError("Save the Microsoft tenant, application ID, and HTTPS callback before generating a certificate.")

    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Elite RF Visitor Kiosk Microsoft 365")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=730))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=None, decipher_only=None,
        ), critical=True)
        .sign(key, hashes.SHA256())
    )
    private_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    certificate_der = certificate.public_bytes(serialization.Encoding.DER)
    settings.update({
        "private_key": private_key,
        "certificate_der": base64.b64encode(certificate_der).decode("ascii"),
        "certificate_thumbprint": certificate.fingerprint(hashes.SHA1()).hex().upper(),
        "certificate_expires": certificate.not_valid_after_utc.isoformat(),
    })
    save_settings(runtime_root, session_secret, settings)
    return {"thumbprint": settings["certificate_thumbprint"], "expires_at": settings["certificate_expires"]}


def install_certificate(runtime_root: Path, session_secret: str, certificate_bytes: bytes, private_key_bytes: bytes) -> dict[str, str]:
    try:
        from cryptography import x509
        from cryptography.exceptions import UnsupportedAlgorithm
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError as exc:
        raise ValueError("Certificate handling is unavailable; reinstall the application requirements.") from exc

    settings = load_settings(runtime_root, session_secret)
    if not settings.get("tenant_id") or not settings.get("client_id") or not settings.get("redirect_uri"):
        raise ValueError("Save the Microsoft tenant, application ID, and HTTPS callback before adding a certificate.")
    try:
        try:
            certificate = x509.load_pem_x509_certificate(certificate_bytes)
        except ValueError:
            certificate = x509.load_der_x509_certificate(certificate_bytes)
        private_key = serialization.load_pem_private_key(private_key_bytes, password=None)
    except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
        raise ValueError("Choose a readable public certificate and an unencrypted PEM private-key file.") from exc
    if not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size < 2048:
        raise ValueError("The Microsoft app certificate must use an RSA key of at least 2048 bits.")
    now = datetime.now(timezone.utc)
    if certificate.not_valid_before_utc > now or certificate.not_valid_after_utc <= now:
        raise ValueError("The certificate is not currently valid. Check its start and expiration dates.")
    public_format = serialization.Encoding.DER
    public_algorithm = serialization.PublicFormat.SubjectPublicKeyInfo
    if certificate.public_key().public_bytes(public_format, public_algorithm) != private_key.public_key().public_bytes(public_format, public_algorithm):
        raise ValueError("The selected private key does not match the public certificate.")
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    certificate_der = certificate.public_bytes(serialization.Encoding.DER)
    settings.update({
        "private_key": private_pem,
        "certificate_der": base64.b64encode(certificate_der).decode("ascii"),
        "certificate_thumbprint": certificate.fingerprint(hashes.SHA1()).hex().upper(),
        "certificate_expires": certificate.not_valid_after_utc.isoformat(),
    })
    save_settings(runtime_root, session_secret, settings)
    return {"thumbprint": settings["certificate_thumbprint"], "expires_at": settings["certificate_expires"]}


def public_certificate(runtime_root: Path, session_secret: str) -> bytes:
    settings = load_settings(runtime_root, session_secret)
    encoded = settings.get("certificate_der")
    if not encoded:
        raise ValueError("Generate the Microsoft 365 application certificate first.")
    try:
        return base64.b64decode(str(encoded), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("The saved public certificate is invalid.") from exc


def setup_status(runtime_root: Path, session_secret: str) -> dict[str, object]:
    try:
        settings = load_settings(runtime_root, session_secret)
    except ValueError:
        settings = {}
        unreadable = True
    else:
        unreadable = False
    return {
        "tenant_id": settings.get("tenant_id", ""),
        "client_id": settings.get("client_id", ""),
        "tenant_configured": bool(settings.get("tenant_id")),
        "application_configured": bool(settings.get("client_id")),
        "redirect_ready": bool(settings.get("redirect_uri")),
        "certificate_ready": bool(settings.get("private_key") and settings.get("certificate_der") and settings.get("certificate_thumbprint")),
        "token_encryption_ready": len(str(session_secret or "")) >= 32,
        "configuration_unreadable": unreadable,
        "certificate_thumbprint": settings.get("certificate_thumbprint", ""),
        "certificate_expires": settings.get("certificate_expires", ""),
        "redirect_uri": settings.get("redirect_uri", ""),
    }

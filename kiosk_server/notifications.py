from __future__ import annotations

import html
import hashlib
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import json
from email.message import EmailMessage
import base64
from urllib.parse import urlsplit

from .microsoft365_setup import load_settings

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_MAIL_SEND_SCOPE = "https://graph.microsoft.com/Mail.Send"
OAUTH_CALLBACK_PATH = "/oauth/microsoft/callback"
TOKEN_CACHE_NAME = ".microsoft365-token-cache"
PEM_PRIVATE_KEY_TYPES = (b"PRIVATE KEY", b"RSA PRIVATE KEY", b"EC PRIVATE KEY", b"DSA PRIVATE KEY")
_TOKEN_CACHE_LOCK = threading.RLock()


def _values(runtime_root: Path | None = None, session_secret: str | None = None) -> dict[str, str]:
    values = {key: os.environ.get(key, "").strip() for key in (
        "M365_TENANT_ID", "M365_CLIENT_ID", "M365_SENDER_ADDRESS",
        "M365_CERTIFICATE_PATH", "M365_CERTIFICATE_THUMBPRINT",
        "M365_PRIVATE_KEY_PATH", "M365_GRAPH_SCOPE", "M365_REDIRECT_URI",
    )}
    if runtime_root is not None and session_secret:
        try:
            saved = load_settings(runtime_root, session_secret)
        except ValueError:
            saved = {}
        for env_key, saved_key in (("M365_TENANT_ID", "tenant_id"), ("M365_CLIENT_ID", "client_id"), ("M365_REDIRECT_URI", "redirect_uri")):
            if saved.get(saved_key):
                values[env_key] = str(saved[saved_key]).strip()
    return values


def _client_credential(values: dict[str, str], runtime_root: Path | None = None, session_secret: str | None = None) -> dict[str, str]:
    if runtime_root is not None and session_secret:
        try:
            saved = load_settings(runtime_root, session_secret)
        except ValueError:
            saved = {}
        if saved.get("private_key") and saved.get("certificate_thumbprint"):
            return {"private_key": str(saved["private_key"]), "thumbprint": str(saved["certificate_thumbprint"])}
    required = ("M365_TENANT_ID", "M365_CLIENT_ID", "M365_CERTIFICATE_PATH", "M365_CERTIFICATE_THUMBPRINT")
    if any(not values[key] for key in required):
        raise ValueError("Microsoft 365 application setup is incomplete.")
    if not os.path.isfile(values["M365_CERTIFICATE_PATH"]):
        raise ValueError("Microsoft 365 certificate configuration is invalid.")
    key_path = values["M365_PRIVATE_KEY_PATH"] or values["M365_CERTIFICATE_PATH"]
    if values["M365_PRIVATE_KEY_PATH"] and not os.path.isfile(values["M365_PRIVATE_KEY_PATH"]):
        raise ValueError("Microsoft 365 private-key configuration is invalid.")
    if not _has_unencrypted_pem_private_key(key_path):
        raise ValueError("Microsoft 365 private-key configuration must contain an unencrypted PEM private key.")
    try:
        with open(key_path, "r", encoding="utf-8") as handle:
            private_key = handle.read()
    except (OSError, UnicodeError) as exc:
        raise ValueError("Microsoft 365 private-key configuration is invalid.") from exc
    return {"private_key": private_key, "thumbprint": values["M365_CERTIFICATE_THUMBPRINT"]}


def _validate_redirect_uri(value: str) -> str:
    uri = str(value or "").strip()
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != OAUTH_CALLBACK_PATH
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Set M365_REDIRECT_URI to the exact HTTPS Microsoft callback URL.")
    return uri


def microsoft365_oauth_setup_status(runtime_root: Path | None = None, session_secret: str | None = None) -> dict[str, bool]:
    values = _values(runtime_root, session_secret)
    try:
        saved = load_settings(runtime_root, session_secret) if runtime_root is not None and session_secret else {}
    except ValueError:
        saved = {}
    certificate_ready = bool(
        saved.get("private_key") and saved.get("certificate_der") and saved.get("certificate_thumbprint")
    ) or (
        bool(values["M365_CERTIFICATE_PATH"])
        and os.path.isfile(values["M365_CERTIFICATE_PATH"])
        and bool(values["M365_CERTIFICATE_THUMBPRINT"])
        and bool(values["M365_PRIVATE_KEY_PATH"] or values["M365_CERTIFICATE_PATH"])
        and _has_unencrypted_pem_private_key(values["M365_PRIVATE_KEY_PATH"] or values["M365_CERTIFICATE_PATH"])
    )
    try:
        _validate_redirect_uri(values["M365_REDIRECT_URI"])
        redirect_ready = True
    except ValueError:
        redirect_ready = False
    secret_ready = len(str(session_secret or os.environ.get("KIOSK_SESSION_SECRET", "")).strip()) >= 32
    return {
        "tenant_configured": bool(values["M365_TENANT_ID"]),
        "application_configured": bool(values["M365_CLIENT_ID"]),
        "certificate_ready": certificate_ready,
        "redirect_ready": redirect_ready,
        "token_encryption_ready": secret_ready,
    }


def microsoft365_oauth_ready() -> bool:
    return all(microsoft365_oauth_setup_status().values())


def _msal_application(*, token_cache=None, credential: bool = True, runtime_root: Path | None = None, session_secret: str | None = None):
    values = _values(runtime_root, session_secret)
    if not values["M365_TENANT_ID"] or not values["M365_CLIENT_ID"]:
        raise ValueError("Microsoft 365 application setup is incomplete.")
    try:
        import msal
    except ImportError as exc:
        raise ValueError("The Microsoft identity library is not installed.") from exc
    client_credential = _client_credential(values, runtime_root, session_secret) if credential else None
    return msal.ConfidentialClientApplication(
        values["M365_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{values['M365_TENANT_ID']}",
        client_credential=client_credential,
        token_cache=token_cache,
    )


def initiate_microsoft365_auth_flow(state: str, runtime_root: Path | None = None, session_secret: str | None = None) -> dict:
    if not all(microsoft365_oauth_setup_status(runtime_root, session_secret).values()):
        raise ValueError("Microsoft 365 connection is not fully configured on the host.")
    values = _values(runtime_root, session_secret)
    redirect_uri = _validate_redirect_uri(values["M365_REDIRECT_URI"])
    app = _msal_application(runtime_root=runtime_root, session_secret=session_secret)
    flow = app.initiate_auth_code_flow(
        scopes=[GRAPH_MAIL_SEND_SCOPE, "offline_access", "openid", "profile"],
        redirect_uri=redirect_uri,
        state=state,
        response_mode="form_post",
        prompt="select_account",
    )
    auth_uri = flow.get("auth_uri") if isinstance(flow, dict) else None
    parsed = urlsplit(str(auth_uri or ""))
    if not auth_uri or parsed.scheme != "https" or parsed.hostname != "login.microsoftonline.com":
        raise ValueError("Microsoft sign-in could not be started. Check the host's Entra settings.")
    return flow


def _token_cache_path(runtime_root: Path) -> Path:
    return Path(runtime_root) / "data" / TOKEN_CACHE_NAME


def _token_cache_fernet(session_secret: str):
    secret = str(session_secret or "").encode("utf-8")
    if len(secret) < 32:
        raise ValueError("The host needs a strong KIOSK_SESSION_SECRET before Microsoft 365 can be connected.")
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise ValueError("Token-cache encryption is unavailable; reinstall the application requirements.") from exc
    key = hashlib.sha256(b"Elite RF Microsoft 365 token cache v1\0" + secret).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _load_token_cache(runtime_root: Path, session_secret: str):
    try:
        import msal
    except ImportError as exc:
        raise ValueError("The Microsoft identity library is not installed.") from exc
    cache = msal.SerializableTokenCache()
    path = _token_cache_path(runtime_root)
    if path.exists():
        try:
            encrypted = path.read_bytes()
            if len(encrypted) > 2_000_000:
                raise ValueError("Saved Microsoft 365 sign-in data is invalid; disconnect and reconnect the account.")
            serialized = _token_cache_fernet(session_secret).decrypt(encrypted).decode("utf-8")
            cache.deserialize(serialized)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Saved Microsoft 365 sign-in data cannot be opened; reconnect the account.") from exc
    return cache


def _save_token_cache(cache, runtime_root: Path, session_secret: str) -> None:
    if not cache.has_state_changed:
        return
    path = _token_cache_path(runtime_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _token_cache_fernet(session_secret).encrypt(cache.serialize().encode("utf-8"))
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".m365-cache-", dir=path.parent)
        temporary = Path(name)
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
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def complete_microsoft365_auth_flow(
    flow: dict,
    auth_response: dict[str, str],
    runtime_root: Path,
    session_secret: str,
) -> str:
    with _TOKEN_CACHE_LOCK:
        try:
            import msal
        except ImportError as exc:
            raise ValueError("The Microsoft identity library is not installed.") from exc
        cache = msal.SerializableTokenCache()
        app = _msal_application(token_cache=cache, runtime_root=runtime_root, session_secret=session_secret)
        try:
            result = app.acquire_token_by_auth_code_flow(flow, auth_response)
        except ValueError as exc:
            raise ValueError("Microsoft sign-in could not be verified. Start the connection again.") from exc
        if not isinstance(result, dict) or not result.get("access_token"):
            raise ValueError("Microsoft sign-in was not completed. Start the connection again.")
        claims = result.get("id_token_claims")
        accounts = cache.get_accounts()
        account = accounts[0] if accounts else {}
        username = str((claims or {}).get("preferred_username") or account.get("username") or "").strip()
        if not username:
            raise ValueError("Microsoft sign-in did not return an account name. Start the connection again.")
        configured_tenant = _values(runtime_root, session_secret)["M365_TENANT_ID"].lower()
        signed_in_tenant = str((claims or {}).get("tid") or "").lower()
        if signed_in_tenant and configured_tenant and signed_in_tenant != configured_tenant:
            raise ValueError("Sign in with an account from the configured company Microsoft 365 tenant.")
        _save_token_cache(cache, runtime_root, session_secret)
        return username


def microsoft365_connected_account(runtime_root: Path, session_secret: str) -> tuple[str | None, bool]:
    path = _token_cache_path(runtime_root)
    if not path.exists():
        return None, False
    try:
        with _TOKEN_CACHE_LOCK:
            cache = _load_token_cache(runtime_root, session_secret)
            accounts = cache.get_accounts()
            return (str(accounts[0].get("username") or "").strip() or None, False) if accounts else (None, True)
    except ValueError:
        return None, True


def microsoft365_connection_status(runtime_root: Path, session_secret: str) -> dict[str, object]:
    account, reconnect = microsoft365_connected_account(runtime_root, session_secret)
    legacy_ready = all(microsoft365_configuration_status().values())
    return {
        "connected": bool(account),
        "account": account,
        "reconnect_required": reconnect,
        "setup_ready": all(microsoft365_oauth_setup_status(runtime_root, session_secret).values()),
        "legacy_service_ready": legacy_ready,
    }


def disconnect_microsoft365(runtime_root: Path) -> None:
    with _TOKEN_CACHE_LOCK:
        _token_cache_path(runtime_root).unlink(missing_ok=True)


def _delegated_access_token(runtime_root: Path, session_secret: str) -> tuple[str, str]:
    with _TOKEN_CACHE_LOCK:
        cache = _load_token_cache(runtime_root, session_secret)
        accounts = cache.get_accounts()
        if not accounts:
            raise ValueError("Reconnect the Microsoft 365 account from the dashboard.")
        app = _msal_application(token_cache=cache, runtime_root=runtime_root, session_secret=session_secret)
        result = app.acquire_token_silent([GRAPH_MAIL_SEND_SCOPE], account=accounts[0])
        token = result.get("access_token") if isinstance(result, dict) else None
        if not token:
            raise ValueError("Microsoft 365 needs you to reconnect the account from the dashboard.")
        _save_token_cache(cache, runtime_root, session_secret)
        return str(token), str(accounts[0].get("username") or "")


def _has_unencrypted_pem_private_key(path: str) -> bool:
    try:
        with Path(path).open("rb") as handle:
            contents = handle.read(8192)
    except OSError:
        return False
    begin = b"-" * 5 + b"BEGIN "
    end = b"-" * 5
    return any(begin + key_type + end in contents for key_type in PEM_PRIVATE_KEY_TYPES)


def microsoft365_configuration_status() -> dict[str, bool]:
    values = _values()
    key_path = values["M365_PRIVATE_KEY_PATH"] or values["M365_CERTIFICATE_PATH"]
    return {
        "tenant_id_configured": bool(values["M365_TENANT_ID"]),
        "client_id_configured": bool(values["M365_CLIENT_ID"]),
        "sender_address_configured": bool(values["M365_SENDER_ADDRESS"]),
        "certificate_configured": bool(values["M365_CERTIFICATE_PATH"]) and os.path.isfile(values["M365_CERTIFICATE_PATH"]),
        "private_key_configured": bool(key_path) and _has_unencrypted_pem_private_key(key_path),
        "certificate_thumbprint_configured": bool(values["M365_CERTIFICATE_THUMBPRINT"]),
        "scope_configured": (values["M365_GRAPH_SCOPE"] or GRAPH_SCOPE) == GRAPH_SCOPE,
    }


def _access_token() -> str:
    values = _values()
    required = ("M365_TENANT_ID", "M365_CLIENT_ID", "M365_SENDER_ADDRESS", "M365_CERTIFICATE_PATH", "M365_CERTIFICATE_THUMBPRINT")
    if any(not values[key] for key in required):
        raise ValueError("Microsoft 365 email configuration is incomplete.")
    if (values["M365_GRAPH_SCOPE"] or GRAPH_SCOPE) != GRAPH_SCOPE:
        raise ValueError("Microsoft Graph scope is invalid.")
    if not os.path.isfile(values["M365_CERTIFICATE_PATH"]):
        raise ValueError("Microsoft 365 certificate configuration is invalid.")
    if values["M365_PRIVATE_KEY_PATH"] and not os.path.isfile(values["M365_PRIVATE_KEY_PATH"]):
        raise ValueError("Microsoft 365 private-key configuration is invalid.")
    key_path = values["M365_PRIVATE_KEY_PATH"] or values["M365_CERTIFICATE_PATH"]
    if not _has_unencrypted_pem_private_key(key_path):
        raise ValueError("Microsoft 365 private-key configuration must contain an unencrypted PEM private key.")
    try:
        import msal
    except ImportError as exc:
        raise ValueError("The Microsoft identity library is not installed.") from exc
    with open(key_path, "r", encoding="utf-8") as handle:
        certificate = {"private_key": handle.read()}
    certificate["thumbprint"] = values["M365_CERTIFICATE_THUMBPRINT"]
    client = msal.ConfidentialClientApplication(
        values["M365_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{values['M365_TENANT_ID']}",
        client_credential=certificate,
    )
    result = client.acquire_token_for_client(scopes=[values["M365_GRAPH_SCOPE"] or GRAPH_SCOPE])
    token = result.get("access_token") if isinstance(result, dict) else None
    if not token:
        raise ValueError("Microsoft 365 authentication failed.")
    return str(token)


def send_email(
    employee: dict,
    subject: str,
    text_body: str,
    html_body: str,
    *,
    runtime_root: Path | None = None,
    session_secret: str | None = None,
) -> tuple[str, str]:
    address = str(employee.get("email", "")).strip()
    if not address or "@" not in address:
        return "failed", "Employee email address is missing or invalid."
    values = _values(runtime_root, session_secret)
    try:
        connected_account = None
        cache_path = _token_cache_path(runtime_root) if runtime_root is not None else None
        if cache_path is not None and cache_path.exists():
            if not session_secret:
                raise ValueError("Reconnect Microsoft 365 after the host session secret is configured.")
            token, connected_account = _delegated_access_token(runtime_root, session_secret)
            endpoint = "https://graph.microsoft.com/v1.0/me/sendMail"
        else:
            token = _access_token()
            endpoint = "https://graph.microsoft.com/v1.0/users/" + urllib.parse.quote(values["M365_SENDER_ADDRESS"], safe="") + "/sendMail"
        mime = EmailMessage()
        mime["Subject"] = subject
        if connected_account is None:
            mime["From"] = values["M365_SENDER_ADDRESS"]
            mime["Reply-To"] = values["M365_SENDER_ADDRESS"]
        mime["To"] = address
        mime.set_content(text_body)
        mime.add_alternative(html_body, subtype="html")
        payload = base64.b64encode(mime.as_bytes())
        request = urllib.request.Request(endpoint, data=payload, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "text/plain",
        }, method="POST")
        with urllib.request.urlopen(request, timeout=15) as response:
            return ("sent", "Email accepted by Microsoft 365.") if response.status == 202 else ("failed", f"Microsoft 365 rejected the request (HTTP {response.status}).")
    except urllib.error.HTTPError as exc:
        return ("pending", f"Microsoft 365 temporarily unavailable (HTTP {exc.code}).") if exc.code in {408, 429} or exc.code >= 500 else ("failed", f"Microsoft 365 rejected the request (HTTP {exc.code}).")
    except (TimeoutError, socket.timeout):
        return "pending", "Email delivery timed out; retry scheduled because delivery is uncertain."
    except urllib.error.URLError:
        return "pending", "Microsoft 365 could not be reached; retry scheduled."
    except ValueError as exc:
        return "failed", str(exc)
    except Exception:
        return "pending", "Email delivery could not be completed; retry scheduled."


def email_message(visitor: dict) -> tuple[str, str, str]:
    name = f"{visitor['first_name']} {visitor['last_name']}"
    company = visitor.get("company", "")
    location = visitor.get("kiosk_id", "Main Lobby")
    checked_in = visitor.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    text = f"Visitor waiting at Elite RF\n\nVisitor: {name}\nCompany: {company}\nLocation: {location}\nChecked in: {checked_in}\n\nPlease report to reception."
    esc = lambda value: html.escape(str(value), quote=True)
    html_body = f"<p><strong>Visitor waiting at Elite RF</strong></p><p><strong>Visitor:</strong> {esc(name)}<br><strong>Company:</strong> {esc(company)}<br><strong>Location:</strong> {esc(location)}<br><strong>Checked in:</strong> {esc(checked_in)}</p><p>Please report to reception.</p>"
    return "Visitor waiting at Elite RF", text, html_body

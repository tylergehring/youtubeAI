"""Browser OAuth and OS credential-store persistence, never plaintext token files."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from core.exceptions import ConfigError, InvalidInputError, StageError

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
SERVICE = "youtubeAI"
TOKEN_KEY = "google-oauth"


class CredentialStore:
    def __init__(self) -> None:
        try:
            import keyring
        except ImportError as exc:
            raise ConfigError('Install YouTube support: pip install -e ".[youtube]"') from exc
        backend = keyring.get_keyring()
        allowed = (
            "keyring.backends.Windows",
            "keyring.backends.macOS",
            "keyring.backends.SecretService",
            "keyring.backends.libsecret",
            "keyring.backends.kwallet",
        )
        if not type(backend).__module__.startswith(allowed):
            raise ConfigError(
                "A supported OS credential store is required; plaintext token storage is disabled."
            )
        self._keyring = keyring

    def get(self, name: str) -> str | None:
        header = self._keyring.get_password(SERVICE, name)
        if header is None:
            return None
        record = json.loads(header)
        chunks = [
            self._keyring.get_password(SERVICE, f"{name}:{record['version']}:{i}")
            for i in range(record["count"])
        ]
        if any(chunk is None for chunk in chunks):
            raise ConfigError("An OS credential-store entry is incomplete; authorize again.")
        return "".join(chunk for chunk in chunks if chunk is not None)

    def set(self, name: str, value: str) -> None:
        if self.get(name) == value:
            return
        old = self._keyring.get_password(SERVICE, name)
        version = uuid.uuid4().hex
        chunks = [value[i : i + 900] for i in range(0, len(value), 900)] or [""]
        for i, chunk in enumerate(chunks):
            self._keyring.set_password(SERVICE, f"{name}:{version}:{i}", chunk)
        self._keyring.set_password(
            SERVICE, name, json.dumps({"version": version, "count": len(chunks)})
        )
        if old:
            record = json.loads(old)
            for i in range(record["count"]):
                self._keyring.delete_password(SERVICE, f"{name}:{record['version']}:{i}")


def login(client_file: Path, secrets: CredentialStore) -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ConfigError('Install YouTube support: pip install -e ".[youtube]"') from exc
    if not client_file.is_file():
        raise InvalidInputError("OAuth client JSON file does not exist.")
    data = json.loads(client_file.read_text(encoding="utf-8-sig"))
    installed = data.get("installed")
    if not isinstance(installed, dict):
        raise InvalidInputError("Create a Desktop app OAuth client, not a Web client.")
    if (
        installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth"
        or installed.get("token_uri") != "https://oauth2.googleapis.com/token"
    ):
        raise InvalidInputError(
            "OAuth client does not use the expected Google authorization endpoints."
        )
    flow = InstalledAppFlow.from_client_config(data, SCOPES, autogenerate_code_verifier=True)
    credentials = flow.run_local_server(
        host="localhost",
        bind_addr="127.0.0.1",
        port=0,
        open_browser=True,
        authorization_prompt_message=(
            "Google authorization opened in your browser. Select the intended YouTube channel."
        ),
        success_message="YouTube authorization received. You can close this tab.",
        timeout_seconds=300,
        access_type="offline",
        prompt="consent",
    )
    if not credentials.refresh_token:
        raise ConfigError("Google did not grant offline access; authorize again with consent.")
    secrets.set(TOKEN_KEY, credentials.to_json())
    print("Authorization saved in the OS credential store. No token file was written.", flush=True)


class GoogleTransport:
    def __init__(self, secrets: CredentialStore) -> None:
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise ConfigError('Install YouTube support: pip install -e ".[youtube]"') from exc
        saved = secrets.get(TOKEN_KEY)
        if not saved:
            raise ConfigError("Authorize first with python -m pipeline.youtube login.")
        self._credentials = Credentials.from_authorized_user_info(  # type: ignore[no-untyped-call]
            json.loads(saved)
        )
        if not self._credentials.has_scopes(SCOPES):
            raise ConfigError("Stored authorization lacks the required scopes; authorize again.")
        self._session = AuthorizedSession(self._credentials)  # type: ignore[no-untyped-call]
        self._secrets = secrets

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        from google.auth.exceptions import GoogleAuthError
        from requests.exceptions import RequestException

        try:
            response = self._session.request(  # type: ignore[no-untyped-call]
                method, url, allow_redirects=False, **kwargs
            )
        except GoogleAuthError as exc:
            raise ConfigError(
                "Google authorization expired or was revoked. Run the login command again."
            ) from exc
        except RequestException as exc:
            raise StageError(
                "YouTube network request failed. The upload receipt/session was retained; "
                "re-run the same command to recover without creating a new upload."
            ) from exc
        self._secrets.set(TOKEN_KEY, self._credentials.to_json())
        return response

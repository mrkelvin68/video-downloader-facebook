from __future__ import annotations

import keyring

from app.services.logging_service import redactor

SERVICE_NAME = "video-downloader-facebook"
TOKEN_KEY = "apify_token"
META_TOKEN_KEY = "meta_graph_token"
AI_TOKEN_KEY = "ai_provider_api_key"


def _page_token_key(page_id: str) -> str:
    return f"meta_page_token:{page_id}"


def _legacy_page_token_key(project_id: str, page_id: str) -> str:
    return f"meta_page_token:{project_id}:{page_id}"


def get_apify_token() -> str:
    token = keyring.get_password(SERVICE_NAME, TOKEN_KEY) or ""
    if token:
        redactor.add(token)
    return token


def get_meta_token() -> str:
    token = keyring.get_password(SERVICE_NAME, META_TOKEN_KEY) or ""
    if token:
        redactor.add(token)
    return token


def set_meta_token(token: str) -> None:
    value = token.strip()
    if not value:
        raise ValueError("Meta Graph API token is required")
    redactor.add(value)
    keyring.set_password(SERVICE_NAME, META_TOKEN_KEY, value)


def delete_meta_token() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, META_TOKEN_KEY)
    except keyring.errors.PasswordDeleteError:
        pass


def get_page_token(page_id: str, legacy_page_id: str | None = None) -> str:
    """Look up a Page credential by Page ID, migrating a legacy project key lazily."""
    token = keyring.get_password(SERVICE_NAME, _page_token_key(legacy_page_id or page_id)) or ""
    if not token and legacy_page_id:
        token = keyring.get_password(SERVICE_NAME, _legacy_page_token_key(page_id, legacy_page_id)) or ""
        if token:
            keyring.set_password(SERVICE_NAME, _page_token_key(legacy_page_id), token)
    if token:
        redactor.add(token)
    return token


def set_page_token(page_id: str, token: str, legacy_token: str | None = None) -> None:
    """Store one credential per Page ID; accepts the legacy three-argument form."""
    if legacy_token is not None:
        page_id, token = token, legacy_token
    value = token.strip()
    if not value:
        raise ValueError("Page Access Token là bắt buộc")
    redactor.add(value)
    keyring.set_password(SERVICE_NAME, _page_token_key(page_id), value)


def delete_page_token(page_id: str, legacy_page_id: str | None = None) -> None:
    legacy_project_id, page_id = (page_id, legacy_page_id) if legacy_page_id else (None, page_id)
    try:
        keyring.delete_password(SERVICE_NAME, _page_token_key(page_id))
    except keyring.errors.PasswordDeleteError:
        pass
    if legacy_project_id:
        try:
            keyring.delete_password(SERVICE_NAME, _legacy_page_token_key(legacy_project_id, page_id))
        except keyring.errors.PasswordDeleteError:
            pass


def set_apify_token(token: str) -> None:
    value = token.strip()
    if not value:
        raise ValueError("Apify API token is required")
    redactor.add(value)
    keyring.set_password(SERVICE_NAME, TOKEN_KEY, value)


def delete_apify_token() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, TOKEN_KEY)
    except keyring.errors.PasswordDeleteError:
        pass

def get_ai_provider_key() -> str:
    value = keyring.get_password(SERVICE_NAME, AI_TOKEN_KEY) or ""
    if value: redactor.add(value)
    return value

def set_ai_provider_key(token: str) -> None:
    value = token.strip()
    if not value: raise ValueError("AI API key is required")
    redactor.add(value); keyring.set_password(SERVICE_NAME, AI_TOKEN_KEY, value)


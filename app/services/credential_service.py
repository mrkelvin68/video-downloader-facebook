from __future__ import annotations

import keyring

SERVICE_NAME = "video-downloader-facebook"
TOKEN_KEY = "apify_token"
META_TOKEN_KEY = "meta_graph_token"


def get_apify_token() -> str:
    return keyring.get_password(SERVICE_NAME, TOKEN_KEY) or ""


def get_meta_token() -> str:
    return keyring.get_password(SERVICE_NAME, META_TOKEN_KEY) or ""


def set_meta_token(token: str) -> None:
    value = token.strip()
    if not value:
        raise ValueError("Meta Graph API token is required")
    keyring.set_password(SERVICE_NAME, META_TOKEN_KEY, value)


def delete_meta_token() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, META_TOKEN_KEY)
    except keyring.errors.PasswordDeleteError:
        pass


def set_apify_token(token: str) -> None:
    value = token.strip()
    if not value:
        raise ValueError("Apify API token is required")
    keyring.set_password(SERVICE_NAME, TOKEN_KEY, value)


def delete_apify_token() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, TOKEN_KEY)
    except keyring.errors.PasswordDeleteError:
        pass

import unittest
from unittest.mock import Mock, patch

from app.services.facebook_connection_service import (
    FacebookConnectionError,
    FacebookConnectionService,
    MetaFacebookConnectionClient,
)


class FacebookTokenHotfixTests(unittest.TestCase):
    def test_page_token_uses_page_identity_when_accounts_discovery_is_denied(self):
        client = MetaFacebookConnectionClient("page-token")
        discovery_error = FacebookConnectionError("forbidden", category="permission")
        with patch.object(client, "_get", side_effect=[
            {"id": "page-1", "name": "Page 1"},
            discovery_error,
            {"id": "page-1", "name": "Page 1", "link": "https://facebook.com/page-1"},
            {"id": "page-1", "name": "Page 1", "tasks": ["CREATE_CONTENT"]},
        ]):
            pages = client.validate_and_list_pages()
        self.assertEqual(pages[0]["id"], "page-1")
        self.assertEqual(pages[0]["access_token"], "page-token")

    def test_page_identity_success_is_not_false_expiration_when_accounts_returns_190(self):
        client = MetaFacebookConnectionClient("page-token")
        accounts_error = FacebookConnectionError(
            "Access Token expired", category="expired", code="190", subcode="463"
        )
        optional_error = FacebookConnectionError("permission", category="permission", code="200")
        with patch.object(client, "_get", side_effect=[
            {"id": "page-1", "name": "Page 1"},
            accounts_error,
            {"id": "page-1", "name": "Page 1", "link": "https://facebook.com/page-1"},
            optional_error,
        ]):
            pages = client.validate_and_list_pages()
        self.assertEqual(pages[0]["id"], "page-1")
        self.assertEqual(pages[0]["permissions"], [])

    def test_user_token_uses_accounts_discovery(self):
        client = MetaFacebookConnectionClient("user-token")
        with patch.object(client, "_get", side_effect=[
            {"id": "user-1", "name": "User"},
            {"data": [{"id": "page-1", "name": "Page 1", "access_token": "page-token"}]},
        ]):
            pages = client.validate_and_list_pages()
        self.assertEqual(pages[0]["access_token"], "page-token")

    def test_failed_replacement_does_not_store_new_token(self):
        service = FacebookConnectionService(object(), lambda token: (_ for _ in ()).throw(
            FacebookConnectionError("Access Token đã hết hạn hoặc bị thu hồi.", category="expired")
        ))
        with patch("app.services.facebook_connection_service.set_meta_token") as save:
            with self.assertRaises(FacebookConnectionError):
                service.connect("project", "new-token")
        save.assert_not_called()

    def test_test_connection_uses_saved_page_token_not_global_user_token(self):
        publishing = type("Publishing", (), {"list_pages": lambda self, project_id: [{"page_id": "page-1"}]})()
        service = FacebookConnectionService(object(), lambda token: MetaFacebookConnectionClient(token))
        service.publishing = publishing
        client = Mock()
        client._get_page_identity.return_value = {"id": "page-1", "name": "Page 1"}
        with patch("app.services.facebook_connection_service.get_page_token", return_value="new-page-token"), \
             patch("app.services.facebook_connection_service.get_meta_token", return_value="old-user-token"), \
             patch.object(service, "client_factory", return_value=client) as factory:
            service.test_connection("project")
        factory.assert_called_once_with("new-page-token")


if __name__ == "__main__":
    unittest.main()

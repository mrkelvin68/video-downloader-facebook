import json
import unittest
from unittest.mock import patch

from app.services.meta_graph_provider import MetaGraphReelsProvider
from app.services.viral_reels_service import PageCandidate, ProviderAuthenticationError


class Response:
    status = 200
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.value).encode()


class MetaGraphTests(unittest.TestCase):
    def page(self):
        return PageCandidate("1301325469737954", "Gấu Béo Review", "https://facebook.com/gau", "Vietnam", "Vietnamese", "Review")

    @patch("app.services.meta_graph_provider.urlopen")
    def test_auth_page_and_reels(self, open_url):
        open_url.side_effect = [Response({"id": "u1", "name": "User"}), Response({"id": "1301325469737954", "name": "Gấu Béo Review"}), Response({"data": [{"id": "r1", "permalink_url": "https://facebook.com/reel/1", "created_time": "2026-01-01T00:00:00Z"}]})]
        provider = MetaGraphReelsProvider("secret", page_id="1301325469737954")
        self.assertEqual(provider.test_connection()["page"]["id"], "1301325469737954")
        self.assertEqual(list(provider.collect_reels(self.page()))[0].reel_id, "r1")
        request = open_url.call_args_list[0].args[0]
        self.assertNotIn("access_token", request.full_url)
        self.assertEqual(request.headers["Authorization"], "Bearer secret")

    @patch("app.services.meta_graph_provider.urlopen")
    def test_invalid_token(self, open_url):
        from urllib.error import HTTPError
        open_url.side_effect = HTTPError("", 401, "", {}, None)
        with self.assertRaises(ProviderAuthenticationError):
            MetaGraphReelsProvider("bad").test_connection()


if __name__ == "__main__": unittest.main()

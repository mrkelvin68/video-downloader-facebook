import json
import unittest
from unittest.mock import patch

from app.services.meta_graph_provider import MetaGraphReelsProvider
from app.services.viral_reels_service import DiscoveryRequest, PageCandidate, ProviderAuthenticationError


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
        reel = list(provider.collect_reels(self.page()))[0]
        self.assertEqual(reel.reel_id, "r1")
        self.assertIsNone(reel.views)
        self.assertIsNone(reel.likes)
        request = open_url.call_args_list[0].args[0]
        self.assertNotIn("access_token", request.full_url)
        self.assertEqual(request.headers["Authorization"], "Bearer secret")

    @patch("app.services.meta_graph_provider.urlopen")
    def test_invalid_token(self, open_url):
        from urllib.error import HTTPError
        open_url.side_effect = HTTPError("", 401, "", {}, None)
        with self.assertRaises(ProviderAuthenticationError):
            MetaGraphReelsProvider("bad").test_connection()

    @patch("app.services.meta_graph_provider.urlopen")
    def test_find_pages_uses_graph_search_and_pagination(self, open_url):
        open_url.side_effect = [
            Response({
                "data": [{"id": "p1", "name": "Page One", "link": "https://facebook.com/page-one"}],
                "paging": {"next": "https://graph.facebook.com/v26.0/pages/search?after=next"},
            }),
            Response({
                "data": [
                    {"id": "p1", "name": "Page One", "link": "https://facebook.com/page-one"},
                    {"id": "p2", "name": "Page Two", "link": "https://facebook.com/page-two"},
                ],
            }),
        ]
        provider = MetaGraphReelsProvider("secret")
        pages = list(provider.discover_pages(DiscoveryRequest(keywords=("food",), maximum_pages=2)))
        self.assertEqual([page.page_id for page in pages], ["p1", "p2"])
        self.assertTrue(open_url.call_args_list[0].args[0].full_url.startswith("https://graph.facebook.com/v26.0/pages/search?"))
        self.assertEqual(open_url.call_args_list[1].args[0].full_url, "https://graph.facebook.com/v26.0/pages/search?after=next")
        self.assertEqual(pages[0].source, "Meta Graph API")

    @patch("app.services.meta_graph_provider.urlopen")
    def test_scan_uses_videos_pagination_and_keeps_only_reels(self, open_url):
        open_url.side_effect = [
            Response({
                "data": [
                    {"id": "video", "permalink_url": "https://facebook.com/video/1"},
                    {"id": "r1", "permalink_url": "https://facebook.com/reel/1", "views": 4},
                ],
                "paging": {"next": "https://graph.facebook.com/v26.0/1301325469737954/videos?after=next"},
            }),
            Response({"data": [{"id": "r2", "permalink_url": "https://facebook.com/reels/2", "views": 5}]}),
        ]
        provider = MetaGraphReelsProvider("secret")
        reels = list(provider.collect_reels(self.page()))
        self.assertEqual([item.reel_id for item in reels], ["r1", "r2"])
        self.assertTrue(open_url.call_args_list[1].args[0].full_url.startswith("https://graph.facebook.com/v26.0/1301325469737954/videos?after=next&"))


if __name__ == "__main__": unittest.main()

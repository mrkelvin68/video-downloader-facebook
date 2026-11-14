import json
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings
from app.services.ai_comment_service import AICommentError, generate_comment_drafts
from app.services.logging_service import SETTINGS_APPLICATION, SETTINGS_ORGANIZATION

class _Response:
    def __init__(self, data): self.data=data
    def read(self): return json.dumps(self.data).encode()
    def __enter__(self): return self
    def __exit__(self, *_): return False

class AICommentTests(unittest.TestCase):
    def setUp(self):
        self.settings=QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION);self.settings.setValue("ai/base_url","http://mock/v1");self.settings.setValue("ai/model","mock-model");self.settings.setValue("ai/provider","OpenAI-compatible")
    @patch("app.services.ai_comment_service.get_ai_provider_key",return_value="key")
    @patch("app.services.ai_comment_service.urlopen")
    def test_valid_drafts_preserve_url_mapping(self, request, _key):
        request.return_value=_Response({"choices":[{"message":{"content":json.dumps({"drafts":[{"text":"Xin chào","url":"https://a.test"},{"text":"Xem thêm","url":"https://b.test"}]})}}]})
        drafts=generate_comment_drafts("Ngữ cảnh",["https://a.test","https://b.test"],2,"Tự nhiên")
        self.assertEqual([draft["url"] for draft in drafts],["https://a.test","https://b.test"])
    @patch("app.services.ai_comment_service.get_ai_provider_key",return_value="")
    def test_openai_requires_key(self, _key):
        with self.assertRaisesRegex(AICommentError,"API key"): generate_comment_drafts("x",[],3,"Tự nhiên")
    @patch("app.services.ai_comment_service.get_ai_provider_key",return_value="key")
    @patch("app.services.ai_comment_service.urlopen")
    def test_rejects_unauthorized_url(self, request, _key):
        request.return_value=_Response({"choices":[{"message":{"content":json.dumps({"drafts":[{"text":"x","url":"https://bad.test"}]})}}]})
        with self.assertRaises(AICommentError): generate_comment_drafts("x",["https://ok.test"],1,"Tự nhiên")

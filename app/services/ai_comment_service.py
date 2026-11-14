"""Configured OpenAI-compatible draft generation; never publishes to Facebook."""
from __future__ import annotations
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from PySide6.QtCore import QSettings
from app.services.credential_service import get_ai_provider_key
from app.services.logging_service import SETTINGS_APPLICATION, SETTINGS_ORGANIZATION

class AICommentError(ValueError): pass

def generate_comment_drafts(context: str, urls: list[str], count: int, tone: str) -> list[dict]:
    settings=QSettings(SETTINGS_ORGANIZATION, SETTINGS_APPLICATION); base=str(settings.value("ai/base_url", "", type=str)).rstrip("/"); model=str(settings.value("ai/model", "", type=str)); key=get_ai_provider_key(); provider=str(settings.value("ai/provider", "OpenAI-compatible", type=str))
    if not context.strip(): raise AICommentError("Hãy nhập ngữ cảnh Reel/sản phẩm.")
    if not base or not model: raise AICommentError("Chưa cấu hình AI. Vui lòng vào Cài đặt → Cấu hình AI.")
    if provider == "OpenAI-compatible" and not key: raise AICommentError("Chưa cấu hình AI API key.")
    if len(urls)>count: raise AICommentError("Số URL không được lớn hơn số mẫu cần tạo.")
    prompt={"context":context,"affiliate_urls":urls,"count":count,"tone":tone,"rules":"Return JSON object {drafts:[{text,url}]}. Every url must be one supplied URL or empty. Be truthful; do not invent claims."}
    headers={"Content-Type":"application/json"};
    if key: headers["Authorization"]=f"Bearer {key}"
    try:
        payload={"model":model,"messages":[{"role":"user","content":json.dumps(prompt,ensure_ascii=False)}],"response_format":{"type":"json_object"}}
        with urlopen(Request(base+"/chat/completions",data=json.dumps(payload).encode(),headers=headers),timeout=int(settings.value("ai/timeout",30))) as response: raw=json.loads(response.read().decode())
        data=json.loads(raw["choices"][0]["message"]["content"]); drafts=data.get("drafts",[])
    except (HTTPError,URLError,TimeoutError,KeyError,json.JSONDecodeError) as exc: raise AICommentError("Không thể tạo mẫu AI. Kiểm tra gateway, model và API key.") from exc
    valid=[]
    for draft in drafts:
        text=str(draft.get("text") or "").strip(); url=str(draft.get("url") or "")
        if text and (not url or url in urls): valid.append({"id":str(len(valid)),"text":text,"url":url,"context":context})
    if not valid: raise AICommentError("AI không trả về mẫu hợp lệ.")
    return valid[:count]

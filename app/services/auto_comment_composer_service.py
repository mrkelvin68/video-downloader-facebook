"""Local Comment Composer combining an approved text template and saved Affiliate URL (Phase 3C1)."""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from app.services.auto_comment_affiliate_link_service import (
    AffiliateLinkError,
    AutoCommentAffiliateLinkService,
    validate_affiliate_url,
)
from app.services.auto_comment_service import AutoCommentService, AutoCommentValidationError
from app.services.auto_comment_template_service import (
    AutoCommentTemplateService,
    TemplateError,
)


class CommentComposerError(ValueError):
    pass


@dataclass
class ComposedCommentDraft:
    project_id: str
    page_id: str
    template_id: str
    template_name: str
    template_body: str
    affiliate_link_id: str | None
    affiliate_link_name: str | None
    affiliate_url: str | None
    composed_text: str
    edited_text: str
    has_placeholder: bool
    warnings: list[str] = field(default_factory=list)


def validate_template_placeholders(body_text: str) -> bool:
    """Validate placeholders in template body; only {affiliate_url} is permitted."""
    text = str(body_text or "")
    if not text.strip():
        raise CommentComposerError("Nội dung mẫu không được để trống.")
    placeholders = re.findall(r"{([^{}]*)}", text)
    if (
        any(item != "affiliate_url" for item in placeholders)
        or text.count("{") != len(placeholders)
        or text.count("}") != len(placeholders)
    ):
        raise CommentComposerError("Chỉ hỗ trợ placeholder {affiliate_url}.")
    return bool(placeholders)


def render_template_with_affiliate_url(body_text: str, affiliate_url: str | None = None) -> str:
    """Replace literal {affiliate_url} with the exact URL; never invent text or alter tracking parameters."""
    has_placeholder = validate_template_placeholders(body_text)
    text = str(body_text)
    if has_placeholder:
        if not affiliate_url or not str(affiliate_url).strip():
            raise CommentComposerError(
                "Mẫu đang chọn có placeholder {affiliate_url}; hãy chọn một Affiliate URL."
            )
        exact_url = validate_affiliate_url(str(affiliate_url))
        return text.replace("{affiliate_url}", exact_url)
    return text


def evaluate_draft_warnings(
    draft_text: str,
    existing_comments: list[str] | None = None,
) -> list[str]:
    """Evaluate local promotional safety warnings using the existing Safety Guard and URL checks."""
    text = str(draft_text or "")
    if not text.strip():
        raise CommentComposerError("Nội dung bản nháp không được để trống.")
    placeholders = re.findall(r"{([^{}]*)}", text)
    if placeholders or "{" in text or "}" in text:
        raise CommentComposerError("Bản nháp vẫn còn placeholder chưa hợp lệ hoặc chưa được thay thế.")

    warnings: list[str] = []
    found_urls = re.findall(r"https?://[^\s]+", text)
    lower_urls = [u.casefold() for u in found_urls]
    if len(lower_urls) > len(set(lower_urls)):
        warnings.append("URL quảng bá bị lặp lại nhiều lần trong cùng một comment.")

    prior_rows = [
        {"id": f"existing-{idx}", "comment_text": comment}
        for idx, comment in enumerate(existing_comments or [])
        if str(comment or "").strip()
    ]
    review = AutoCommentService.campaign_safety_review(
        [*prior_rows, {"id": "draft", "comment_text": text}]
    )
    for reason in review.get("draft", []):
        if reason not in warnings:
            warnings.append(reason)
    return warnings


class AutoCommentComposerService:

    def __init__(self, database):
        self.database = database
        self.template_service = AutoCommentTemplateService(database)
        self.link_service = AutoCommentAffiliateLinkService(database)

    @staticmethod
    def _validate_scope(project_id: str, page_id: str) -> tuple[str, str]:
        clean_project = str(project_id or "").strip()
        clean_page = str(page_id or "").strip()
        if not clean_project or not clean_page:
            raise CommentComposerError("Hãy chọn Fanpage trước khi tạo comment từ mẫu.")
        return clean_project, clean_page

    def list_enabled_templates(self, project_id: str, page_id: str) -> list[dict]:
        clean_project, clean_page = self._validate_scope(project_id, page_id)
        return [
            row
            for row in self.template_service.list_templates(clean_project, clean_page)
            if int(row.get("enabled") or 0) == 1
        ]

    def list_enabled_links(self, project_id: str, page_id: str) -> list[dict]:
        clean_project, clean_page = self._validate_scope(project_id, page_id)
        try:
            return self.link_service.list_links(
                clean_project, clean_page, enabled_only=True
            )
        except AffiliateLinkError as exc:
            raise CommentComposerError(str(exc)) from exc

    def compose_draft(
        self,
        project_id: str,
        page_id: str,
        template_id: str,
        affiliate_link_id: str | None = None,
        existing_comments: list[str] | None = None,
    ) -> ComposedCommentDraft:
        clean_project, clean_page = self._validate_scope(project_id, page_id)
        clean_template_id = str(template_id or "").strip()
        if not clean_template_id:
            raise CommentComposerError("Hãy chọn một mẫu comment đang bật.")

        try:
            template = self.template_service.get_template(
                clean_project, clean_page, clean_template_id
            )
        except TemplateError as exc:
            raise CommentComposerError(
                "Mẫu comment không tồn tại hoặc không thuộc Fanpage đang chọn."
            ) from exc

        if int(template.get("enabled") or 0) != 1:
            raise CommentComposerError("Mẫu comment đã bị tắt; hãy chọn một mẫu đang bật.")

        body_text = str(template.get("body_text") or "")
        has_placeholder = validate_template_placeholders(body_text)

        clean_link_id = str(affiliate_link_id or "").strip() or None
        link: dict | None = None
        if clean_link_id is not None:
            try:
                link = self.link_service.get_link(
                    clean_project, clean_page, clean_link_id
                )
            except AffiliateLinkError as exc:
                raise CommentComposerError(
                    "Affiliate URL không tồn tại hoặc không thuộc Fanpage đang chọn."
                ) from exc
            if int(link.get("enabled") or 0) != 1:
                raise CommentComposerError(
                    "Affiliate URL đã bị tắt; hãy chọn một link đang bật."
                )
            try:
                validate_affiliate_url(str(link.get("url") or ""))
            except AffiliateLinkError as exc:
                raise CommentComposerError(str(exc)) from exc

        if has_placeholder and link is None:
            raise CommentComposerError(
                "Mẫu đang chọn có placeholder {affiliate_url}; hãy chọn một Affiliate URL."
            )

        exact_url = str(link["url"]) if link is not None else None
        composed_text = render_template_with_affiliate_url(body_text, exact_url)
        warnings = evaluate_draft_warnings(composed_text, existing_comments)
        if not has_placeholder and link is not None:
            warnings.append(
                "Mẫu không có placeholder {affiliate_url}; hệ thống giữ nguyên văn bản mẫu và không tự động chèn link."
            )

        return ComposedCommentDraft(
            project_id=clean_project,
            page_id=clean_page,
            template_id=str(template["id"]),
            template_name=str(template["name"]),
            template_body=body_text,
            affiliate_link_id=str(link["id"]) if link is not None else None,
            affiliate_link_name=str(link["name"]) if link is not None else None,
            affiliate_url=exact_url,
            composed_text=composed_text,
            edited_text=composed_text,
            has_placeholder=has_placeholder,
            warnings=warnings,
        )

    def list_destination_libraries(self, project_id: str, page_id: str) -> list[dict]:
        clean_project, clean_page = self._validate_scope(project_id, page_id)
        return AutoCommentService(self.database).list_libraries(clean_project, clean_page)

    def save_approved_draft(self, draft: ComposedCommentDraft, library_id: str,
                            project_id: str, page_id: str) -> str:
        clean_project, clean_page = self._validate_scope(project_id, page_id)
        if (draft.project_id, draft.page_id) != (clean_project, clean_page):
            raise CommentComposerError("Bản nháp thuộc Fanpage khác; hãy tạo lại bản nháp.")
        # Re-read source records immediately before persistence so a stale draft cannot save.
        try:
            template = self.template_service.get_template(clean_project, clean_page, draft.template_id)
            if int(template.get("enabled") or 0) != 1:
                raise CommentComposerError("Mẫu comment đã bị tắt; không thể lưu bản nháp cũ.")
            if draft.affiliate_link_id:
                link = self.link_service.get_link(clean_project, clean_page, draft.affiliate_link_id)
                if int(link.get("enabled") or 0) != 1:
                    raise CommentComposerError("Affiliate URL đã bị tắt; không thể lưu bản nháp cũ.")
        except (TemplateError, AffiliateLinkError) as exc:
            raise CommentComposerError("Nguồn mẫu hoặc Affiliate URL không còn hợp lệ; hãy tạo lại bản nháp.") from exc
        try:
            evaluate_draft_warnings(draft.edited_text)
            return AutoCommentService(self.database).append_library_item(
                clean_project, clean_page, library_id, draft.edited_text,
                draft.template_id, draft.affiliate_link_id,
            )
        except AutoCommentValidationError as exc:
            raise CommentComposerError(str(exc)) from exc

"""Supported public-video URL providers for the shared download queue."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit


class VideoPlatform(StrEnum):
    FACEBOOK = "facebook"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"


@dataclass(frozen=True)
class VideoDownloadProvider:
    platform: VideoPlatform
    video_id: str
    normalized_url: str


class FacebookDownloadProvider(VideoDownloadProvider):
    pass


class YouTubeDownloadProvider(VideoDownloadProvider):
    pass


class InstagramDownloadProvider(VideoDownloadProvider):
    pass


def _safe_parts(url: str):
    try:
        parts = urlsplit(url.strip())
        if (
            parts.scheme.lower() not in {"http", "https"}
            or parts.username
            or parts.password
            or not parts.hostname
        ):
            return None
        _ = parts.port
        return parts
    except ValueError:
        return None


def _valid_video_id(video_id: str, *, exact_length: int | None = None) -> bool:
    if exact_length is not None and len(video_id) != exact_length:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_-]+", video_id))


def provider_for_url(url: str) -> VideoDownloadProvider | None:
    """Recognize and canonicalize a supported public video URL.

    Authentication is deliberately not inferred or supplied here.  The existing
    yt-dlp invocation receives only the public URL selected by the user.
    """
    parts = _safe_parts(url)
    if parts is None:
        return None
    host = (parts.hostname or "").lower().removeprefix("www.")
    path_parts = [part for part in parts.path.split("/") if part]

    if host == "youtu.be":
        if not path_parts or not _valid_video_id(path_parts[0], exact_length=11):
            return None
        video_id = path_parts[0]
        return YouTubeDownloadProvider(
            VideoPlatform.YOUTUBE,
            video_id,
            f"https://www.youtube.com/watch?v={video_id}",
        )
    if host == "youtube.com" or host.endswith(".youtube.com"):
        if path_parts[:1] == ["watch"]:
            video_id = (parse_qs(parts.query).get("v") or [""])[0]
        elif len(path_parts) >= 2 and path_parts[0] == "shorts":
            video_id = path_parts[1]
        else:
            video_id = ""
        if not _valid_video_id(video_id, exact_length=11):
            return None
        return YouTubeDownloadProvider(
            VideoPlatform.YOUTUBE,
            video_id,
            f"https://www.youtube.com/watch?v={video_id}",
        )
    if host == "instagram.com" or host.endswith(".instagram.com"):
        if len(path_parts) < 2 or path_parts[0] not in {"reel", "p"}:
            return None
        kind, video_id = path_parts[0], path_parts[1]
        if not _valid_video_id(video_id) or len(video_id) < 5:
            return None
        return InstagramDownloadProvider(
            VideoPlatform.INSTAGRAM,
            video_id,
            f"https://www.instagram.com/{kind}/{video_id}/",
        )
    if host == "fb.watch" or host == "facebook.com" or host.endswith(".facebook.com"):
        if not (parts.path.strip("/") or parts.query):
            return None
        path = parts.path.rstrip("/") or "/"
        if path_parts[:1] == ["reel"]:
            if len(path_parts) < 2 or not _valid_video_id(path_parts[1]):
                return None
            return FacebookDownloadProvider(
                VideoPlatform.FACEBOOK,
                path_parts[1],
                f"https://facebook.com/reel/{path_parts[1]}",
            )
        if path_parts[:1] == ["watch"]:
            video_id = (parse_qs(parts.query).get("v") or [""])[0]
            if not video_id:
                return None
            return FacebookDownloadProvider(
                VideoPlatform.FACEBOOK,
                video_id,
                f"https://facebook.com/watch/?v={video_id}",
            )
        return FacebookDownloadProvider(
            VideoPlatform.FACEBOOK,
            path_parts[-1] if path_parts else (parse_qs(parts.query).get("v") or [""])[0],
            urlunsplit((parts.scheme.lower(), host, path, "", "")),
        )
    return None


def platform_label(provider: VideoDownloadProvider | None) -> str:
    return {
        VideoPlatform.FACEBOOK: "Facebook",
        VideoPlatform.YOUTUBE: "YouTube",
        VideoPlatform.INSTAGRAM: "Instagram",
    }.get(provider.platform, "") if provider else ""

"""YouTube Data API의 공용 진입점이다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from common.config import YouTubeSettings


class YouTubeDataError(RuntimeError):
    """YouTube API 응답이 수집에 필요한 형태가 아닐 때 발생한다."""


@dataclass(frozen=True)
class YouTubeChannel:
    """YouTube에서 조회한 채널 메타데이터다."""

    youtube_channel_id: str
    name: str
    uploads_playlist_id: str
    subscriber_count: int | None


@dataclass(frozen=True)
class YouTubeVideo:
    """YouTube에서 조회한 영상 메타데이터다."""

    youtube_video_id: str
    youtube_channel_id: str
    title: str
    description: str | None
    published_at: datetime
    duration_seconds: int | None
    view_count: int | None
    has_paid_product_placement: bool | None


_VIDEO_DURATION_PATTERN = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


class YouTubeDataClient:
    """후속 Work Unit이 재사용할 YouTube Data API 클라이언트 경계다."""

    def __init__(self, settings: YouTubeSettings) -> None:
        from googleapiclient.discovery import build

        self._service: Any = build(
            "youtube",
            "v3",
            developerKey=settings.api_key,
            cache_discovery=False,
        )

    @property
    def service(self) -> Any:
        """필요할 때 원시 API 서비스를 제공한다."""

        return self._service

    def fetch_channels(self, channel_ids: Sequence[str]) -> tuple[YouTubeChannel, ...]:
        """채널 ID를 최대 50개씩 조회해 동기화용 메타데이터로 변환한다."""

        channels: list[YouTubeChannel] = []
        for start in range(0, len(channel_ids), 50):
            batch = channel_ids[start : start + 50]
            response = (
                self._service.channels()
                .list(
                    part="snippet,contentDetails,statistics",
                    id=",".join(batch),
                    maxResults=len(batch),
                )
                .execute()
            )
            items = response.get("items")
            if not isinstance(items, list):
                raise YouTubeDataError("channels.list response must contain an items array")
            channels.extend(_parse_channel(item) for item in items)

        return tuple(channels)

    def fetch_videos(self, video_ids: Sequence[str]) -> tuple[YouTubeVideo, ...]:
        """영상 ID를 최대 50개씩 조회해 동기화용 메타데이터로 변환한다."""

        videos: list[YouTubeVideo] = []
        for start in range(0, len(video_ids), 50):
            batch = video_ids[start : start + 50]
            response = (
                self._service.videos()
                .list(
                    part="snippet,contentDetails,statistics,paidProductPlacementDetails",
                    id=",".join(batch),
                    maxResults=len(batch),
                )
                .execute()
            )
            items = response.get("items")
            if not isinstance(items, list):
                raise YouTubeDataError("videos.list response must contain an items array")
            videos.extend(_parse_video(item) for item in items)

        return tuple(videos)


def _parse_channel(item: Any) -> YouTubeChannel:
    if not isinstance(item, dict):
        raise YouTubeDataError("channels.list returned an invalid channel item")

    channel_id = item.get("id")
    snippet = item.get("snippet")
    content_details = item.get("contentDetails")
    statistics = item.get("statistics")
    if (
        not isinstance(channel_id, str)
        or not isinstance(snippet, dict)
        or not isinstance(content_details, dict)
        or not isinstance(statistics, dict)
    ):
        raise YouTubeDataError("channels.list returned incomplete channel metadata")

    name = snippet.get("title")
    related_playlists = content_details.get("relatedPlaylists")
    uploads_playlist_id = (
        related_playlists.get("uploads") if isinstance(related_playlists, dict) else None
    )
    if not isinstance(name, str) or not isinstance(uploads_playlist_id, str):
        raise YouTubeDataError("channels.list returned incomplete channel metadata")

    subscriber_count = statistics.get("subscriberCount")
    if subscriber_count is not None:
        try:
            subscriber_count = int(subscriber_count)
        except (TypeError, ValueError) as error:
            raise YouTubeDataError("channels.list returned an invalid subscriber count") from error

    return YouTubeChannel(
        youtube_channel_id=channel_id,
        name=name,
        uploads_playlist_id=uploads_playlist_id,
        subscriber_count=subscriber_count,
    )


def _parse_video(item: Any) -> YouTubeVideo:
    if not isinstance(item, dict):
        raise YouTubeDataError("videos.list returned an invalid video item")

    video_id = item.get("id")
    snippet = item.get("snippet")
    if not isinstance(video_id, str) or not isinstance(snippet, dict):
        raise YouTubeDataError("videos.list returned incomplete video metadata")

    channel_id = snippet.get("channelId")
    title = snippet.get("title")
    published_at = _parse_published_at(snippet.get("publishedAt"))
    if not isinstance(channel_id, str) or not isinstance(title, str):
        raise YouTubeDataError("videos.list returned incomplete video metadata")

    description = _read_optional_string(snippet, "description")
    duration_seconds = _read_optional_duration(item.get("contentDetails"))
    view_count = _read_optional_count(item.get("statistics"), "viewCount")
    has_paid_product_placement = _read_optional_paid_product_placement(
        item.get("paidProductPlacementDetails")
    )

    return YouTubeVideo(
        youtube_video_id=video_id,
        youtube_channel_id=channel_id,
        title=title,
        description=description,
        published_at=published_at,
        duration_seconds=duration_seconds,
        view_count=view_count,
        has_paid_product_placement=has_paid_product_placement,
    )


def _parse_published_at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise YouTubeDataError("videos.list returned incomplete video metadata")

    normalized_value = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized_value)
    except ValueError as error:
        raise YouTubeDataError("videos.list returned an invalid publishedAt") from error
    if parsed.tzinfo is None:
        raise YouTubeDataError("videos.list returned an invalid publishedAt")

    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _read_optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise YouTubeDataError(f"videos.list returned an invalid {key}")
    return value


def _read_optional_duration(content_details: Any) -> int | None:
    if content_details is None:
        return None
    if not isinstance(content_details, dict):
        raise YouTubeDataError("videos.list returned an invalid contentDetails")

    value = content_details.get("duration")
    if value is None:
        return None
    if not isinstance(value, str):
        raise YouTubeDataError("videos.list returned an invalid duration")

    match = _VIDEO_DURATION_PATTERN.fullmatch(value)
    if match is None or not any(match.groupdict().values()):
        raise YouTubeDataError("videos.list returned an invalid duration")

    values = {name: int(amount or 0) for name, amount in match.groupdict().items()}
    return (
        values["days"] * 86_400
        + values["hours"] * 3_600
        + values["minutes"] * 60
        + values["seconds"]
    )


def _read_optional_count(statistics: Any, key: str) -> int | None:
    if statistics is None:
        return None
    if not isinstance(statistics, dict):
        raise YouTubeDataError("videos.list returned an invalid statistics")

    value = statistics.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise YouTubeDataError(f"videos.list returned an invalid {key}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise YouTubeDataError(f"videos.list returned an invalid {key}") from error
    if parsed < 0:
        raise YouTubeDataError(f"videos.list returned an invalid {key}")
    return parsed


def _read_optional_paid_product_placement(value: Any) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise YouTubeDataError(
            "videos.list returned an invalid paidProductPlacementDetails"
        )

    has_paid_product_placement = value.get("hasPaidProductPlacement")
    if has_paid_product_placement is None:
        return None
    if not isinstance(has_paid_product_placement, bool):
        raise YouTubeDataError(
            "videos.list returned an invalid hasPaidProductPlacement"
        )
    return has_paid_product_placement

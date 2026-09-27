"""YouTube Data API의 공용 진입점이다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from common.config import YouTubeSettings


class YouTubeDataError(RuntimeError):
    """YouTube API 응답이 채널 동기화에 필요한 형태가 아닐 때 발생한다."""


@dataclass(frozen=True)
class YouTubeChannel:
    """YouTube에서 조회한 채널 메타데이터다."""

    youtube_channel_id: str
    name: str
    uploads_playlist_id: str
    subscriber_count: int | None


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

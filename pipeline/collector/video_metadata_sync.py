"""선정한 YouTube 영상의 메타데이터를 videos 테이블에 동기화한다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from collector.video_seed_validation import prepare_video_seed_selection
from common.config import load_settings
from common.mysql import MySqlConnectionFactory
from common.youtube import YouTubeDataClient, YouTubeVideo


class VideoMetadataSyncError(RuntimeError):
    """영상 메타데이터가 등록된 채널과 안전하게 동기화될 수 없을 때 발생한다."""


class VideoLookupClient(Protocol):
    def fetch_videos(self, video_ids: Sequence[str]) -> tuple[YouTubeVideo, ...]:
        """주어진 영상 ID의 메타데이터를 조회한다."""


@dataclass(frozen=True)
class RegisteredChannel:
    id: int
    youtube_channel_id: str
    language_code: str


@dataclass(frozen=True)
class VideoMetadataSyncResult:
    synchronized_count: int
    unavailable_positions: tuple[int, ...] = field(default=(), repr=False)
    unregistered_channel_positions: tuple[int, ...] = field(default=(), repr=False)

    @property
    def unavailable_count(self) -> int:
        return len(self.unavailable_positions)

    @property
    def unregistered_channel_count(self) -> int:
        return len(self.unregistered_channel_positions)


_SELECT_REGISTERED_CHANNELS_SQL = """
SELECT id, youtube_channel_id, language_code
FROM channels
WHERE youtube_channel_id IN ({placeholders})
"""

_UPSERT_VIDEO_SQL = """
INSERT INTO videos (
    youtube_video_id,
    channel_id,
    title,
    description,
    published_at,
    language_code,
    duration_seconds,
    view_count,
    has_paid_product_placement,
    created_at,
    updated_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())
AS incoming
ON DUPLICATE KEY UPDATE
    channel_id = incoming.channel_id,
    title = incoming.title,
    description = incoming.description,
    published_at = incoming.published_at,
    language_code = incoming.language_code,
    duration_seconds = incoming.duration_seconds,
    view_count = incoming.view_count,
    has_paid_product_placement = incoming.has_paid_product_placement,
    updated_at = UTC_TIMESTAMP()
"""


class VideoMetadataSynchronizer:
    """YouTube 영상 메타데이터를 등록 채널에 연결해 videos에 upsert한다."""

    def __init__(
        self,
        youtube_client: VideoLookupClient,
        connection_factory: MySqlConnectionFactory,
    ) -> None:
        self._youtube_client = youtube_client
        self._connection_factory = connection_factory

    def synchronize(self, video_ids: Sequence[str]) -> VideoMetadataSyncResult:
        """반환된 정상 영상만 저장하고, 누락·미등록 채널 영상의 위치를 보고한다."""

        requested_ids = tuple(video_ids)
        fetched_videos = self._youtube_client.fetch_videos(requested_ids)
        videos_by_id = _index_returned_videos(fetched_videos)
        unavailable_positions = tuple(
            index
            for index, video_id in enumerate(requested_ids, 1)
            if video_id not in videos_by_id
        )
        selected_videos = tuple(
            videos_by_id[video_id]
            for video_id in requested_ids
            if video_id in videos_by_id
        )
        if not selected_videos:
            return VideoMetadataSyncResult(
                synchronized_count=0,
                unavailable_positions=unavailable_positions,
            )

        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            channels_by_youtube_id = _fetch_registered_channels(cursor, selected_videos)
            known_videos, unregistered_channel_positions = _filter_registered_videos(
                requested_ids, videos_by_id, channels_by_youtube_id
            )
            for video, channel in known_videos:
                cursor.execute(
                    _UPSERT_VIDEO_SQL,
                    (
                        video.youtube_video_id,
                        channel.id,
                        video.title,
                        video.description,
                        video.published_at,
                        channel.language_code,
                        video.duration_seconds,
                        video.view_count,
                        video.has_paid_product_placement,
                    ),
                )
            if known_videos:
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

        return VideoMetadataSyncResult(
            synchronized_count=len(known_videos),
            unavailable_positions=unavailable_positions,
            unregistered_channel_positions=unregistered_channel_positions,
        )


def _index_returned_videos(videos: Sequence[YouTubeVideo]) -> dict[str, YouTubeVideo]:
    videos_by_id: dict[str, YouTubeVideo] = {}
    for video in videos:
        if video.youtube_video_id in videos_by_id:
            raise VideoMetadataSyncError("YouTube API returned duplicate video metadata")
        videos_by_id[video.youtube_video_id] = video
    return videos_by_id


def _fetch_registered_channels(
    cursor: Any, videos: Sequence[YouTubeVideo]
) -> dict[str, RegisteredChannel]:
    channel_ids = tuple(dict.fromkeys(video.youtube_channel_id for video in videos))
    placeholders = ", ".join("%s" for _ in channel_ids)
    cursor.execute(
        _SELECT_REGISTERED_CHANNELS_SQL.format(placeholders=placeholders), channel_ids
    )

    channels_by_youtube_id: dict[str, RegisteredChannel] = {}
    for row in cursor.fetchall():
        channel = _parse_registered_channel(row)
        if channel.youtube_channel_id in channels_by_youtube_id:
            raise VideoMetadataSyncError("database returned duplicate registered channels")
        channels_by_youtube_id[channel.youtube_channel_id] = channel
    return channels_by_youtube_id


def _parse_registered_channel(row: Any) -> RegisteredChannel:
    if (
        not isinstance(row, tuple)
        or len(row) != 3
        or isinstance(row[0], bool)
        or not isinstance(row[0], int)
        or not isinstance(row[1], str)
        or not isinstance(row[2], str)
    ):
        raise VideoMetadataSyncError("database returned invalid registered channel metadata")

    return RegisteredChannel(
        id=row[0], youtube_channel_id=row[1], language_code=row[2]
    )


def _filter_registered_videos(
    requested_ids: Sequence[str],
    videos_by_id: dict[str, YouTubeVideo],
    channels_by_youtube_id: dict[str, RegisteredChannel],
) -> tuple[list[tuple[YouTubeVideo, RegisteredChannel]], tuple[int, ...]]:
    known_videos: list[tuple[YouTubeVideo, RegisteredChannel]] = []
    unregistered_channel_positions: list[int] = []
    for index, video_id in enumerate(requested_ids, 1):
        video = videos_by_id.get(video_id)
        if video is None:
            continue
        channel = channels_by_youtube_id.get(video.youtube_channel_id)
        if channel is None:
            unregistered_channel_positions.append(index)
            continue
        known_videos.append((video, channel))

    return known_videos, tuple(unregistered_channel_positions)


def main() -> None:
    """로컬 시드의 영상 메타데이터를 동기화하고 요약을 출력한다."""

    pipeline_root = Path(__file__).resolve().parents[1]
    repository_root = pipeline_root.parent
    settings = load_settings(repository_root, pipeline_root / "channels.local.json")
    selection = prepare_video_seed_selection(settings)
    synchronizer = VideoMetadataSynchronizer(
        YouTubeDataClient(settings.youtube),
        MySqlConnectionFactory(settings.mysql),
    )
    result = synchronizer.synchronize(selection.video_ids)
    print(f"Synchronized {result.synchronized_count} selected video(s).")
    if result.unavailable_count:
        positions = ", ".join(map(str, result.unavailable_positions))
        print(f"Video seed position(s) not returned by YouTube: {positions}.")
    if result.unregistered_channel_count:
        positions = ", ".join(map(str, result.unregistered_channel_positions))
        print(f"Video seed position(s) skipped for an unregistered channel: {positions}.")


if __name__ == "__main__":
    main()

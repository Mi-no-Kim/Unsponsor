"""비공개 채널 시드를 YouTube와 MySQL에 동기화한다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from common.config import ChannelSeed, load_settings
from common.mysql import MySqlConnectionFactory
from common.youtube import YouTubeChannel, YouTubeDataClient


class ChannelSeedSyncError(RuntimeError):
    """시드와 YouTube 응답이 동기화될 수 없을 때 발생한다."""


class ChannelLookupClient(Protocol):
    def fetch_channels(self, channel_ids: Sequence[str]) -> tuple[YouTubeChannel, ...]:
        """주어진 채널 ID의 메타데이터를 조회한다."""


@dataclass(frozen=True)
class ChannelSeedSyncResult:
    synchronized_count: int


_UPSERT_CHANNEL_SQL = """
INSERT INTO channels (
    youtube_channel_id,
    uploads_playlist_id,
    name,
    language_code,
    subscriber_count,
    created_at,
    updated_at
)
VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())
AS incoming
ON DUPLICATE KEY UPDATE
    uploads_playlist_id = incoming.uploads_playlist_id,
    name = incoming.name,
    language_code = incoming.language_code,
    subscriber_count = incoming.subscriber_count,
    updated_at = UTC_TIMESTAMP()
"""


class ChannelSeedSynchronizer:
    """YouTube 채널 메타데이터를 조회하고 channels 테이블에 upsert한다."""

    def __init__(
        self,
        youtube_client: ChannelLookupClient,
        connection_factory: MySqlConnectionFactory,
    ) -> None:
        self._youtube_client = youtube_client
        self._connection_factory = connection_factory

    def synchronize(self, seeds: Sequence[ChannelSeed]) -> ChannelSeedSyncResult:
        """모든 시드가 조회된 경우에만 하나의 트랜잭션으로 저장한다."""

        fetched_channels = self._youtube_client.fetch_channels(
            [seed.channel_id for seed in seeds]
        )
        channels_by_id = {
            channel.youtube_channel_id: channel for channel in fetched_channels
        }
        missing_indexes = [
            index
            for index, seed in enumerate(seeds, 1)
            if seed.channel_id not in channels_by_id
        ]
        if missing_indexes:
            positions = ", ".join(map(str, missing_indexes))
            raise ChannelSeedSyncError(
                "YouTube API did not return seed entries "
                f"{positions}; they may be deleted, private, or invalid."
            )

        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            for seed in seeds:
                channel = channels_by_id[seed.channel_id]
                cursor.execute(
                    _UPSERT_CHANNEL_SQL,
                    (
                        channel.youtube_channel_id,
                        channel.uploads_playlist_id,
                        channel.name,
                        seed.language_code,
                        channel.subscriber_count,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

        return ChannelSeedSyncResult(synchronized_count=len(seeds))


def main() -> None:
    """루트 설정과 비공개 시드로 채널 동기화를 실행한다."""

    pipeline_root = Path(__file__).resolve().parents[1]
    repository_root = pipeline_root.parent
    settings = load_settings(repository_root, pipeline_root / "channels.local.json")
    synchronizer = ChannelSeedSynchronizer(
        YouTubeDataClient(settings.youtube),
        MySqlConnectionFactory(settings.mysql),
    )
    result = synchronizer.synchronize(settings.channels)
    print(f"Synchronized {result.synchronized_count} channel seed(s).")


if __name__ == "__main__":
    main()

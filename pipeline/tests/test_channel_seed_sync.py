from __future__ import annotations

import unittest
from unittest.mock import Mock

from collector.channel_seed_sync import (
    ChannelSeedSyncError,
    ChannelSeedSynchronizer,
    _UPSERT_CHANNEL_SQL,
)
from common.config import ChannelSeed
from common.youtube import YouTubeChannel


class ChannelSeedSynchronizerTests(unittest.TestCase):
    def test_synchronize_upserts_each_seed_and_commits_once(self) -> None:
        youtube_client = Mock()
        youtube_client.fetch_channels.return_value = (
            _channel("channel-a", "First channel", "playlist-a", 100),
            _channel("channel-b", "Second channel", "playlist-b", None),
        )
        cursor = Mock()
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        synchronizer = ChannelSeedSynchronizer(youtube_client, factory)

        result = synchronizer.synchronize(
            (
                ChannelSeed(channel_id="channel-a", language_code="ko"),
                ChannelSeed(channel_id="channel-b", language_code="ko"),
            )
        )

        self.assertEqual(result.synchronized_count, 2)
        youtube_client.fetch_channels.assert_called_once_with(["channel-a", "channel-b"])
        self.assertEqual(
            cursor.execute.call_args_list,
            [
                ((
                    _UPSERT_CHANNEL_SQL,
                    ("channel-a", "playlist-a", "First channel", "ko", 100),
                ),),
                ((
                    _UPSERT_CHANNEL_SQL,
                    ("channel-b", "playlist-b", "Second channel", "ko", None),
                ),),
            ],
        )
        self.assertIn("ON DUPLICATE KEY UPDATE", _UPSERT_CHANNEL_SQL)
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_synchronize_does_not_open_a_database_connection_for_missing_channels(
        self,
    ) -> None:
        youtube_client = Mock()
        youtube_client.fetch_channels.return_value = (
            _channel("channel-a", "First channel", "playlist-a", 100),
        )
        factory = Mock()
        synchronizer = ChannelSeedSynchronizer(youtube_client, factory)

        with self.assertRaisesRegex(
            ChannelSeedSyncError, "seed entries 2"
        ) as error:
            synchronizer.synchronize(
                (
                    ChannelSeed(channel_id="channel-a", language_code="ko"),
                    ChannelSeed(channel_id="private-channel", language_code="ko"),
                )
            )

        self.assertNotIn("private-channel", str(error.exception))
        factory.connect.assert_not_called()

    def test_synchronize_rolls_back_when_an_upsert_fails(self) -> None:
        youtube_client = Mock()
        youtube_client.fetch_channels.return_value = (
            _channel("channel-a", "First channel", "playlist-a", 100),
        )
        cursor = Mock()
        cursor.execute.side_effect = RuntimeError("database unavailable")
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        synchronizer = ChannelSeedSynchronizer(youtube_client, factory)

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            synchronizer.synchronize((ChannelSeed("channel-a", "ko"),))

        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()


def _channel(
    channel_id: str,
    name: str,
    uploads_playlist_id: str,
    subscriber_count: int | None,
) -> YouTubeChannel:
    return YouTubeChannel(
        youtube_channel_id=channel_id,
        name=name,
        uploads_playlist_id=uploads_playlist_id,
        subscriber_count=subscriber_count,
    )

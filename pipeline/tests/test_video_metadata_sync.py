from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import Mock

from collector.video_metadata_sync import (
    VideoMetadataSyncError,
    VideoMetadataSynchronizer,
    _SELECT_REGISTERED_CHANNELS_SQL,
    _UPSERT_VIDEO_SQL,
)
from common.youtube import YouTubeVideo


class VideoMetadataSynchronizerTests(unittest.TestCase):
    def test_synchronize_upserts_returned_videos_with_registered_channel_languages(
        self,
    ) -> None:
        youtube_client = Mock()
        youtube_client.fetch_videos.return_value = (
            _video("video000001", "channel-a", title="First video"),
            _video("video000002", "channel-b", title="Second video"),
        )
        cursor = Mock()
        cursor.fetchall.return_value = [
            (101, "channel-a", "ko"),
            (102, "channel-b", "en"),
        ]
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        synchronizer = VideoMetadataSynchronizer(youtube_client, factory)

        result = synchronizer.synchronize(("video000001", "video000002"))

        self.assertEqual(result.synchronized_count, 2)
        self.assertEqual(result.unavailable_positions, ())
        self.assertEqual(result.unregistered_channel_positions, ())
        youtube_client.fetch_videos.assert_called_once_with(
            ("video000001", "video000002")
        )
        self.assertEqual(
            cursor.execute.call_args_list,
            [
                (
                    (
                        _select_registered_channels_sql(2),
                        ("channel-a", "channel-b"),
                    ),
                ),
                (
                    (
                        _UPSERT_VIDEO_SQL,
                        _upsert_parameters(
                            _video("video000001", "channel-a", title="First video"),
                            101,
                            "ko",
                        ),
                    ),
                ),
                (
                    (
                        _UPSERT_VIDEO_SQL,
                        _upsert_parameters(
                            _video("video000002", "channel-b", title="Second video"),
                            102,
                            "en",
                        ),
                    ),
                ),
            ],
        )
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_synchronize_reports_unavailable_video_without_blocking_a_returned_video(
        self,
    ) -> None:
        youtube_client = Mock()
        returned_video = _video("video000002", "channel-a")
        youtube_client.fetch_videos.return_value = (returned_video,)
        cursor = Mock()
        cursor.fetchall.return_value = [(101, "channel-a", "ko")]
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        synchronizer = VideoMetadataSynchronizer(youtube_client, factory)

        result = synchronizer.synchronize(("video000001", "video000002"))

        self.assertEqual(result.synchronized_count, 1)
        self.assertEqual(result.unavailable_positions, (1,))
        self.assertEqual(result.unavailable_count, 1)
        self.assertEqual(result.unregistered_channel_positions, ())
        cursor.execute.assert_any_call(
            _UPSERT_VIDEO_SQL, _upsert_parameters(returned_video, 101, "ko")
        )
        connection.commit.assert_called_once_with()

    def test_synchronize_skips_an_unregistered_channel_without_blocking_other_videos(
        self,
    ) -> None:
        youtube_client = Mock()
        skipped_video = _video("video000001", "missing-channel")
        saved_video = _video("video000002", "channel-a")
        youtube_client.fetch_videos.return_value = (skipped_video, saved_video)
        cursor = Mock()
        cursor.fetchall.return_value = [(101, "channel-a", "ko")]
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        synchronizer = VideoMetadataSynchronizer(youtube_client, factory)

        result = synchronizer.synchronize(("video000001", "video000002"))

        self.assertEqual(result.synchronized_count, 1)
        self.assertEqual(result.unavailable_positions, ())
        self.assertEqual(result.unregistered_channel_positions, (1,))
        self.assertEqual(result.unregistered_channel_count, 1)
        cursor.execute.assert_any_call(
            _UPSERT_VIDEO_SQL, _upsert_parameters(saved_video, 101, "ko")
        )
        connection.commit.assert_called_once_with()

    def test_synchronize_does_not_open_a_database_connection_when_no_videos_return(
        self,
    ) -> None:
        youtube_client = Mock()
        youtube_client.fetch_videos.return_value = ()
        factory = Mock()
        synchronizer = VideoMetadataSynchronizer(youtube_client, factory)

        result = synchronizer.synchronize(("video000001", "video000002"))

        self.assertEqual(result.synchronized_count, 0)
        self.assertEqual(result.unavailable_positions, (1, 2))
        self.assertEqual(result.unregistered_channel_positions, ())
        factory.connect.assert_not_called()

    def test_synchronize_rolls_back_when_an_upsert_fails(self) -> None:
        youtube_client = Mock()
        youtube_client.fetch_videos.return_value = (_video("video000001", "channel-a"),)
        cursor = Mock()
        cursor.fetchall.return_value = [(101, "channel-a", "ko")]
        cursor.execute.side_effect = [None, RuntimeError("database unavailable")]
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        synchronizer = VideoMetadataSynchronizer(youtube_client, factory)

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            synchronizer.synchronize(("video000001",))

        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_synchronize_rejects_duplicate_video_metadata_before_opening_database(
        self,
    ) -> None:
        youtube_client = Mock()
        youtube_client.fetch_videos.return_value = (
            _video("video000001", "channel-a"),
            _video("video000001", "channel-a"),
        )
        factory = Mock()
        synchronizer = VideoMetadataSynchronizer(youtube_client, factory)

        with self.assertRaisesRegex(VideoMetadataSyncError, "duplicate video metadata"):
            synchronizer.synchronize(("video000001",))

        factory.connect.assert_not_called()


def _select_registered_channels_sql(channel_count: int) -> str:
    return _SELECT_REGISTERED_CHANNELS_SQL.format(
        placeholders=", ".join("%s" for _ in range(channel_count))
    )


def _video(video_id: str, channel_id: str, *, title: str = "Test video") -> YouTubeVideo:
    return YouTubeVideo(
        youtube_video_id=video_id,
        youtube_channel_id=channel_id,
        title=title,
        description="Test description",
        published_at=datetime(2026, 1, 2, 3, 4, 5),
        duration_seconds=3723,
        view_count=4321,
        has_paid_product_placement=True,
    )


def _upsert_parameters(
    video: YouTubeVideo, channel_id: int, language_code: str
) -> tuple[object, ...]:
    return (
        video.youtube_video_id,
        channel_id,
        video.title,
        video.description,
        video.published_at,
        language_code,
        video.duration_seconds,
        video.view_count,
        video.has_paid_product_placement,
    )

from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import Mock, patch

from common.config import MySqlSettings, YouTubeSettings
from common.mysql import MySqlConnectionFactory
from common.youtube import YouTubeDataClient, YouTubeDataError


class ClientBoundaryTests(unittest.TestCase):
    @patch("googleapiclient.discovery.build")
    def test_youtube_client_creates_a_service_from_the_api_key(self, build: Mock) -> None:
        service = Mock()
        build.return_value = service

        client = YouTubeDataClient(YouTubeSettings(api_key="test-key"))

        self.assertIs(client.service, service)
        build.assert_called_once_with(
            "youtube", "v3", developerKey="test-key", cache_discovery=False
        )

    @patch("mysql.connector.connect")
    def test_mysql_factory_opens_a_connection_only_when_requested(
        self, connect: Mock
    ) -> None:
        factory = MySqlConnectionFactory(
            MySqlSettings(
                host="localhost",
                port=3306,
                database="unsponsor",
                user="unsponsor",
                password="test-password",
            )
        )

        factory.connect()

        connect.assert_called_once_with(
            host="localhost",
            port=3306,
            database="unsponsor",
            user="unsponsor",
            password="test-password",
            charset="utf8mb4",
        )

    @patch("googleapiclient.discovery.build")
    def test_youtube_client_fetches_channels_in_batches_of_fifty(self, build: Mock) -> None:
        service = Mock()
        request = Mock()
        request.execute.side_effect = [
            {"items": [_youtube_item("channel-0", "0")]},
            {"items": [_youtube_item("channel-50", "50")]},
        ]
        service.channels.return_value.list.return_value = request
        build.return_value = service
        client = YouTubeDataClient(YouTubeSettings(api_key="test-key"))
        channel_ids = [f"channel-{index}" for index in range(51)]

        channels = client.fetch_channels(channel_ids)

        self.assertEqual([channel.youtube_channel_id for channel in channels], ["channel-0", "channel-50"])
        self.assertEqual(service.channels.return_value.list.call_count, 2)
        service.channels.return_value.list.assert_any_call(
            part="snippet,contentDetails,statistics",
            id=",".join(channel_ids[:50]),
            maxResults=50,
        )
        service.channels.return_value.list.assert_any_call(
            part="snippet,contentDetails,statistics",
            id="channel-50",
            maxResults=1,
        )

    @patch("googleapiclient.discovery.build")
    def test_youtube_client_maps_channel_metadata(self, build: Mock) -> None:
        service = Mock()
        service.channels.return_value.list.return_value.execute.return_value = {
            "items": [_youtube_item("channel-a", "1234")]
        }
        build.return_value = service
        client = YouTubeDataClient(YouTubeSettings(api_key="test-key"))

        channels = client.fetch_channels(["channel-a"])

        self.assertEqual(
            channels[0].youtube_channel_id,
            "channel-a",
        )
        self.assertEqual(channels[0].name, "Test channel")
        self.assertEqual(channels[0].uploads_playlist_id, "uploads-a")
        self.assertEqual(channels[0].subscriber_count, 1234)

    @patch("googleapiclient.discovery.build")
    def test_youtube_client_rejects_incomplete_channel_metadata(self, build: Mock) -> None:
        service = Mock()
        service.channels.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "channel-a", "snippet": {}, "contentDetails": {}, "statistics": {}}]
        }
        build.return_value = service
        client = YouTubeDataClient(YouTubeSettings(api_key="test-key"))

        with self.assertRaisesRegex(YouTubeDataError, "incomplete channel metadata"):
            client.fetch_channels(["channel-a"])

    @patch("googleapiclient.discovery.build")
    def test_youtube_client_fetches_videos_in_batches_of_fifty(self, build: Mock) -> None:
        service = Mock()
        request = Mock()
        request.execute.side_effect = [
            {"items": [_youtube_video_item("video000000", "channel-a")]},
            {"items": [_youtube_video_item("video000050", "channel-b")]},
        ]
        service.videos.return_value.list.return_value = request
        build.return_value = service
        client = YouTubeDataClient(YouTubeSettings(api_key="test-key"))
        video_ids = [f"video{index:06d}" for index in range(51)]

        videos = client.fetch_videos(video_ids)

        self.assertEqual(
            [video.youtube_video_id for video in videos],
            ["video000000", "video000050"],
        )
        self.assertEqual(service.videos.return_value.list.call_count, 2)
        service.videos.return_value.list.assert_any_call(
            part="snippet,contentDetails,statistics,paidProductPlacementDetails",
            id=",".join(video_ids[:50]),
            maxResults=50,
        )
        service.videos.return_value.list.assert_any_call(
            part="snippet,contentDetails,statistics,paidProductPlacementDetails",
            id="video000050",
            maxResults=1,
        )

    @patch("googleapiclient.discovery.build")
    def test_youtube_client_maps_video_metadata_and_missing_optional_values(
        self, build: Mock
    ) -> None:
        service = Mock()
        service.videos.return_value.list.return_value.execute.return_value = {
            "items": [
                _youtube_video_item(
                    "video000001",
                    "channel-a",
                    description=None,
                    duration=None,
                    view_count=None,
                    paid_product_placement=None,
                    published_at="2026-01-02T03:04:05+09:00",
                )
            ]
        }
        build.return_value = service
        client = YouTubeDataClient(YouTubeSettings(api_key="test-key"))

        videos = client.fetch_videos(["video000001"])

        self.assertEqual(videos[0].youtube_channel_id, "channel-a")
        self.assertEqual(videos[0].title, "Test video")
        self.assertIsNone(videos[0].description)
        self.assertEqual(videos[0].published_at, datetime(2026, 1, 1, 18, 4, 5))
        self.assertIsNone(videos[0].duration_seconds)
        self.assertIsNone(videos[0].view_count)
        self.assertIsNone(videos[0].has_paid_product_placement)

    @patch("googleapiclient.discovery.build")
    def test_youtube_client_rejects_invalid_video_duration(self, build: Mock) -> None:
        service = Mock()
        service.videos.return_value.list.return_value.execute.return_value = {
            "items": [
                _youtube_video_item(
                    "video000001", "channel-a", duration="not-a-duration"
                )
            ]
        }
        build.return_value = service
        client = YouTubeDataClient(YouTubeSettings(api_key="test-key"))

        with self.assertRaisesRegex(YouTubeDataError, "invalid duration"):
            client.fetch_videos(["video000001"])


def _youtube_item(channel_id: str, subscriber_count: str) -> dict[str, object]:
    return {
        "id": channel_id,
        "snippet": {"title": "Test channel"},
        "contentDetails": {"relatedPlaylists": {"uploads": "uploads-a"}},
        "statistics": {"subscriberCount": subscriber_count},
    }


def _youtube_video_item(
    video_id: str,
    channel_id: str,
    *,
    description: str | None = "Test description",
    duration: str | None = "PT1H2M3S",
    view_count: str | None = "4321",
    paid_product_placement: bool | None = True,
    published_at: str = "2026-01-02T03:04:05Z",
) -> dict[str, object]:
    snippet: dict[str, object] = {
        "channelId": channel_id,
        "title": "Test video",
        "publishedAt": published_at,
    }
    if description is not None:
        snippet["description"] = description

    content_details: dict[str, object] = {}
    if duration is not None:
        content_details["duration"] = duration

    statistics: dict[str, object] = {}
    if view_count is not None:
        statistics["viewCount"] = view_count

    item: dict[str, object] = {
        "id": video_id,
        "snippet": snippet,
        "contentDetails": content_details,
        "statistics": statistics,
    }
    if paid_product_placement is not None:
        item["paidProductPlacementDetails"] = {
            "hasPaidProductPlacement": paid_product_placement
        }
    return item

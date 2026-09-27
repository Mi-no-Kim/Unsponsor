from __future__ import annotations

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


def _youtube_item(channel_id: str, subscriber_count: str) -> dict[str, object]:
    return {
        "id": channel_id,
        "snippet": {"title": "Test channel"},
        "contentDetails": {"relatedPlaylists": {"uploads": "uploads-a"}},
        "statistics": {"subscriberCount": subscriber_count},
    }

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from common.config import MySqlSettings, YouTubeSettings
from common.mysql import MySqlConnectionFactory
from common.youtube import YouTubeDataClient


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

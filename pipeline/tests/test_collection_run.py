from __future__ import annotations

import unittest
from unittest.mock import Mock

from collector.channel_seed_sync import ChannelSeedSyncResult
from collector.collection_run import run_collection
from collector.processing_queue_registration import ProcessingQueueRegistrationResult
from collector.video_metadata_sync import VideoMetadataSyncResult
from common.config import ChannelSeed, MySqlSettings, PipelineSettings, YouTubeSettings


class CollectionRunTests(unittest.TestCase):
    def test_run_collection_executes_each_stage_in_order_and_summarizes_results(self) -> None:
        settings = _settings()
        channel_synchronizer = Mock()
        channel_synchronizer.synchronize.return_value = ChannelSeedSyncResult(
            synchronized_count=3
        )
        video_synchronizer = Mock()
        video_synchronizer.synchronize.return_value = VideoMetadataSyncResult(
            synchronized_count=8,
            unavailable_positions=(2,),
            unregistered_channel_positions=(5,),
        )
        queue_registrar = Mock()
        queue_registrar.register.return_value = ProcessingQueueRegistrationResult(
            created_count=6,
            existing_count=2,
            missing_video_count=2,
        )

        result = run_collection(
            settings,
            channel_synchronizer,
            video_synchronizer,
            queue_registrar,
        )

        channel_synchronizer.synchronize.assert_called_once_with(settings.channels)
        video_synchronizer.synchronize.assert_called_once_with(settings.selected_video_ids)
        queue_registrar.register.assert_called_once_with(settings.selected_video_ids)
        self.assertEqual(result.created_count, 6)
        self.assertEqual(result.updated_count, 11)
        self.assertEqual(result.skipped_count, 4)
        self.assertEqual(result.failed_count, 0)


def _settings() -> PipelineSettings:
    return PipelineSettings(
        youtube=YouTubeSettings(api_key="test-key"),
        mysql=MySqlSettings(
            host="localhost",
            port=3306,
            database="unsponsor",
            user="unsponsor",
            password="test-password",
        ),
        channels=(
            ChannelSeed(channel_id="channel-a", language_code="ko"),
            ChannelSeed(channel_id="channel-b", language_code="ko"),
            ChannelSeed(channel_id="channel-c", language_code="ko"),
        ),
        selected_video_ids=tuple(f"video{index:06d}" for index in range(10)),
    )

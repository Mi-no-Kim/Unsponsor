from __future__ import annotations

import unittest

from collector.video_seed_validation import prepare_video_seed_selection
from common.config import (
    ChannelSeed,
    ConfigurationError,
    MySqlSettings,
    PipelineSettings,
    YouTubeSettings,
)


class VideoSeedValidationTests(unittest.TestCase):
    def test_dry_run_preserves_the_configured_video_id_order(self) -> None:
        video_ids = tuple(f"video{index:06d}" for index in range(10))

        selection = prepare_video_seed_selection(_settings(video_ids))

        self.assertEqual(selection.video_ids, video_ids)
        self.assertEqual(selection.count, 10)
        self.assertNotIn(video_ids[0], repr(selection))

    def test_dry_run_requires_a_private_video_selection(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "selected_video_ids is required"):
            prepare_video_seed_selection(_settings(None))


def _settings(selected_video_ids: tuple[str, ...] | None) -> PipelineSettings:
    return PipelineSettings(
        youtube=YouTubeSettings(api_key="test-key"),
        mysql=MySqlSettings(
            host="localhost",
            port=3306,
            database="unsponsor",
            user="unsponsor",
            password="test-password",
        ),
        channels=(ChannelSeed(channel_id="UCexample", language_code="ko"),),
        selected_video_ids=selected_video_ids,
    )

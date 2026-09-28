from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from common.config import ConfigurationError, load_settings


REQUIRED_ENVIRONMENT_KEYS = (
    "YOUTUBE_API_KEY",
    "MYSQL_HOST",
    "MYSQL_PORT",
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
)
CHANNEL_ID_A = "UC0123456789abcdefghijkl"
CHANNEL_ID_B = "UCabcdefghijkl0123456789"


class LoadSettingsTests(unittest.TestCase):
    repository_root = Path(__file__).resolve().parents[2]
    pipeline_directory = Path(__file__).resolve().parents[1]

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.test_repository_root = Path(self.temporary_directory.name)
        self.channel_seed_path = (
            self.test_repository_root / "pipeline" / "channels.local.json"
        )
        self.channel_seed_path.parent.mkdir()
        (self.test_repository_root / ".env").write_text(
            "\n".join(
                [
                    "YOUTUBE_API_KEY=super-secret-api-key",
                    "MYSQL_HOST=localhost",
                    "MYSQL_PORT=3306",
                    "MYSQL_DATABASE=unsponsor",
                    "MYSQL_USER=unsponsor",
                    "MYSQL_PASSWORD=super-secret-password",
                ]
            ),
            encoding="utf-8",
        )
        self.write_channels(
            {
                "channels": [
                    {"channel_id": CHANNEL_ID_A, "language_code": "ko"},
                    {"channel_id": CHANNEL_ID_B, "language_code": "en-US"},
                ]
            }
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_channels(self, contents: object) -> None:
        self.channel_seed_path.write_text(
            json.dumps(contents), encoding="utf-8"
        )

    def test_loads_valid_settings_without_exposing_secrets_in_repr(self) -> None:
        settings = load_settings(
            self.test_repository_root, self.channel_seed_path, environment={}
        )

        self.assertEqual(settings.youtube.api_key, "super-secret-api-key")
        self.assertEqual(settings.mysql.port, 3306)
        self.assertEqual(settings.channels[0].language_code, "ko")
        self.assertEqual(settings.channels[1].language_code, "en-US")
        self.assertIsNone(settings.selected_video_ids)
        self.assertNotIn("super-secret-api-key", repr(settings))
        self.assertNotIn("super-secret-password", repr(settings))

    def test_loads_a_private_selection_of_ten_to_fifty_video_ids(self) -> None:
        video_ids = _video_ids(50)
        self.write_channels(
            {
                "channels": [{"channel_id": CHANNEL_ID_A, "language_code": "ko"}],
                "selected_video_ids": video_ids,
            }
        )

        settings = load_settings(
            self.test_repository_root, self.channel_seed_path, environment={}
        )

        self.assertEqual(settings.selected_video_ids, tuple(video_ids))

    def test_rejects_video_selection_outside_the_ph1_range(self) -> None:
        for count in (9, 51):
            with self.subTest(count=count):
                self.write_channels(
                    {
                        "channels": [
                            {"channel_id": CHANNEL_ID_A, "language_code": "ko"}
                        ],
                        "selected_video_ids": _video_ids(count),
                    }
                )

                with self.assertRaisesRegex(ConfigurationError, "between 10 and 50"):
                    load_settings(
                        self.test_repository_root,
                        self.channel_seed_path,
                        environment={},
                    )

    def test_rejects_duplicate_video_ids_without_echoing_them(self) -> None:
        video_ids = _video_ids(10)
        video_ids[-1] = video_ids[0]
        self.write_channels(
            {
                "channels": [{"channel_id": CHANNEL_ID_A, "language_code": "ko"}],
                "selected_video_ids": video_ids,
            }
        )

        with self.assertRaisesRegex(
            ConfigurationError, "duplicate video IDs"
        ) as error:
            load_settings(
                self.test_repository_root, self.channel_seed_path, environment={}
            )

        self.assertNotIn(video_ids[0], str(error.exception))

    def test_process_environment_overrides_dotenv_values(self) -> None:
        settings = load_settings(
            self.test_repository_root,
            self.channel_seed_path,
            environment={"MYSQL_HOST": "database.internal"},
        )

        self.assertEqual(settings.mysql.host, "database.internal")

    def test_missing_required_settings_fails_before_external_clients_are_created(self) -> None:
        (self.test_repository_root / ".env").write_text(
            "YOUTUBE_API_KEY=super-secret-api-key\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(
            ConfigurationError, "MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD"
        ) as error:
            load_settings(
                self.test_repository_root, self.channel_seed_path, environment={}
            )

        self.assertNotIn("super-secret-api-key", str(error.exception))

    def test_rejects_channel_without_language_code(self) -> None:
        self.write_channels({"channels": [{"channel_id": CHANNEL_ID_A}]})

        with self.assertRaisesRegex(ConfigurationError, "Channel entry 1"):
            load_settings(
                self.test_repository_root, self.channel_seed_path, environment={}
            )

    def test_rejects_duplicate_channel_ids_without_echoing_them(self) -> None:
        self.write_channels(
            {
                "channels": [
                    {"channel_id": CHANNEL_ID_A, "language_code": "ko"},
                    {"channel_id": CHANNEL_ID_A, "language_code": "ko"},
                ]
            }
        )

        with self.assertRaisesRegex(ConfigurationError, "duplicate channel IDs") as error:
            load_settings(
                self.test_repository_root, self.channel_seed_path, environment={}
            )

        self.assertNotIn(CHANNEL_ID_A, str(error.exception))

    def test_rejects_malformed_channel_ids_without_echoing_them(self) -> None:
        for channel_id in (
            "not-a-youtube-channel",
            "XX0123456789abcdefghijk",
            "UC0123456789abcdefghij",
            "UC0123456789abcdefghijk!",
        ):
            with self.subTest(channel_id=channel_id):
                self.write_channels(
                    {"channels": [{"channel_id": channel_id, "language_code": "ko"}]}
                )

                with self.assertRaisesRegex(
                    ConfigurationError, "valid YouTube channel ID"
                ) as error:
                    load_settings(
                        self.test_repository_root,
                        self.channel_seed_path,
                        environment={},
                    )

                self.assertNotIn(channel_id, str(error.exception))

    def test_public_examples_show_the_required_configuration_shape(self) -> None:
        environment_example = (self.repository_root / ".env.example").read_text(
            encoding="utf-8"
        )
        channels_example = json.loads(
            (self.pipeline_directory / "channels.example.json").read_text(encoding="utf-8")
        )

        for key in REQUIRED_ENVIRONMENT_KEYS:
            self.assertIn(f"{key}=", environment_example)
        self.assertEqual(channels_example["channels"][0]["language_code"], "ko")
        self.assertRegex(
            channels_example["channels"][0]["channel_id"], r"^UC[A-Za-z0-9_-]{22}$"
        )
        self.assertEqual(len(channels_example["selected_video_ids"]), 10)
        self.assertTrue(
            all(
                video_id.startswith("<video-id-")
                for video_id in channels_example["selected_video_ids"]
            )
        )


def _video_ids(count: int) -> list[str]:
    return [f"video{index:06d}" for index in range(count)]

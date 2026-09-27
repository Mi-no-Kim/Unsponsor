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


class LoadSettingsTests(unittest.TestCase):
    pipeline_directory = Path(__file__).resolve().parents[1]

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_directory = Path(self.temporary_directory.name)
        (self.project_directory / ".env").write_text(
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
                    {"channel_id": "UCexample", "language_code": "ko"},
                    {"channel_id": "UCexampleTwo", "language_code": "en-US"},
                ]
            }
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_channels(self, contents: object) -> None:
        (self.project_directory / "channels.local.json").write_text(
            json.dumps(contents), encoding="utf-8"
        )

    def test_loads_valid_settings_without_exposing_secrets_in_repr(self) -> None:
        settings = load_settings(self.project_directory, environment={})

        self.assertEqual(settings.youtube.api_key, "super-secret-api-key")
        self.assertEqual(settings.mysql.port, 3306)
        self.assertEqual(settings.channels[0].language_code, "ko")
        self.assertEqual(settings.channels[1].language_code, "en-US")
        self.assertNotIn("super-secret-api-key", repr(settings))
        self.assertNotIn("super-secret-password", repr(settings))

    def test_process_environment_overrides_dotenv_values(self) -> None:
        settings = load_settings(
            self.project_directory,
            environment={"MYSQL_HOST": "database.internal"},
        )

        self.assertEqual(settings.mysql.host, "database.internal")

    def test_missing_required_settings_fails_before_external_clients_are_created(self) -> None:
        (self.project_directory / ".env").write_text(
            "YOUTUBE_API_KEY=super-secret-api-key\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(
            ConfigurationError, "MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE, MYSQL_USER, MYSQL_PASSWORD"
        ) as error:
            load_settings(self.project_directory, environment={})

        self.assertNotIn("super-secret-api-key", str(error.exception))

    def test_rejects_channel_without_language_code(self) -> None:
        self.write_channels({"channels": [{"channel_id": "UCexample"}]})

        with self.assertRaisesRegex(ConfigurationError, "Channel entry 1"):
            load_settings(self.project_directory, environment={})

    def test_rejects_duplicate_channel_ids_without_echoing_them(self) -> None:
        self.write_channels(
            {
                "channels": [
                    {"channel_id": "UCprivate", "language_code": "ko"},
                    {"channel_id": "UCprivate", "language_code": "ko"},
                ]
            }
        )

        with self.assertRaisesRegex(ConfigurationError, "duplicate channel IDs") as error:
            load_settings(self.project_directory, environment={})

        self.assertNotIn("UCprivate", str(error.exception))

    def test_public_examples_show_the_required_configuration_shape(self) -> None:
        environment_example = (self.pipeline_directory / ".env.example").read_text(
            encoding="utf-8"
        )
        channels_example = json.loads(
            (self.pipeline_directory / "channels.example.json").read_text(encoding="utf-8")
        )

        for key in REQUIRED_ENVIRONMENT_KEYS:
            self.assertIn(f"{key}=", environment_example)
        self.assertEqual(channels_example["channels"][0]["language_code"], "ko")

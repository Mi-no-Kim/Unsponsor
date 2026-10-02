from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.capture_transcript_fixtures import (
    TranscriptFixtureCaptureError,
    _capture_library_payload,
    _capture_ytdlp_payload,
    _read_selection,
    _reuse_library_payload,
    _write_dataset,
    main,
)
from transcript.fixture_dataset import TranscriptFixtureDataset
from transcript.model import TranscriptFailure
from youtube_transcript_api._errors import IpBlocked


class CaptureTranscriptFixturesTests(unittest.TestCase):
    def test_reads_one_private_video_id_without_copying_it_to_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selection_path = root / "selection.json"
            private_video_id = "fixture001a"
            selection_path.write_text(
                json.dumps({"video_id": private_video_id}), encoding="utf-8"
            )

            selected = _read_selection(selection_path)
            _write_dataset(root / "dataset", _library_payload(), _srv1_payload())
            manifest = (root / "dataset" / "manifest.json").read_text(
                encoding="utf-8"
            )

            self.assertEqual(selected, private_video_id)
            self.assertNotIn(private_video_id, manifest)
            self.assertTrue(TranscriptFixtureDataset.load(root / "dataset"))

    def test_rejects_invalid_or_multiple_selection_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            selection_path = Path(directory) / "selection.json"
            selection_path.write_text(
                json.dumps(
                    {"video_id": "fixture001a", "other": "fixture002b"}
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                TranscriptFixtureCaptureError, "selection_contract_invalid"
            ):
                _read_selection(selection_path)

    def test_refuses_to_overwrite_existing_fixture_or_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dataset"
            _write_dataset(target, _library_payload(), _srv1_payload())

            with self.assertRaisesRegex(
                TranscriptFixtureCaptureError, "fixture_dataset_already_exists"
            ):
                _write_dataset(target, _library_payload(), _srv1_payload())

    def test_reuses_a_verified_api_fixture_without_an_external_api_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "dataset"
            expected = _library_payload()
            _write_dataset(root, expected, _srv1_payload())

            actual = _reuse_library_payload(root)

            self.assertEqual(actual, expected)

    def test_library_fixture_is_compact_and_contains_only_normalizer_fields(
        self,
    ) -> None:
        session = Mock()
        api = Mock()
        fetched = _FetchedTranscript(
            [_Snippet(text="actual fixture", start=0.0, duration=1.25)]
        )
        api.fetch.return_value = fetched
        with (
            patch(
                "scripts.capture_transcript_fixtures.load_dataimpulse_proxy_settings",
                return_value=Mock(),
            ),
            patch(
                "scripts.capture_transcript_fixtures.create_dataimpulse_api",
                return_value=(session, api),
            ),
        ):
            payload = _capture_library_payload(Path("repository"), "fixture001a")

        self.assertEqual(
            payload,
            b'[{"text":"actual fixture","start":0.0,"duration":1.25}]',
        )
        api.fetch.assert_called_once_with(
            "fixture001a",
            languages=["ko", "en"],
            preserve_formatting=False,
        )
        session.close.assert_called_once()

    def test_library_rate_limit_is_reported_without_starting_ytdlp(self) -> None:
        session = Mock()
        api = Mock()
        api.fetch.side_effect = IpBlocked("private upstream detail")
        with (
            patch(
                "scripts.capture_transcript_fixtures.load_dataimpulse_proxy_settings",
                return_value=Mock(),
            ),
            patch(
                "scripts.capture_transcript_fixtures.create_dataimpulse_api",
                return_value=(session, api),
            ),
            self.assertRaisesRegex(TranscriptFixtureCaptureError, "rate_limited"),
        ):
            _capture_library_payload(Path("repository"), "fixture001a")

        session.close.assert_called_once()

    def test_ytdlp_rate_limit_stops_without_starting_provider_request(self) -> None:
        with (
            patch(
                "scripts.capture_transcript_fixtures._download_ytdlp_payload",
                return_value=(TranscriptFailure.RATE_LIMITED, None),
            ) as download,
            patch(
                "scripts.capture_transcript_fixtures.BgutilProviderSession"
            ) as provider,
        ):
            with self.assertRaisesRegex(
                TranscriptFixtureCaptureError, "rate_limited"
            ):
                _capture_ytdlp_payload(
                    "fixture001a",
                    provider_home=Path("provider"),
                    cookie_file=None,
                    error_logger=logging.getLogger("test"),
                )

        download.assert_called_once()
        provider.assert_not_called()

    def test_confirmation_is_required_before_external_request(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(
                [
                    "--selection-file",
                    "private-selection",
                    "--fixture-dir",
                    "private-fixture",
                ]
            )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--confirm-external-request is required", stderr.getvalue())
        self.assertNotIn("private-selection", stderr.getvalue())
        self.assertNotIn("private-fixture", stderr.getvalue())

    def test_unexpected_error_prints_only_a_safe_code(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "scripts.capture_transcript_fixtures._require_new_dataset",
                side_effect=RuntimeError("private upstream detail"),
            ),
            redirect_stdout(stdout),
            self.assertRaises(SystemExit) as raised,
        ):
            main(
                [
                    "--selection-file",
                    "private-selection",
                    "--fixture-dir",
                    "private-fixture",
                    "--confirm-external-request",
                ]
            )

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(
            stdout.getvalue().strip(),
            '{"status":"failed","error":"unexpected_error"}',
        )
        self.assertNotIn("private", stdout.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue())


@dataclass(frozen=True)
class _Snippet:
    text: str
    start: float
    duration: float


class _FetchedTranscript(list[_Snippet]):
    def to_raw_data(self) -> list[dict[str, object]]:
        return [
            {
                "text": snippet.text,
                "start": snippet.start,
                "duration": snippet.duration,
            }
            for snippet in self
        ]


def _library_payload() -> bytes:
    return json.dumps(
        [
            {"text": "api first", "start": 0.0, "duration": 1.0},
            {"text": "api second", "start": 1.0, "duration": 1.5},
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _srv1_payload() -> bytes:
    return (
        '<transcript><text start="0" dur="1">actual fixture</text></transcript>'
    ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()

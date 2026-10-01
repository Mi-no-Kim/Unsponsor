from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.capture_transcript_fixtures import (
    TranscriptFixtureCaptureError,
    _capture_ytdlp_payload,
    _read_selection,
    _write_dataset,
    main,
)
from transcript.fixture_dataset import TranscriptFixtureDataset
from transcript.model import TranscriptFailure


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
            _write_dataset(root / "dataset", _json3_payload())
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
            _write_dataset(target, _json3_payload())

            with self.assertRaisesRegex(
                TranscriptFixtureCaptureError, "fixture_dataset_already_exists"
            ):
                _write_dataset(target, _json3_payload())

    def test_rate_limit_stops_without_starting_provider_request(self) -> None:
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
                "scripts.capture_transcript_fixtures._read_selection",
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


def _json3_payload() -> bytes:
    return json.dumps(
        {
            "events": [
                {
                    "tStartMs": 0,
                    "dDurationMs": 1000,
                    "segs": [{"utf8": "actual fixture"}],
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()

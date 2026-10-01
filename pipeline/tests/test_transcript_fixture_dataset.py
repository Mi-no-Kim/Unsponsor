from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from transcript.fixture_dataset import (
    TranscriptFixtureDataset,
    TranscriptFixtureDatasetError,
)


class TranscriptFixtureDatasetTests(unittest.TestCase):
    def test_loads_actual_json3_shape_through_production_normalizer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root)

            dataset = TranscriptFixtureDataset.load(root)

            self.assertEqual(dataset.ytdlp_success.text, "yt-dlp first\nyt-dlp second")
            self.assertEqual(len(dataset.ytdlp_success.segments), 2)

    def test_rejects_payload_changed_after_capture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root)
            (root / "yt-dlp-success.json3").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(
                TranscriptFixtureDatasetError, "checksum does not match"
            ):
                TranscriptFixtureDataset.load(root)

    def test_rejects_payload_path_outside_fixture_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root)
            manifest = _read_manifest(root)
            manifest["fixture"]["file"] = "../outside.json3"
            _write_manifest(root, manifest)

            with self.assertRaisesRegex(
                TranscriptFixtureDatasetError, "metadata is invalid"
            ):
                TranscriptFixtureDataset.load(root)

    def test_rejects_unexpected_manifest_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root)
            manifest = _read_manifest(root)
            manifest["video_id"] = "private0001"
            _write_manifest(root, manifest)

            with self.assertRaisesRegex(
                TranscriptFixtureDatasetError, "contract is invalid"
            ):
                TranscriptFixtureDataset.load(root)

    def test_rejects_symlinked_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root)
            with patch.object(
                Path,
                "is_symlink",
                lambda path: path.name == "yt-dlp-success.json3",
            ):
                with self.assertRaisesRegex(
                    TranscriptFixtureDatasetError, "payload is unavailable"
                ):
                    TranscriptFixtureDataset.load(root)

    def test_rejects_malformed_json3_after_valid_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root, payload=b'{"events":[]}')

            with self.assertRaisesRegex(
                TranscriptFixtureDatasetError, "could not be normalized"
            ):
                TranscriptFixtureDataset.load(root)


def _json3_payload() -> bytes:
    payload = {
        "wireMagic": "pb3",
        "events": [
            {
                "tStartMs": 0,
                "dDurationMs": 1000,
                "segs": [{"utf8": "yt-dlp first"}],
            },
            {
                "tStartMs": 1000,
                "dDurationMs": 1500,
                "segs": [{"utf8": "yt-dlp second"}],
            },
        ],
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _write_dataset(root: Path, *, payload: bytes | None = None) -> None:
    content = _json3_payload() if payload is None else payload
    (root / "yt-dlp-success.json3").write_bytes(content)
    _write_manifest(
        root,
        {
            "schema_version": 2,
            "fixture": {
                "file": "yt-dlp-success.json3",
                "format": "json3",
                "sha256": hashlib.sha256(content).hexdigest(),
            },
        },
    )


def _read_manifest(root: Path) -> dict[str, object]:
    document = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError("test manifest must be an object")
    return document


def _write_manifest(root: Path, manifest: object) -> None:
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

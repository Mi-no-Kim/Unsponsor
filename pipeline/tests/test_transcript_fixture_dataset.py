from __future__ import annotations

import hashlib
import gzip
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
    def test_loads_both_actual_shapes_through_production_normalizers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root)

            dataset = TranscriptFixtureDataset.load(root)

            self.assertEqual(dataset.library_success.text, "api first\napi second")
            self.assertEqual(len(dataset.library_success.segments), 2)
            self.assertEqual(dataset.ytdlp_success.text, "yt-dlp first\nyt-dlp second")
            self.assertEqual(len(dataset.ytdlp_success.segments), 2)

    def test_rejects_each_payload_changed_after_capture(self) -> None:
        for filename in ("api-success.json.gz", "yt-dlp-success.json3.gz"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _write_dataset(root)
                (root / filename).write_text("{}", encoding="utf-8")

                with self.assertRaisesRegex(
                    TranscriptFixtureDatasetError, "checksum does not match"
                ):
                    TranscriptFixtureDataset.load(root)

    def test_rejects_payload_path_outside_fixture_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root)
            manifest = _read_manifest(root)
            manifest["fixtures"]["library_success"]["file"] = "../outside.json"
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
                lambda path: path.name == "api-success.json.gz",
            ):
                with self.assertRaisesRegex(
                    TranscriptFixtureDatasetError, "payload is unavailable"
                ):
                    TranscriptFixtureDataset.load(root)

    def test_rejects_invalid_gzip_after_valid_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root)
            invalid_gzip = b"not-a-gzip-payload"
            (root / "api-success.json.gz").write_bytes(invalid_gzip)
            manifest = _read_manifest(root)
            manifest["fixtures"]["library_success"]["sha256"] = hashlib.sha256(
                invalid_gzip
            ).hexdigest()
            _write_manifest(root, manifest)

            with self.assertRaisesRegex(
                TranscriptFixtureDatasetError, "gzip payload is invalid"
            ):
                TranscriptFixtureDataset.load(root)

    def test_rejects_decompressed_payload_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root)

            with (
                patch("transcript.fixture_dataset._MAX_DECOMPRESSED_BYTES", 8),
                self.assertRaisesRegex(
                    TranscriptFixtureDatasetError, "decompressed size is invalid"
                ),
            ):
                TranscriptFixtureDataset.load(root)

    def test_rejects_malformed_library_shape_after_valid_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root, library_payload=b'[{"text":"missing timing"}]')

            with self.assertRaisesRegex(
                TranscriptFixtureDatasetError, "could not be normalized"
            ):
                TranscriptFixtureDataset.load(root)

    def test_rejects_malformed_json3_after_valid_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_dataset(root, ytdlp_payload=b'{"events":[]}')

            with self.assertRaisesRegex(
                TranscriptFixtureDatasetError, "could not be normalized"
            ):
                TranscriptFixtureDataset.load(root)


def _library_payload() -> bytes:
    payload = [
        {"text": "api first", "start": 0.0, "duration": 1.0},
        {"text": "api second", "start": 1.0, "duration": 1.5},
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


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


def _write_dataset(
    root: Path,
    *,
    library_payload: bytes | None = None,
    ytdlp_payload: bytes | None = None,
) -> None:
    library_content = _library_payload() if library_payload is None else library_payload
    ytdlp_content = _json3_payload() if ytdlp_payload is None else ytdlp_payload
    compressed_library = gzip.compress(library_content, compresslevel=9, mtime=0)
    compressed_ytdlp = gzip.compress(ytdlp_content, compresslevel=9, mtime=0)
    (root / "api-success.json.gz").write_bytes(compressed_library)
    (root / "yt-dlp-success.json3.gz").write_bytes(compressed_ytdlp)
    _write_manifest(
        root,
        {
            "schema_version": 4,
            "fixtures": {
                "library_success": {
                    "file": "api-success.json.gz",
                    "format": "youtube-transcript-api-snippets",
                    "compression": "gzip",
                    "sha256": hashlib.sha256(compressed_library).hexdigest(),
                },
                "ytdlp_success": {
                    "file": "yt-dlp-success.json3.gz",
                    "format": "json3",
                    "compression": "gzip",
                    "sha256": hashlib.sha256(compressed_ytdlp).hexdigest(),
                },
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

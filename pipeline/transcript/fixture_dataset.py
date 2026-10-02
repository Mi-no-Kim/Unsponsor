"""레포 밖 API·yt-dlp 성공 fixture를 검증하고 생산 모델로 변환한다."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from transcript.library_extractor import _normalize_response
from transcript.model import TranscriptExtractionResult, TranscriptSegment
from transcript.ytdlp_extractor import _normalize_json3


class TranscriptFixtureDatasetError(ValueError):
    """비공개 fixture가 누락·변조됐거나 계약과 맞지 않음을 나타낸다."""


@dataclass(frozen=True)
class TranscriptFixtureDataset:
    """W-027 DB 검증에 필요한 두 실제 성공 결과를 보관한다."""

    library_success: TranscriptExtractionResult
    ytdlp_success: TranscriptExtractionResult

    @classmethod
    def load(cls, directory: Path) -> TranscriptFixtureDataset:
        root = Path(directory)
        if not root.is_dir() or root.is_symlink():
            raise TranscriptFixtureDatasetError(
                "fixture directory must be an existing non-symlink directory"
            )

        manifest_path = root / "manifest.json"
        if manifest_path.is_symlink():
            raise TranscriptFixtureDatasetError("fixture manifest must not be a symlink")
        manifest = _read_json(manifest_path, "manifest")
        if not isinstance(manifest, dict) or set(manifest) != {
            "schema_version",
            "fixtures",
        }:
            raise TranscriptFixtureDatasetError("fixture manifest contract is invalid")
        if manifest["schema_version"] != 3:
            raise TranscriptFixtureDatasetError("fixture manifest version is unsupported")

        fixtures = manifest["fixtures"]
        if not isinstance(fixtures, dict) or set(fixtures) != {
            "library_success",
            "ytdlp_success",
        }:
            raise TranscriptFixtureDatasetError("fixture manifest contract is invalid")

        library_path = _verified_payload_path(
            root,
            fixtures["library_success"],
            filename=_LIBRARY_FIXTURE_FILENAME,
            payload_format=_LIBRARY_FIXTURE_FORMAT,
            label="library",
        )
        ytdlp_path = _verified_payload_path(
            root,
            fixtures["ytdlp_success"],
            filename=_YTDLP_FIXTURE_FILENAME,
            payload_format="json3",
            label="yt-dlp",
        )

        try:
            library_result = TranscriptExtractionResult.succeeded(
                _normalize_library_fixture(library_path)
            )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as error:
            raise TranscriptFixtureDatasetError(
                "library fixture could not be normalized"
            ) from error
        try:
            ytdlp_result = TranscriptExtractionResult.succeeded(
                _normalize_json3(ytdlp_path)
            )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as error:
            raise TranscriptFixtureDatasetError(
                "yt-dlp fixture could not be normalized"
            ) from error
        return cls(
            library_success=library_result,
            ytdlp_success=ytdlp_result,
        )


@dataclass(frozen=True)
class _LibraryFixtureSnippet:
    text: str
    start: float
    duration: float


_LIBRARY_FIXTURE_FILENAME = "api-success.json"
_LIBRARY_FIXTURE_FORMAT = "youtube-transcript-api-snippets"
_YTDLP_FIXTURE_FILENAME = "yt-dlp-success.json3"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _verified_payload_path(
    root: Path,
    document: object,
    *,
    filename: str,
    payload_format: str,
    label: str,
) -> Path:
    if not isinstance(document, dict) or set(document) != {
        "file",
        "format",
        "sha256",
    }:
        raise TranscriptFixtureDatasetError(f"{label} fixture metadata is invalid")
    if document["format"] != payload_format or document["file"] != filename:
        raise TranscriptFixtureDatasetError(f"{label} fixture metadata is invalid")

    manifest_filename = document["file"]
    expected_hash = document["sha256"]
    if (
        not isinstance(manifest_filename, str)
        or Path(manifest_filename).name != manifest_filename
        or not isinstance(expected_hash, str)
        or not _SHA256_PATTERN.fullmatch(expected_hash)
    ):
        raise TranscriptFixtureDatasetError(f"{label} fixture metadata is invalid")

    payload_path = root / manifest_filename
    if not payload_path.is_file() or payload_path.is_symlink():
        raise TranscriptFixtureDatasetError(f"{label} fixture payload is unavailable")
    try:
        payload = payload_path.read_bytes()
    except OSError as error:
        raise TranscriptFixtureDatasetError(
            f"{label} fixture payload is unavailable"
        ) from error
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise TranscriptFixtureDatasetError(
            f"{label} fixture payload checksum does not match"
        )
    return payload_path


def _normalize_library_fixture(path: Path) -> tuple[TranscriptSegment, ...]:
    document = _read_json(path, "library fixture")
    if not isinstance(document, list):
        raise TranscriptFixtureDatasetError("library fixture must be an array")

    snippets: list[_LibraryFixtureSnippet] = []
    for raw_snippet in document:
        if not isinstance(raw_snippet, dict) or set(raw_snippet) != {
            "text",
            "start",
            "duration",
        }:
            raise TranscriptFixtureDatasetError(
                "library fixture snippet contract is invalid"
            )
        snippets.append(
            _LibraryFixtureSnippet(
                text=raw_snippet["text"],
                start=raw_snippet["start"],
                duration=raw_snippet["duration"],
            )
        )
    return _normalize_response(snippets)


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TranscriptFixtureDatasetError(
            f"{label} JSON is unavailable or invalid"
        ) from error

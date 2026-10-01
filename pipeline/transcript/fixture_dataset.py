"""레포 밖 yt-dlp JSON3 fixture를 검증하고 생산 모델로 변환한다."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from transcript.model import TranscriptExtractionResult
from transcript.ytdlp_extractor import _normalize_json3


class TranscriptFixtureDatasetError(ValueError):
    """비공개 fixture가 누락·변조됐거나 계약과 맞지 않음을 나타낸다."""


@dataclass(frozen=True)
class TranscriptFixtureDataset:
    """W-027 DB 검증에 필요한 실제 yt-dlp 성공 결과만 보관한다."""

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
            "fixture",
        }:
            raise TranscriptFixtureDatasetError("fixture manifest contract is invalid")
        if manifest["schema_version"] != 2:
            raise TranscriptFixtureDatasetError("fixture manifest version is unsupported")

        payload_path = _verified_payload_path(root, manifest["fixture"])
        try:
            result = TranscriptExtractionResult.succeeded(
                _normalize_json3(payload_path)
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
        return cls(ytdlp_success=result)


_FIXTURE_FILENAME = "yt-dlp-success.json3"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _verified_payload_path(root: Path, document: object) -> Path:
    if not isinstance(document, dict) or set(document) != {
        "file",
        "format",
        "sha256",
    }:
        raise TranscriptFixtureDatasetError("yt-dlp fixture metadata is invalid")
    if document["format"] != "json3" or document["file"] != _FIXTURE_FILENAME:
        raise TranscriptFixtureDatasetError("yt-dlp fixture metadata is invalid")

    filename = document["file"]
    expected_hash = document["sha256"]
    if (
        not isinstance(filename, str)
        or Path(filename).name != filename
        or not isinstance(expected_hash, str)
        or not _SHA256_PATTERN.fullmatch(expected_hash)
    ):
        raise TranscriptFixtureDatasetError("yt-dlp fixture metadata is invalid")

    payload_path = root / filename
    if not payload_path.is_file() or payload_path.is_symlink():
        raise TranscriptFixtureDatasetError("yt-dlp fixture payload is unavailable")
    try:
        payload = payload_path.read_bytes()
    except OSError as error:
        raise TranscriptFixtureDatasetError(
            "yt-dlp fixture payload is unavailable"
        ) from error
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise TranscriptFixtureDatasetError(
            "yt-dlp fixture payload checksum does not match"
        )
    return payload_path


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TranscriptFixtureDatasetError(
            f"{label} JSON is unavailable or invalid"
        ) from error

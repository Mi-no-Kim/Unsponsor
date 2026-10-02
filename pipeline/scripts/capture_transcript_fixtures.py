"""실제 API 결과와 yt-dlp JSON3 본문을 W-027 로컬 fixture로 수집한다."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import re
import subprocess
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from common.config import ConfigurationError, load_dataimpulse_proxy_settings
from transcript.dataimpulse_proxy import create_dataimpulse_api
from transcript.fixture_dataset import TranscriptFixtureDataset
from transcript.library_extractor import _normalize_response
from transcript.model import TranscriptFailure
from transcript.ytdlp_extractor import (
    BgutilProviderSession,
    ProviderUnavailable,
    _YtDlpErrorLogger,
    _download_failure,
    _normalize_json3,
    _yt_dlp_options,
)
from youtube_transcript_api._errors import (
    IpBlocked,
    NoTranscriptFound,
    TranscriptsDisabled,
)
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


class TranscriptFixtureCaptureError(RuntimeError):
    """fixture 수집 실패를 민감 원문 없는 안전 코드로 표현한다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LANGUAGE_CODES = ("ko", "en")
_LIBRARY_FIXTURE_FILENAME = "api-success.json.gz"
_YTDLP_FIXTURE_FILENAME = "yt-dlp-success.json3.gz"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-file", required=True, type=Path)
    parser.add_argument("--fixture-dir", required=True, type=Path)
    parser.add_argument("--provider-home", type=Path)
    parser.add_argument("--cookie-file", type=Path)
    parser.add_argument("--confirm-external-request", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.confirm_external_request:
        parser.error("--confirm-external-request is required")

    started_at = time.monotonic()
    try:
        _validate_optional_paths(arguments.provider_home, arguments.cookie_file)
        _require_new_dataset(arguments.fixture_dir)
        video_id = _read_selection(arguments.selection_file)
        repository_root = Path(__file__).resolve().parents[2]
        library_payload = _capture_library_payload(repository_root, video_id)
        ytdlp_payload = _capture_ytdlp_payload(
            video_id,
            provider_home=arguments.provider_home,
            cookie_file=arguments.cookie_file,
            error_logger=_silent_logger(),
        )
        _write_dataset(arguments.fixture_dir, library_payload, ytdlp_payload)
    except TranscriptFixtureCaptureError as error:
        _print_failure(error.code)
    except Exception:
        _print_failure("unexpected_error")

    print(
        json.dumps(
            {
                "status": "passed",
                "code": "fixtures_captured",
                "elapsed_seconds": _elapsed(started_at),
            },
            separators=(",", ":"),
        )
    )


def _print_failure(code: str) -> None:
    print(json.dumps({"status": "failed", "error": code}, separators=(",", ":")))
    raise SystemExit(1)


def _elapsed(started_at: float) -> float:
    return round(time.monotonic() - started_at, 3)


def _validate_optional_paths(
    provider_home: Path | None, cookie_file: Path | None
) -> None:
    if cookie_file is not None and (
        not cookie_file.is_file() or cookie_file.is_symlink()
    ):
        raise TranscriptFixtureCaptureError("cookie_file_unavailable")
    if provider_home is not None and (
        not provider_home.is_dir()
        or provider_home.is_symlink()
        or not (provider_home / "build" / "main.js").is_file()
    ):
        raise TranscriptFixtureCaptureError("provider_unavailable")


def _require_new_dataset(fixture_dir: Path) -> None:
    root = Path(fixture_dir)
    if root.exists() and (not root.is_dir() or root.is_symlink()):
        raise TranscriptFixtureCaptureError("fixture_directory_invalid")
    if any(
        (root / filename).exists()
        for filename in (
            _LIBRARY_FIXTURE_FILENAME,
            _YTDLP_FIXTURE_FILENAME,
            "api-success.json",
            "yt-dlp-success.json3",
            "manifest.json",
        )
    ):
        raise TranscriptFixtureCaptureError("fixture_dataset_already_exists")


def _read_selection(path: Path) -> str:
    selection_path = Path(path)
    if selection_path.is_symlink():
        raise TranscriptFixtureCaptureError("selection_file_invalid")
    try:
        document = json.loads(selection_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TranscriptFixtureCaptureError("selection_file_invalid") from error
    if not isinstance(document, dict) or set(document) != {"video_id"}:
        raise TranscriptFixtureCaptureError("selection_contract_invalid")
    video_id = document["video_id"]
    if not isinstance(video_id, str) or not _VIDEO_ID_PATTERN.fullmatch(video_id):
        raise TranscriptFixtureCaptureError("selection_video_id_invalid")
    return video_id


def _capture_library_payload(repository_root: Path, video_id: str) -> bytes:
    try:
        settings = load_dataimpulse_proxy_settings(repository_root)
    except ConfigurationError as error:
        raise TranscriptFixtureCaptureError("proxy_configuration_invalid") from error

    session = None
    try:
        session, api = create_dataimpulse_api(settings)
        fetched = api.fetch(
            video_id,
            languages=list(_LANGUAGE_CODES),
            preserve_formatting=False,
        )
        raw_data = fetched.to_raw_data()
        _normalize_response(fetched)
        payload = json.dumps(
            raw_data,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except IpBlocked as error:
        raise TranscriptFixtureCaptureError("rate_limited") from error
    except (NoTranscriptFound, TranscriptsDisabled) as error:
        raise TranscriptFixtureCaptureError("no_transcript") from error
    except TranscriptFixtureCaptureError:
        raise
    except Exception as error:
        raise TranscriptFixtureCaptureError("api_fixture_capture_failed") from error
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    if not payload:
        raise TranscriptFixtureCaptureError("api_fixture_payload_invalid")
    return payload


def _capture_ytdlp_payload(
    video_id: str,
    *,
    provider_home: Path | None,
    cookie_file: Path | None,
    error_logger: logging.Logger,
) -> bytes:
    failure, payload = _download_ytdlp_payload(
        video_id,
        provider_base_url=None,
        cookie_file=cookie_file,
        error_logger=error_logger,
    )
    if payload is not None:
        return payload
    if failure is TranscriptFailure.RATE_LIMITED:
        raise TranscriptFixtureCaptureError("rate_limited")
    if failure is not TranscriptFailure.PO_TOKEN_REQUIRED or provider_home is None:
        raise TranscriptFixtureCaptureError("ytdlp_fixture_capture_failed")

    try:
        with BgutilProviderSession(provider_home) as provider:
            failure, payload = _download_ytdlp_payload(
                video_id,
                provider_base_url=provider.base_url,
                cookie_file=cookie_file,
                error_logger=error_logger,
            )
    except (ProviderUnavailable, OSError, subprocess.SubprocessError) as error:
        raise TranscriptFixtureCaptureError("provider_unavailable") from error
    if payload is not None:
        return payload
    if failure is TranscriptFailure.RATE_LIMITED:
        raise TranscriptFixtureCaptureError("rate_limited")
    raise TranscriptFixtureCaptureError("ytdlp_fixture_capture_failed")


def _download_ytdlp_payload(
    video_id: str,
    *,
    provider_base_url: str | None,
    cookie_file: Path | None,
    error_logger: logging.Logger,
) -> tuple[TranscriptFailure | None, bytes | None]:
    for language_code in _LANGUAGE_CODES:
        with tempfile.TemporaryDirectory(
            prefix="unsponsor-w027-fixture-"
        ) as directory:
            output_directory = Path(directory)
            ytdlp_logger = _YtDlpErrorLogger(error_logger)
            try:
                with YoutubeDL(
                    _yt_dlp_options(
                        output_directory,
                        language_code=language_code,
                        node_executable="node",
                        provider_base_url=provider_base_url,
                        cookie_file=cookie_file,
                        ytdlp_logger=ytdlp_logger,
                    )
                ) as downloader:
                    exit_code = downloader.download(
                        [f"https://www.youtube.com/watch?v={video_id}"]
                    )
            except DownloadError as error:
                return (
                    _download_failure(
                        error, failure_hint=ytdlp_logger.failure_hint
                    ),
                    None,
                )
            except Exception as error:
                return (
                    _download_failure(
                        error, failure_hint=ytdlp_logger.failure_hint
                    ),
                    None,
                )
            if exit_code != 0:
                return (
                    ytdlp_logger.failure_hint or TranscriptFailure.TRANSIENT_ERROR,
                    None,
                )

            files = tuple(output_directory.glob("*.json3"))
            if not files:
                if ytdlp_logger.failure_hint is not None:
                    return ytdlp_logger.failure_hint, None
                continue
            if len(files) != 1:
                return TranscriptFailure.INVALID_RESPONSE, None
            try:
                _normalize_json3(files[0])
                return None, files[0].read_bytes()
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
            ):
                return TranscriptFailure.INVALID_RESPONSE, None
    return TranscriptFailure.NO_TRANSCRIPT, None


def _write_dataset(
    fixture_dir: Path,
    library_payload: bytes,
    ytdlp_payload: bytes,
) -> None:
    if not isinstance(library_payload, bytes) or not library_payload:
        raise TranscriptFixtureCaptureError("api_fixture_payload_invalid")
    if not isinstance(ytdlp_payload, bytes) or not ytdlp_payload:
        raise TranscriptFixtureCaptureError("ytdlp_fixture_payload_invalid")

    root = Path(fixture_dir)
    _require_new_dataset(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise TranscriptFixtureCaptureError("fixture_directory_unavailable") from error

    compressed_library_payload = _compress_payload(library_payload)
    compressed_ytdlp_payload = _compress_payload(ytdlp_payload)
    library_path = root / _LIBRARY_FIXTURE_FILENAME
    ytdlp_path = root / _YTDLP_FIXTURE_FILENAME
    manifest_path = root / "manifest.json"
    manifest = {
        "schema_version": 4,
        "fixtures": {
            "library_success": {
                "file": _LIBRARY_FIXTURE_FILENAME,
                "format": "youtube-transcript-api-snippets",
                "compression": "gzip",
                "sha256": hashlib.sha256(compressed_library_payload).hexdigest(),
            },
            "ytdlp_success": {
                "file": _YTDLP_FIXTURE_FILENAME,
                "format": "json3",
                "compression": "gzip",
                "sha256": hashlib.sha256(compressed_ytdlp_payload).hexdigest(),
            },
        },
    }
    created_paths: list[Path] = []
    try:
        with library_path.open("xb") as stream:
            created_paths.append(library_path)
            stream.write(compressed_library_payload)
        with ytdlp_path.open("xb") as stream:
            created_paths.append(ytdlp_path)
            stream.write(compressed_ytdlp_payload)
        with manifest_path.open("x", encoding="utf-8", newline="\n") as stream:
            created_paths.append(manifest_path)
            json.dump(manifest, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        TranscriptFixtureDataset.load(root)
    except Exception as error:
        for created_path in reversed(created_paths):
            created_path.unlink(missing_ok=True)
        if isinstance(error, TranscriptFixtureCaptureError):
            raise
        raise TranscriptFixtureCaptureError("fixture_write_failed") from error


def _compress_payload(payload: bytes) -> bytes:
    """fixture를 같은 입력에서 같은 바이트가 나오도록 gzip level 9로 압축한다."""

    try:
        return gzip.compress(payload, compresslevel=9, mtime=0)
    except (OSError, ValueError) as error:
        raise TranscriptFixtureCaptureError("fixture_compression_failed") from error


def _silent_logger() -> logging.Logger:
    logger = logging.getLogger("unsponsor.transcript.w027_fixture_capture")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


if __name__ == "__main__":
    main()

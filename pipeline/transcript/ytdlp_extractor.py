"""yt-dlp와 작업 수명에 한정된 PoToken Provider로 자막을 확보한다."""

from __future__ import annotations

import json
import logging
import re
import socket
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import ContextManager, Protocol
from urllib.error import URLError
from urllib.request import urlopen

from common.restricted_error_log import transcript_error_logger
from transcript.model import (
    TranscriptExtractionResult,
    TranscriptFailure,
    TranscriptSegment,
    normalize_segment_text,
)
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


_DEFAULT_LANGUAGE_CODES = ("ko", "en")
_YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
_REQUEST_SLEEP_SECONDS = 2.0
_SUBTITLE_SLEEP_SECONDS = 35.0
_PO_TOKEN_SIGNAL = re.compile(r"(?i)\bpo[\s_-]?token\b")
_RATE_LIMIT_SIGNAL = re.compile(
    r"(?i)(?:http(?:\s+error)?\s*429|\b429\b.*too many requests|too many requests)"
)
_MAX_SUBTITLE_BYTES = 64 * 1024 * 1024
_MAX_TIMESTAMP_MS = 4_294_967_295


class _YtDlp(Protocol):
    def __enter__(self) -> _YtDlp:
        """다운로더 세션을 시작한다."""

    def __exit__(self, *args: object) -> None:
        """다운로더 세션을 끝낸다."""

    def download(self, url_list: list[str]) -> int:
        """자막만 임시 디렉터리에 쓴다."""


class _ProviderSession(Protocol):
    @property
    def base_url(self) -> str:
        """yt-dlp가 호출할 loopback Provider URL이다."""

    def __enter__(self) -> _ProviderSession:
        """Provider를 시작한다."""

    def __exit__(self, *args: object) -> None:
        """Provider를 반드시 종료한다."""


YtDlpFactory = Callable[[Mapping[str, object]], ContextManager[_YtDlp]]
ProviderSessionFactory = Callable[[], ContextManager[_ProviderSession]]


class _YtDlpErrorLogger:
    """yt-dlp 원문은 버리고 후속 경로 판단에 필요한 신호만 보존한다."""

    def __init__(self, error_logger: logging.Logger) -> None:
        self._error_logger = error_logger
        self.po_token_required = False
        self.rate_limited = False

    def debug(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        self._observe(message)
        self._error_logger.warning("yt-dlp reported a warning")

    def error(self, message: str) -> None:
        self._observe(message)
        self._error_logger.error("yt-dlp reported an error")

    @property
    def failure_hint(self) -> TranscriptFailure | None:
        if self.rate_limited:
            return TranscriptFailure.RATE_LIMITED
        if self.po_token_required:
            return TranscriptFailure.PO_TOKEN_REQUIRED
        return None

    def _observe(self, message: str) -> None:
        if _RATE_LIMIT_SIGNAL.search(message):
            self.rate_limited = True
        if _PO_TOKEN_SIGNAL.search(message):
            self.po_token_required = True


class ProviderUnavailable(RuntimeError):
    """PoToken Provider를 안전하게 시작·준비할 수 없음을 나타낸다."""


@dataclass
class BgutilProviderSession:
    """현재 작업 동안에만 bgutil HTTP Provider를 loopback에서 실행한다."""

    provider_home: Path
    node_executable: str | Path = "node"
    startup_timeout_seconds: float = 20.0

    _process: subprocess.Popen[bytes] | None = None
    _port: int | None = None

    @property
    def base_url(self) -> str:
        if self._port is None:
            raise ProviderUnavailable("PoToken provider is not running")
        return f"http://127.0.0.1:{self._port}"

    def __enter__(self) -> BgutilProviderSession:
        script_path = self.provider_home / "build" / "main.js"
        if not script_path.is_file():
            raise ProviderUnavailable("PoToken provider build is unavailable")

        self._port = _reserve_loopback_port()
        self._process = subprocess.Popen(
            [
                str(self.node_executable),
                str(script_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(self._port),
            ],
            cwd=self.provider_home,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self._wait_until_ready()
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args: object) -> None:
        process = self._process
        self._process = None
        self._port = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def _wait_until_ready(self) -> None:
        if self._process is None:
            raise ProviderUnavailable("PoToken provider did not start")

        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise ProviderUnavailable("PoToken provider exited during startup")
            try:
                with urlopen(f"{self.base_url}/ping", timeout=1):
                    return
            except URLError:
                time.sleep(0.1)
        raise ProviderUnavailable("PoToken provider was not ready before timeout")


class YtDlpTranscriptExtractor:
    """yt-dlp 자막 결과를 기존 자막 추출기와 같은 안전한 결과 형식으로 바꾼다."""

    def __init__(
        self,
        *,
        provider_home: Path | None = None,
        cookie_file: Path | None = None,
        node_executable: str | Path = "node",
        ytdlp_factory: YtDlpFactory = YoutubeDL,
        provider_session_factory: ProviderSessionFactory | None = None,
        error_logger: logging.Logger | None = None,
    ) -> None:
        self._ytdlp_factory = ytdlp_factory
        if provider_session_factory is not None:
            self._provider_session_factory = provider_session_factory
        elif provider_home is not None:
            self._provider_session_factory = lambda: BgutilProviderSession(
                provider_home=provider_home,
                node_executable=node_executable,
            )
        else:
            self._provider_session_factory = None
        self._node_executable = node_executable
        self._cookie_file = Path(cookie_file) if cookie_file is not None else None
        self._error_logger = (
            error_logger if error_logger is not None else transcript_error_logger()
        )

    def extract(
        self,
        video_id: str,
        *,
        language_codes: Sequence[str] = _DEFAULT_LANGUAGE_CODES,
    ) -> TranscriptExtractionResult:
        """토큰 불필요 경로를 먼저 시도하고 필요할 때만 Provider를 실행한다."""

        primary_result = self.extract_token_free(
            video_id, language_codes=language_codes
        )
        if primary_result.is_success:
            return primary_result
        if primary_result.failure is not TranscriptFailure.PO_TOKEN_REQUIRED:
            return primary_result

        if self._provider_session_factory is None:
            return primary_result

        return self.extract_with_provider(video_id, language_codes=language_codes)

    def extract_token_free(
        self,
        video_id: str,
        *,
        language_codes: Sequence[str] = _DEFAULT_LANGUAGE_CODES,
    ) -> TranscriptExtractionResult:
        """PoToken 없이 자동 자막을 한 번 추출한다."""

        return self._extract_with_languages(
            video_id,
            language_codes=language_codes,
            provider_base_url=None,
        )

    def extract_with_provider(
        self,
        video_id: str,
        *,
        language_codes: Sequence[str] = _DEFAULT_LANGUAGE_CODES,
    ) -> TranscriptExtractionResult:
        """loopback Provider를 사용한 web/PoToken 자막 경로를 한 번 추출한다."""

        if self._provider_session_factory is None:
            return TranscriptExtractionResult.failed(TranscriptFailure.PO_TOKEN_REQUIRED)

        try:
            with self._provider_session_factory() as provider:
                fallback_result = self._extract_with_languages(
                    video_id,
                    language_codes=language_codes,
                    provider_base_url=provider.base_url,
                )
        except (ProviderUnavailable, OSError, subprocess.SubprocessError):
            self._error_logger.error("yt-dlp PoToken provider was unavailable")
            return TranscriptExtractionResult.failed(TranscriptFailure.PO_TOKEN_REQUIRED)

        return fallback_result

    def _extract_with_languages(
        self,
        video_id: str,
        *,
        language_codes: Sequence[str],
        provider_base_url: str | None,
    ) -> TranscriptExtractionResult:
        for language_code in language_codes:
            result = self._download_language(
                video_id,
                language_code=language_code,
                provider_base_url=provider_base_url,
            )
            if result.is_success:
                return result
            if result.failure is not TranscriptFailure.NO_TRANSCRIPT:
                return result
        return TranscriptExtractionResult.failed(TranscriptFailure.NO_TRANSCRIPT)

    def _download_language(
        self,
        video_id: str,
        *,
        language_code: str,
        provider_base_url: str | None,
    ) -> TranscriptExtractionResult:
        with tempfile.TemporaryDirectory(prefix="unsponsor-transcript-") as directory:
            output_directory = Path(directory)
            ytdlp_logger = _YtDlpErrorLogger(self._error_logger)
            try:
                with self._ytdlp_factory(
                    _yt_dlp_options(
                        output_directory,
                        language_code=language_code,
                        node_executable=self._node_executable,
                        provider_base_url=provider_base_url,
                        cookie_file=self._cookie_file,
                        ytdlp_logger=ytdlp_logger,
                    )
                ) as downloader:
                    exit_code = downloader.download(
                        [_YOUTUBE_WATCH_URL.format(video_id=video_id)]
                    )
            except DownloadError as error:
                failure = _download_failure(
                    error, failure_hint=ytdlp_logger.failure_hint
                )
                self._error_logger.error(
                    "yt-dlp subtitle download failed safely: %s", failure.value
                )
                return TranscriptExtractionResult.failed(failure)
            except Exception as error:
                failure = _download_failure(
                    error, failure_hint=ytdlp_logger.failure_hint
                )
                self._error_logger.error(
                    "yt-dlp subtitle extraction failed safely: %s", failure.value
                )
                return TranscriptExtractionResult.failed(failure)

            if exit_code != 0:
                failure = (
                    ytdlp_logger.failure_hint or TranscriptFailure.TRANSIENT_ERROR
                )
                self._error_logger.error(
                    "yt-dlp exited with a non-zero status safely: %s",
                    failure.value,
                )
                return TranscriptExtractionResult.failed(failure)

            srv1_files = tuple(output_directory.glob("*.srv1"))
            json3_files = tuple(output_directory.glob("*.json3"))
            subtitle_files = srv1_files or json3_files
            if not subtitle_files:
                if ytdlp_logger.failure_hint is not None:
                    return TranscriptExtractionResult.failed(
                        ytdlp_logger.failure_hint
                    )
                return TranscriptExtractionResult.failed(TranscriptFailure.NO_TRANSCRIPT)
            if len(subtitle_files) != 1:
                return TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE)

            try:
                return TranscriptExtractionResult.succeeded(
                    _normalize_subtitle_file(subtitle_files[0])
                )
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
                TypeError,
                ElementTree.ParseError,
                InvalidOperation,
            ):
                self._error_logger.error("yt-dlp wrote an invalid subtitle")
                return TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE)


def _yt_dlp_options(
    output_directory: Path,
    *,
    language_code: str,
    node_executable: str | Path,
    provider_base_url: str | None,
    cookie_file: Path | None,
    ytdlp_logger: _YtDlpErrorLogger,
) -> dict[str, object]:
    """수동·원본 자동 자막만 느리고 제한된 요청으로 srv1 우선 기록한다."""

    options: dict[str, object] = {
        "skip_download": True,
        "ignore_no_formats_error": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [language_code],
        "subtitlesformat": "srv1/json3",
        "retries": 0,
        "extractor_retries": 0,
        "fragment_retries": 0,
        "sleep_interval_requests": _REQUEST_SLEEP_SECONDS,
        "sleep_interval_subtitles": _SUBTITLE_SLEEP_SECONDS,
        "paths": {"home": str(output_directory)},
        "outtmpl": {"default": "%(id)s.%(ext)s"},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": ytdlp_logger,
        "js_runtimes": {"node": {"path": str(node_executable)}},
        "extractor_args": {"youtube": {"skip": ["translated_subs"]}},
    }
    if provider_base_url is not None:
        options["extractor_args"] = {
            "youtube": {
                "skip": ["translated_subs"],
                "player_client": ["web"],
            },
            "youtubepot-bgutilhttp": {"base_url": [provider_base_url]},
        }
    if cookie_file is not None:
        options["cookiefile"] = str(cookie_file)
    return options


def _download_failure(
    error: BaseException,
    *,
    failure_hint: TranscriptFailure | None = None,
) -> TranscriptFailure:
    """429와 명시적 PoToken 요구만 분류하고 세부 오류는 버린다."""

    if _has_http_status(error, 429):
        return TranscriptFailure.RATE_LIMITED
    if failure_hint is not None:
        return failure_hint
    if _exception_mentions_po_token(error):
        return TranscriptFailure.PO_TOKEN_REQUIRED
    return TranscriptFailure.TRANSIENT_ERROR


def _exception_mentions_po_token(error: BaseException) -> bool:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if _PO_TOKEN_SIGNAL.search(str(current)):
            return True
        for chained in (current.__cause__, current.__context__):
            if isinstance(chained, BaseException):
                pending.append(chained)
    return False


def _has_http_status(error: BaseException, expected_status: int) -> bool:
    """yt-dlp의 래핑 예외와 원래 HTTP 예외에서 상태 코드만 읽는다."""

    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        for attribute in ("status", "code"):
            status = getattr(current, attribute, None)
            if isinstance(status, int) and not isinstance(status, bool):
                if status == expected_status:
                    return True

        exc_info = getattr(current, "exc_info", None)
        if (
            isinstance(exc_info, tuple)
            and len(exc_info) >= 2
            and isinstance(exc_info[1], BaseException)
        ):
            pending.append(exc_info[1])
        for chained in (current.__cause__, current.__context__):
            if isinstance(chained, BaseException):
                pending.append(chained)

    # 일부 yt-dlp DownloadError는 원래 HTTP 예외를 보존하지 않는다. 이 경우에도
    # 오류 문자열을 반환·기록하지 않고 상태 숫자만 즉시 판별한다.
    return isinstance(error, DownloadError) and f"HTTP Error {expected_status}" in str(
        error
    )


def _normalize_json3(path: Path) -> tuple[TranscriptSegment, ...]:
    """자막 JSON3 이벤트를 시간·순서를 보존한 세그먼트로 정규화한다."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("JSON3 subtitle payload has no events")

    normalized_events: list[TranscriptSegment] = []
    previous_start_ms = -1
    for event in payload["events"]:
        if not isinstance(event, dict):
            raise ValueError("JSON3 subtitle event is invalid")
        segments = event.get("segs")
        if segments is None:
            continue
        if not isinstance(segments, list):
            raise ValueError("JSON3 subtitle segments are invalid")

        parts: list[str] = []
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get("utf8"), str):
                raise ValueError("JSON3 subtitle segment has no text")
            parts.append(segment["utf8"])
        normalized_text = " ".join("".join(parts).split())
        if not normalized_text:
            continue
        start_ms = _json3_milliseconds(event.get("tStartMs"))
        duration_ms = _json3_milliseconds(event.get("dDurationMs"))
        if (
            duration_ms <= 0
            or start_ms < previous_start_ms
            or start_ms + duration_ms > _MAX_TIMESTAMP_MS
        ):
            raise ValueError("JSON3 subtitle event duration must be positive")
        normalized_events.append(
            TranscriptSegment(
                sequence=len(normalized_events),
                start_ms=start_ms,
                end_ms=start_ms + duration_ms,
                text=normalized_text,
            )
        )
        previous_start_ms = start_ms

    if not normalized_events:
        raise ValueError("JSON3 subtitle payload contains no text")
    return tuple(normalized_events)


def _normalize_subtitle_file(path: Path) -> tuple[TranscriptSegment, ...]:
    if path.suffix == ".srv1":
        return _normalize_srv1(path)
    if path.suffix == ".json3":
        return _normalize_json3(path)
    raise ValueError("unsupported yt-dlp subtitle format")


def _normalize_srv1(path: Path) -> tuple[TranscriptSegment, ...]:
    """srv1 XML을 외부 엔터티 없이 공통 시간 세그먼트로 정규화한다."""

    payload = path.read_bytes()
    if not payload or len(payload) > _MAX_SUBTITLE_BYTES:
        raise ValueError("srv1 subtitle size is invalid")
    if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
        raise ValueError("srv1 subtitle declarations are unsupported")

    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise ValueError("srv1 subtitle XML is invalid") from error
    if root.tag != "transcript" or root.attrib:
        raise ValueError("srv1 subtitle root is invalid")

    normalized_events: list[TranscriptSegment] = []
    previous_start_ms = -1
    for element in root:
        if (
            element.tag != "text"
            or set(element.attrib) != {"start", "dur"}
            or len(element) != 0
        ):
            raise ValueError("srv1 subtitle event is invalid")
        start_ms = _srv1_milliseconds(element.attrib["start"])
        duration_ms = _srv1_milliseconds(element.attrib["dur"])
        if (
            duration_ms <= 0
            or start_ms < previous_start_ms
            or start_ms + duration_ms > _MAX_TIMESTAMP_MS
        ):
            raise ValueError("srv1 subtitle timeline is invalid")
        normalized_text = normalize_segment_text(element.text or "")
        normalized_events.append(
            TranscriptSegment(
                sequence=len(normalized_events),
                start_ms=start_ms,
                end_ms=start_ms + duration_ms,
                text=normalized_text,
            )
        )
        previous_start_ms = start_ms

    if not normalized_events:
        raise ValueError("srv1 subtitle payload contains no text")
    return tuple(normalized_events)


def _srv1_milliseconds(value: str) -> int:
    try:
        seconds = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("srv1 subtitle timestamp is invalid") from error
    if not seconds.is_finite() or seconds < 0:
        raise ValueError("srv1 subtitle timestamp is invalid")
    if seconds * 1000 > _MAX_TIMESTAMP_MS:
        raise ValueError("srv1 subtitle timestamp is invalid")
    milliseconds = int((seconds * 1000).quantize(Decimal("1"), ROUND_HALF_UP))
    if milliseconds < 0:
        raise ValueError("srv1 subtitle timestamp is invalid")
    return milliseconds


def _json3_milliseconds(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _MAX_TIMESTAMP_MS
    ):
        raise ValueError("JSON3 subtitle timestamp must be a non-negative integer")
    return value


def _reserve_loopback_port() -> int:
    """Provider가 loopback에 바인딩할 임시 포트를 고른다."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])

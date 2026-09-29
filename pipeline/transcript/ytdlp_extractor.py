"""yt-dlp와 작업 수명에 한정된 PoToken Provider로 자막을 확보한다."""

from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager, Protocol
from urllib.error import URLError
from urllib.request import urlopen

from transcript.library_extractor import TranscriptExtractionResult, TranscriptFailure
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


_DEFAULT_LANGUAGE_CODES = ("ko", "en")
_YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"


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
        node_executable: str | Path = "node",
        ytdlp_factory: YtDlpFactory = YoutubeDL,
        provider_session_factory: ProviderSessionFactory | None = None,
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
        if primary_result.failure is TranscriptFailure.INVALID_RESPONSE:
            return primary_result

        if self._provider_session_factory is None:
            if primary_result.failure is TranscriptFailure.NO_TRANSCRIPT:
                return primary_result
            return TranscriptExtractionResult.failed(TranscriptFailure.PO_TOKEN_REQUIRED)

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
            return TranscriptExtractionResult.failed(TranscriptFailure.PO_TOKEN_REQUIRED)

        if fallback_result.is_success:
            return fallback_result
        if fallback_result.failure in {
            TranscriptFailure.NO_TRANSCRIPT,
            TranscriptFailure.INVALID_RESPONSE,
        }:
            return fallback_result
        return TranscriptExtractionResult.failed(TranscriptFailure.PO_TOKEN_REQUIRED)

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
            try:
                with self._ytdlp_factory(
                    _yt_dlp_options(
                        output_directory,
                        language_code=language_code,
                        node_executable=self._node_executable,
                        provider_base_url=provider_base_url,
                    )
                ) as downloader:
                    exit_code = downloader.download(
                        [_YOUTUBE_WATCH_URL.format(video_id=video_id)]
                    )
            except DownloadError:
                return TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR)
            except Exception:
                return TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR)

            if exit_code != 0:
                return TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR)

            subtitle_files = tuple(output_directory.glob("*.json3"))
            if not subtitle_files:
                return TranscriptExtractionResult.failed(TranscriptFailure.NO_TRANSCRIPT)
            if len(subtitle_files) != 1:
                return TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE)

            try:
                return TranscriptExtractionResult.succeeded(
                    _normalize_json3(subtitle_files[0])
                )
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
                return TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE)


def _yt_dlp_options(
    output_directory: Path,
    *,
    language_code: str,
    node_executable: str | Path,
    provider_base_url: str | None,
) -> dict[str, object]:
    """미디어 없이 자동 자막 JSON3만 기록하도록 yt-dlp 옵션을 만든다."""

    options: dict[str, object] = {
        "skip_download": True,
        "ignore_no_formats_error": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [language_code],
        "subtitlesformat": "json3",
        "paths": {"home": str(output_directory)},
        "outtmpl": {"default": "%(id)s.%(ext)s"},
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "js_runtimes": {"node": {"path": str(node_executable)}},
    }
    if provider_base_url is not None:
        options["extractor_args"] = {
            "youtube": {"player_client": ["web"]},
            "youtubepot-bgutilhttp": {"base_url": [provider_base_url]},
        }
    return options


def _normalize_json3(path: Path) -> str:
    """자막 JSON3의 이벤트 텍스트를 순서대로 합쳐 원문으로 정규화한다."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("JSON3 subtitle payload has no events")

    normalized_events: list[str] = []
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
        if normalized_text:
            normalized_events.append(normalized_text)

    if not normalized_events:
        raise ValueError("JSON3 subtitle payload contains no text")
    return "\n".join(normalized_events)


def _reserve_loopback_port() -> int:
    """Provider가 loopback에 바인딩할 임시 포트를 고른다."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])

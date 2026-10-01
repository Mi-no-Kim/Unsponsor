"""DataImpulse를 통한 라이브러리 자막 경로를 제한적으로 검증한다."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig

from common.config import (
    ConfigurationError,
    DataImpulseProxySettings,
    load_dataimpulse_proxy_settings,
)
from common.restricted_error_log import configure_restricted_error_log
from transcript.library_extractor import LibraryTranscriptExtractor
from transcript.model import TranscriptExtractionResult, TranscriptFailure


_DATAIMPULSE_HOST = "gw.dataimpulse.com"
_DATAIMPULSE_ROTATING_HTTP_PORT = 823
_DEFAULT_LANGUAGE_CODES = ("ko", "en")
_MAX_ATTEMPTS_PER_VIDEO = 2
_MIN_VIDEO_COUNT = 3
_MAX_VIDEO_COUNT = 5
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
_RETRYABLE_FAILURES = frozenset(
    {
        TranscriptFailure.ACCESS_RESTRICTED,
        TranscriptFailure.RATE_LIMITED,
        TranscriptFailure.TRANSIENT_ERROR,
    }
)


class CanaryConfigurationError(ValueError):
    """외부 요청 전에 사용자가 고쳐야 하는 canary 입력 오류다."""


class _CanaryAttempt(Protocol):
    def extract(
        self, video_id: str, *, language_codes: Sequence[str]
    ) -> TranscriptExtractionResult:
        """한 세션에서 영상 하나의 자막을 요청한다."""

    def close(self) -> None:
        """현재 요청 세션을 닫아 다음 시도와 연결을 공유하지 않는다."""


AttemptFactory = Callable[
    [DataImpulseProxySettings, logging.Logger | None], _CanaryAttempt
]
MonotonicClock = Callable[[], float]


@dataclass(frozen=True)
class CanaryCheck:
    """영상 식별자와 자막 원문을 제외한 한 건의 최종 결과다."""

    sequence: int
    code: str
    attempts: int
    duration_ms: int
    text_length: int | None


@dataclass(frozen=True)
class DataImpulseCanaryReport:
    """자격 증명·영상 식별자·자막 원문이 없는 canary 요약이다."""

    checks: tuple[CanaryCheck, ...]

    @property
    def verdict(self) -> str:
        if any(check.code != "ok" for check in self.checks):
            return "FAIL"
        if any(check.attempts > 1 for check in self.checks):
            return "CONDITIONAL"
        return "PASS"

    @property
    def exit_code(self) -> int:
        if self.verdict == "PASS":
            return 0
        if self.verdict == "CONDITIONAL":
            return 3
        return 2

    def to_dict(self) -> dict[str, object]:
        checked = [check for check in self.checks if check.attempts > 0]
        return {
            "provider": "dataimpulse",
            "verdict": self.verdict,
            "requested_count": len(self.checks),
            "checked_count": len(checked),
            "success_count": sum(check.code == "ok" for check in checked),
            "attempt_count": sum(check.attempts for check in checked),
            "checks": [asdict(check) for check in self.checks],
        }


class DataImpulseCanary:
    """영상별 세션과 재시도 수를 제한해 프록시 효과만 확인한다."""

    def __init__(
        self,
        settings: DataImpulseProxySettings,
        *,
        error_logger: logging.Logger | None = None,
        attempt_factory: AttemptFactory | None = None,
        monotonic_clock: MonotonicClock = time.monotonic,
    ) -> None:
        self._settings = settings
        self._error_logger = error_logger
        self._attempt_factory = attempt_factory or _create_attempt
        self._monotonic_clock = monotonic_clock

    def run(
        self,
        video_ids: Sequence[str],
        *,
        language_codes: Sequence[str] = _DEFAULT_LANGUAGE_CODES,
    ) -> DataImpulseCanaryReport:
        """로컬에서 선택한 3~5건을 순서대로 검사한다."""

        normalized_ids = _validate_video_ids(video_ids)
        checks: list[CanaryCheck] = []
        for sequence, video_id in enumerate(normalized_ids, 1):
            check = self._run_video(sequence, video_id, language_codes)
            checks.append(check)
            if check.code == TranscriptFailure.RATE_LIMITED.value:
                checks.extend(
                    CanaryCheck(
                        sequence=remaining_sequence,
                        code="skipped_after_rate_limit",
                        attempts=0,
                        duration_ms=0,
                        text_length=None,
                    )
                    for remaining_sequence in range(
                        sequence + 1, len(normalized_ids) + 1
                    )
                )
                break
        return DataImpulseCanaryReport(tuple(checks))

    def _run_video(
        self,
        sequence: int,
        video_id: str,
        language_codes: Sequence[str],
    ) -> CanaryCheck:
        started_at = self._monotonic_clock()
        final_result: TranscriptExtractionResult | None = None
        attempts = 0
        for attempts in range(1, _MAX_ATTEMPTS_PER_VIDEO + 1):
            attempt: _CanaryAttempt | None = None
            try:
                attempt = self._attempt_factory(self._settings, self._error_logger)
                final_result = attempt.extract(
                    video_id, language_codes=language_codes
                )
            except Exception:
                if self._error_logger is not None:
                    self._error_logger.exception(
                        "DataImpulse canary attempt raised an unexpected error"
                    )
                final_result = TranscriptExtractionResult.failed(
                    TranscriptFailure.TRANSIENT_ERROR
                )
            finally:
                if attempt is not None:
                    try:
                        attempt.close()
                    except Exception:
                        if self._error_logger is not None:
                            self._error_logger.exception(
                                "DataImpulse canary could not close its HTTP session"
                            )

            if final_result.is_success or final_result.failure not in _RETRYABLE_FAILURES:
                break

        duration_ms = _duration_ms(started_at, self._monotonic_clock())
        if final_result is not None and final_result.is_success:
            return CanaryCheck(
                sequence=sequence,
                code="ok",
                attempts=attempts,
                duration_ms=duration_ms,
                text_length=len(final_result.text or ""),
            )
        failure = (
            final_result.failure
            if final_result is not None
            else TranscriptFailure.TRANSIENT_ERROR
        )
        return CanaryCheck(
            sequence=sequence,
            code=(failure or TranscriptFailure.TRANSIENT_ERROR).value,
            attempts=attempts,
            duration_ms=duration_ms,
            text_length=None,
        )


class _LibraryCanaryAttempt:
    def __init__(
        self, session: Session, extractor: LibraryTranscriptExtractor
    ) -> None:
        self._session = session
        self._extractor = extractor

    def extract(
        self, video_id: str, *, language_codes: Sequence[str]
    ) -> TranscriptExtractionResult:
        return self._extractor.extract(video_id, language_codes=language_codes)

    def close(self) -> None:
        self._session.close()


def _create_attempt(
    settings: DataImpulseProxySettings,
    error_logger: logging.Logger | None,
) -> _LibraryCanaryAttempt:
    session = Session()
    session.trust_env = False
    api = YouTubeTranscriptApi(
        proxy_config=GenericProxyConfig(http_url=_proxy_url(settings)),
        http_client=session,
    )
    return _LibraryCanaryAttempt(
        session,
        LibraryTranscriptExtractor(api, error_logger=error_logger),
    )


def _proxy_url(settings: DataImpulseProxySettings) -> str:
    targeted_username = (
        f"{settings.username}__cr.{settings.country_code}"
    )
    return (
        f"http://{quote(targeted_username, safe='')}:{quote(settings.password, safe='')}"
        f"@{_DATAIMPULSE_HOST}:{_DATAIMPULSE_ROTATING_HTTP_PORT}"
    )


def _proxy_secret_values(settings: DataImpulseProxySettings) -> tuple[str, ...]:
    targeted_username = f"{settings.username}__cr.{settings.country_code}"
    return (
        settings.username,
        settings.password,
        targeted_username,
        quote(settings.username, safe=""),
        quote(settings.password, safe=""),
        quote(targeted_username, safe=""),
        _proxy_url(settings),
    )


def _load_video_ids(path: Path) -> tuple[str, ...]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise CanaryConfigurationError(
            "canary video file could not be read"
        ) from error
    except json.JSONDecodeError as error:
        raise CanaryConfigurationError(
            "canary video file must contain valid JSON"
        ) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("video_ids"), list):
        raise CanaryConfigurationError(
            "canary video file must contain a video_ids array"
        )
    return _validate_video_ids(payload["video_ids"])


def _validate_video_ids(video_ids: Sequence[object]) -> tuple[str, ...]:
    if isinstance(video_ids, (str, bytes)) or not isinstance(video_ids, Sequence):
        raise CanaryConfigurationError("video_ids must be an array")
    if not _MIN_VIDEO_COUNT <= len(video_ids) <= _MAX_VIDEO_COUNT:
        raise CanaryConfigurationError("video_ids must contain between 3 and 5 entries")

    normalized: list[str] = []
    for video_id in video_ids:
        if not isinstance(video_id, str) or not _VIDEO_ID_PATTERN.fullmatch(video_id):
            raise CanaryConfigurationError(
                "every canary video ID must be an 11-character YouTube ID"
            )
        normalized.append(video_id)
    if len(set(normalized)) != len(normalized):
        raise CanaryConfigurationError("canary video IDs must not contain duplicates")
    return tuple(normalized)


def _duration_ms(started_at: float, finished_at: float) -> int:
    return max(0, round((finished_at - started_at) * 1000))


def _default_error_log_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (
            Path(local_app_data)
            / "Unsponsor"
            / "logs"
            / "dataimpulse-canary-errors.log"
        )
    return Path.cwd() / ".unsponsor" / "logs" / "dataimpulse-canary-errors.log"


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a bounded youtube-transcript-api canary through DataImpulse."
    )
    parser.add_argument(
        "--video-id-file",
        required=True,
        type=Path,
        help="Git에서 제외된 3~5개 검사 영상 ID JSON",
    )
    parser.add_argument(
        "--error-log",
        type=Path,
        default=_default_error_log_path(),
        help="자격 증명을 마스킹한 상세 오류의 로컬 전용 파일",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """환경과 로컬 입력을 검증한 뒤 안전한 JSON 한 줄만 출력한다."""

    arguments = _parse_arguments(argv)
    repository_root = Path(__file__).resolve().parents[2]
    try:
        settings = load_dataimpulse_proxy_settings(repository_root)
        video_ids = _load_video_ids(arguments.video_id_file)
    except (ConfigurationError, CanaryConfigurationError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    error_logger = configure_restricted_error_log(
        arguments.error_log,
        secret_values=(*_proxy_secret_values(settings), *video_ids),
    )
    report = DataImpulseCanary(settings, error_logger=error_logger).run(video_ids)
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

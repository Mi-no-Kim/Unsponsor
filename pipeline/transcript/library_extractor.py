"""비공식 라이브러리로 영상 자막을 확보하는 1차 추출기다."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal, ROUND_HALF_UP
import logging
import math
from typing import Protocol

from common.restricted_error_log import transcript_error_logger
from transcript.model import (
    TranscriptExtractionResult,
    TranscriptFailure,
    TranscriptSegment,
    normalize_segment_text,
)
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    YouTubeDataUnparsable,
    YouTubeRequestFailed,
)


class TranscriptApi(Protocol):
    """youtube-transcript-api가 제공하는 필요한 최소 경계다."""

    def fetch(self, video_id: str, languages: Sequence[str]) -> Iterable[object]:
        """영상 ID의 선택 가능한 자막 조각을 반환한다."""


class _InvalidTranscriptResponse(ValueError):
    """라이브러리 응답이 자막 조각으로 정규화될 수 없음을 나타낸다."""


_DEFAULT_LANGUAGE_CODES = ("ko", "en")


class LibraryTranscriptExtractor:
    """youtube-transcript-api를 PH-1 자막 확보 흐름에 맞춰 감싼다."""

    def __init__(
        self,
        api: TranscriptApi | None = None,
        *,
        error_logger: logging.Logger | None = None,
    ) -> None:
        self._api = api if api is not None else YouTubeTranscriptApi()
        self._error_logger = (
            error_logger if error_logger is not None else transcript_error_logger()
        )

    def extract(
        self,
        video_id: str,
        *,
        language_codes: Sequence[str] = _DEFAULT_LANGUAGE_CODES,
    ) -> TranscriptExtractionResult:
        """영상의 자막을 가져와 순서를 보존한 원문으로 정규화한다."""

        try:
            response = self._api.fetch(video_id, languages=list(language_codes))
        except (NoTranscriptFound, TranscriptsDisabled):
            return TranscriptExtractionResult.failed(TranscriptFailure.NO_TRANSCRIPT)
        except YouTubeDataUnparsable:
            self._error_logger.exception(
                "youtube-transcript-api returned unparsable data; video_id=%s",
                video_id,
            )
            return TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE)
        except YouTubeRequestFailed:
            self._error_logger.exception(
                "youtube-transcript-api request failed; video_id=%s", video_id
            )
            return TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR)
        except (AgeRestricted, RequestBlocked, CouldNotRetrieveTranscript):
            self._error_logger.exception(
                "youtube-transcript-api access was restricted; video_id=%s", video_id
            )
            return TranscriptExtractionResult.failed(
                TranscriptFailure.ACCESS_RESTRICTED
            )
        except Exception:
            self._error_logger.exception(
                "youtube-transcript-api raised an unexpected error; video_id=%s",
                video_id,
            )
            return TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR)

        try:
            return TranscriptExtractionResult.succeeded(_normalize_response(response))
        except _InvalidTranscriptResponse:
            self._error_logger.exception(
                "youtube-transcript-api returned an invalid transcript; video_id=%s",
                video_id,
            )
            return TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE)


def _normalize_response(response: Iterable[object]) -> tuple[TranscriptSegment, ...]:
    """라이브러리 자막을 시간·순서를 보존한 세그먼트로 정규화한다."""

    if isinstance(response, (str, bytes)):
        raise _InvalidTranscriptResponse("transcript response must contain snippets")

    try:
        snippets = iter(response)
    except TypeError as error:
        raise _InvalidTranscriptResponse("transcript response must be iterable") from error

    normalized_snippets: list[TranscriptSegment] = []
    try:
        for snippet in snippets:
            normalized_text = normalize_segment_text(getattr(snippet, "text"))
            start_ms = _seconds_to_milliseconds(getattr(snippet, "start"))
            duration_ms = _seconds_to_milliseconds(getattr(snippet, "duration"))
            if duration_ms <= 0:
                raise _InvalidTranscriptResponse(
                    "transcript snippet duration must be positive"
                )
            normalized_snippets.append(
                TranscriptSegment(
                    sequence=len(normalized_snippets),
                    start_ms=start_ms,
                    end_ms=start_ms + duration_ms,
                    text=normalized_text,
                )
            )
    except (AttributeError, TypeError, ValueError) as error:
        raise _InvalidTranscriptResponse("transcript response has an invalid snippet") from error

    if not normalized_snippets:
        raise _InvalidTranscriptResponse("transcript response contains no text")

    return tuple(normalized_snippets)


def _seconds_to_milliseconds(value: object) -> int:
    """라이브러리의 초 단위 숫자를 반올림 규칙이 고정된 밀리초로 바꾼다."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _InvalidTranscriptResponse("transcript timestamp must be numeric")
    if not math.isfinite(value) or value < 0:
        raise _InvalidTranscriptResponse("transcript timestamp must be non-negative")
    return int(
        (Decimal(str(value)) * 1000).to_integral_value(rounding=ROUND_HALF_UP)
    )

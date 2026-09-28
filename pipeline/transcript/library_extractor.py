"""비공식 라이브러리로 영상 자막을 확보하는 1차 추출기다."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

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


class TranscriptFailure(StrEnum):
    """다음 폴백 단계가 판단할 수 있는 1차 추출 실패 유형이다."""

    NO_TRANSCRIPT = "no_transcript"
    ACCESS_RESTRICTED = "access_restricted"
    PO_TOKEN_REQUIRED = "po_token_required"
    TRANSIENT_ERROR = "transient_error"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class TranscriptExtractionResult:
    """원문 또는 원문을 포함하지 않는 안전한 실패 유형을 담는다."""

    text: str | None
    failure: TranscriptFailure | None

    def __post_init__(self) -> None:
        if self.text is None and self.failure is not None:
            return
        if isinstance(self.text, str) and self.text and self.failure is None:
            return
        raise ValueError("a transcript result must contain either text or a failure")

    @classmethod
    def succeeded(cls, text: str) -> TranscriptExtractionResult:
        return cls(text=text, failure=None)

    @classmethod
    def failed(cls, failure: TranscriptFailure) -> TranscriptExtractionResult:
        return cls(text=None, failure=failure)

    @property
    def is_success(self) -> bool:
        return self.text is not None


class TranscriptApi(Protocol):
    """youtube-transcript-api가 제공하는 필요한 최소 경계다."""

    def fetch(self, video_id: str, languages: Sequence[str]) -> Iterable[object]:
        """영상 ID의 선택 가능한 자막 조각을 반환한다."""


class _InvalidTranscriptResponse(ValueError):
    """라이브러리 응답이 자막 조각으로 정규화될 수 없음을 나타낸다."""


_DEFAULT_LANGUAGE_CODES = ("ko", "en")


class LibraryTranscriptExtractor:
    """youtube-transcript-api를 PH-1 자막 확보 흐름에 맞춰 감싼다."""

    def __init__(self, api: TranscriptApi | None = None) -> None:
        self._api = api if api is not None else YouTubeTranscriptApi()

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
            return TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE)
        except YouTubeRequestFailed:
            return TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR)
        except (AgeRestricted, RequestBlocked, CouldNotRetrieveTranscript):
            return TranscriptExtractionResult.failed(
                TranscriptFailure.ACCESS_RESTRICTED
            )
        except Exception:
            return TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR)

        try:
            return TranscriptExtractionResult.succeeded(_normalize_response(response))
        except _InvalidTranscriptResponse:
            return TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE)


def _normalize_response(response: Iterable[object]) -> str:
    """자막 조각의 줄바꿈·중복 공백을 정리하면서 순서를 유지한다."""

    if isinstance(response, (str, bytes)):
        raise _InvalidTranscriptResponse("transcript response must contain snippets")

    try:
        snippets = iter(response)
    except TypeError as error:
        raise _InvalidTranscriptResponse("transcript response must be iterable") from error

    normalized_snippets: list[str] = []
    try:
        for snippet in snippets:
            text = getattr(snippet, "text")
            if not isinstance(text, str):
                raise _InvalidTranscriptResponse("transcript snippet text must be a string")
            normalized_text = " ".join(text.split())
            if normalized_text:
                normalized_snippets.append(normalized_text)
    except (AttributeError, TypeError) as error:
        raise _InvalidTranscriptResponse("transcript response has an invalid snippet") from error

    if not normalized_snippets:
        raise _InvalidTranscriptResponse("transcript response contains no text")

    return "\n".join(normalized_snippets)

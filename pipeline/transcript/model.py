"""자막 추출 경로가 공유하는 정규화된 결과 모델이다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class TranscriptFailure(StrEnum):
    """다음 폴백 단계가 판단할 수 있는 자막 추출 실패 유형이다."""

    NO_TRANSCRIPT = "no_transcript"
    ACCESS_RESTRICTED = "access_restricted"
    RATE_LIMITED = "rate_limited"
    PO_TOKEN_REQUIRED = "po_token_required"
    TRANSIENT_ERROR = "transient_error"
    INVALID_RESPONSE = "invalid_response"


class TranscriptSource(StrEnum):
    """현재 yt-dlp 결과와 기존 library 이력을 함께 보존하는 출처다."""

    LIBRARY = "library"
    YT_DLP = "yt_dlp"


def normalize_segment_text(text: str) -> str:
    """외부 자막 조각을 한 줄의 안전한 원문 표현으로 정규화한다."""

    if not isinstance(text, str):
        raise ValueError("transcript segment text must be a string")
    normalized = " ".join(text.split())
    if not normalized:
        raise ValueError("transcript segment text must not be empty")
    return normalized


@dataclass(frozen=True)
class TranscriptSegment:
    """원문 재현과 이후 시간 기반 처리를 위한 자막 조각이다."""

    sequence: int
    start_ms: int
    end_ms: int
    text: str

    def __post_init__(self) -> None:
        for value, name in (
            (self.sequence, "sequence"),
            (self.start_ms, "start_ms"),
            (self.end_ms, "end_ms"),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"transcript segment {name} must be an integer")
        if self.sequence < 0:
            raise ValueError("transcript segment sequence must not be negative")
        if self.start_ms < 0:
            raise ValueError("transcript segment start_ms must not be negative")
        if self.end_ms <= self.start_ms:
            raise ValueError("transcript segment end_ms must be after start_ms")
        if self.text != normalize_segment_text(self.text):
            raise ValueError("transcript segment text must be normalized")


@dataclass(frozen=True)
class TranscriptExtractionResult:
    """시간 세그먼트 또는 원문을 포함하지 않는 안전한 실패 유형을 담는다."""

    segments: tuple[TranscriptSegment, ...]
    failure: TranscriptFailure | None

    def __post_init__(self) -> None:
        if self.failure is not None:
            if self.segments:
                raise ValueError("a failed transcript result must not contain segments")
            return

        if not self.segments:
            raise ValueError("a successful transcript result must contain segments")
        for expected_sequence, segment in enumerate(self.segments):
            if not isinstance(segment, TranscriptSegment):
                raise ValueError("a transcript result must contain transcript segments")
            if segment.sequence != expected_sequence:
                raise ValueError("transcript segment sequences must be contiguous")

    @classmethod
    def succeeded(
        cls, segments: Sequence[TranscriptSegment]
    ) -> TranscriptExtractionResult:
        return cls(segments=tuple(segments), failure=None)

    @classmethod
    def failed(cls, failure: TranscriptFailure) -> TranscriptExtractionResult:
        return cls(segments=(), failure=failure)

    @property
    def text(self) -> str | None:
        """기존 텍스트 소비자가 쓸 수 있게 세그먼트 원문을 재현한다."""

        if self.failure is not None:
            return None
        return "\n".join(segment.text for segment in self.segments)

    @property
    def is_success(self) -> bool:
        return self.failure is None

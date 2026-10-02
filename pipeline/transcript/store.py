"""성공한 자막의 압축 payload를 저장하고 복원하는 MySQL 경계다."""

from __future__ import annotations

import re
from dataclasses import dataclass

from collector.processing_queue_worker import ClaimedTranscriptJob
from common.mysql import MySqlConnectionFactory
from transcript.codec import (
    TRANSCRIPT_FORMAT,
    TranscriptPayloadError,
    decode_transcript_payload,
    encode_transcript_payload,
)
from transcript.model import TranscriptExtractionResult, TranscriptSource


class TranscriptStoreError(RuntimeError):
    """자막 저장을 안전하게 완료하지 못한 DB 응답을 나타낸다."""


class TranscriptClaimLostError(TranscriptStoreError):
    """성공 결과를 저장하기 전에 현재 큐 점유를 잃었음을 나타낸다."""


_YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

_SELECT_YOUTUBE_VIDEO_ID_SQL = """
SELECT youtube_video_id
FROM videos
WHERE id = %s
"""

_UPSERT_VIDEO_TRANSCRIPT_SQL = """
INSERT INTO video_transcripts (
  video_id,
  transcript_format,
  transcript_payload,
  source,
  created_at,
  updated_at
)
VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())
ON DUPLICATE KEY UPDATE
  transcript_format = %s,
  transcript_payload = %s,
  source = %s,
  updated_at = UTC_TIMESTAMP()
"""

_COMPLETE_CLAIMED_TRANSCRIPT_SQL = """
UPDATE video_processing_queue
SET stage = 'identify',
    status = 'pending',
    attempt_count = 0,
    next_attempt_at = NULL,
    started_at = NULL,
    last_error = NULL,
    updated_at = UTC_TIMESTAMP()
WHERE id = %s
  AND video_id = %s
  AND stage = 'transcript'
  AND status = 'processing'
  AND attempt_count = %s
"""

_SELECT_VIDEO_TRANSCRIPT_SQL = """
SELECT transcript_format, transcript_payload, source
FROM video_transcripts
WHERE video_id = %s
"""


@dataclass(frozen=True)
class StoredTranscript:
    """영구 payload에서 검증을 거쳐 복원한 자막과 출처다."""

    source: TranscriptSource
    result: TranscriptExtractionResult


class TranscriptStore:
    """성공 자막 저장과 현재 큐 점유의 완료를 원자적으로 처리한다."""

    def __init__(self, connection_factory: MySqlConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def find_youtube_video_id(self, video_id: int) -> str | None:
        """내부 영상 ID에 대응하는 외부 ID를 실행 중에만 조회한다."""

        _validate_video_id(video_id)
        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(_SELECT_YOUTUBE_VIDEO_ID_SQL, (video_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
            connection.close()

        if row is None:
            return None
        if (
            not isinstance(row, tuple)
            or len(row) != 1
            or not isinstance(row[0], str)
            or not _YOUTUBE_VIDEO_ID_PATTERN.fullmatch(row[0])
        ):
            raise TranscriptStoreError("database returned invalid YouTube video metadata")
        return row[0]

    def find_success(self, video_id: int) -> StoredTranscript | None:
        """저장 payload를 완전히 검증한 뒤 공통 세그먼트 모델로 복원한다."""

        _validate_video_id(video_id)
        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(_SELECT_VIDEO_TRANSCRIPT_SQL, (video_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
            connection.close()

        if row is None:
            return None
        if not isinstance(row, tuple) or len(row) != 3:
            raise TranscriptStoreError("database returned invalid transcript metadata")
        try:
            source = TranscriptSource(row[2])
            segments = decode_transcript_payload(row[0], row[1])
        except (ValueError, TranscriptPayloadError) as error:
            raise TranscriptStoreError(
                "database returned an invalid transcript payload"
            ) from error
        return StoredTranscript(
            source=source,
            result=TranscriptExtractionResult.succeeded(segments),
        )

    def replace_success(
        self,
        video_id: int,
        source: TranscriptSource,
        result: TranscriptExtractionResult,
    ) -> None:
        """성공 결과를 저장하고, 실패하면 기존 payload를 보존한다."""

        _validate_video_id(video_id)
        if not isinstance(source, TranscriptSource):
            raise ValueError("transcript source must be a supported source")
        if not result.is_success or result.text is None:
            raise ValueError("only a successful transcript result can be stored")
        payload = encode_transcript_payload(result.segments)

        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            cursor.execute(
                _UPSERT_VIDEO_TRANSCRIPT_SQL,
                _upsert_parameters(video_id, source, payload),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def complete_success(
        self,
        job: ClaimedTranscriptJob,
        source: TranscriptSource,
        result: TranscriptExtractionResult,
    ) -> None:
        """현재 점유가 유효할 때만 자막 저장과 큐 완료를 함께 commit한다."""

        _validate_video_id(job.video_id)
        if not isinstance(source, TranscriptSource):
            raise ValueError("transcript source must be a supported source")
        if not result.is_success or result.text is None:
            raise ValueError("only a successful transcript result can be stored")
        payload = encode_transcript_payload(result.segments)

        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            cursor.execute(
                _COMPLETE_CLAIMED_TRANSCRIPT_SQL,
                (job.queue_id, job.video_id, job.attempt_count),
            )
            if cursor.rowcount != 1:
                raise TranscriptClaimLostError(
                    "transcript result was rejected because its claim changed"
                )
            cursor.execute(
                _UPSERT_VIDEO_TRANSCRIPT_SQL,
                _upsert_parameters(job.video_id, source, payload),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()


def _validate_video_id(video_id: int) -> None:
    if isinstance(video_id, bool) or not isinstance(video_id, int) or video_id < 1:
        raise ValueError("video_id must be a positive integer")


def _upsert_parameters(
    video_id: int, source: TranscriptSource, payload: bytes
) -> tuple[object, ...]:
    return (
        video_id,
        TRANSCRIPT_FORMAT,
        payload,
        source.value,
        TRANSCRIPT_FORMAT,
        payload,
        source.value,
    )

"""자막 시간 세그먼트를 MySQL에 원자적으로 교체하는 저장 경계다."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from common.mysql import MySqlConnectionFactory
from transcript.model import TranscriptSegment


_DELETE_VIDEO_TRANSCRIPT_SEGMENTS_SQL = """
DELETE FROM video_transcript_segments
WHERE video_id = %s
"""

_INSERT_VIDEO_TRANSCRIPT_SEGMENT_SQL = """
INSERT INTO video_transcript_segments (
  video_id,
  sequence,
  start_ms,
  end_ms,
  text,
  created_at
)
VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP())
"""


class TranscriptSegmentStoreError(RuntimeError):
    """세그먼트 교체를 안전하게 완료하지 못한 DB 응답을 나타낸다."""


class TranscriptSegmentStore:
    """한 영상의 세그먼트 집합을 한 트랜잭션으로 최신 결과로 바꾼다."""

    def __init__(self, connection_factory: MySqlConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def replace_for_video(
        self, video_id: int, segments: Sequence[TranscriptSegment]
    ) -> None:
        """기존 행을 모두 대체하고, 실패하면 이전 집합을 보존한다."""

        normalized_segments = _validate_replacement(video_id, segments)
        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            cursor.execute(_DELETE_VIDEO_TRANSCRIPT_SEGMENTS_SQL, (video_id,))
            cursor.executemany(
                _INSERT_VIDEO_TRANSCRIPT_SEGMENT_SQL,
                [
                    (
                        video_id,
                        segment.sequence,
                        segment.start_ms,
                        segment.end_ms,
                        segment.text,
                    )
                    for segment in normalized_segments
                ],
            )
            if cursor.rowcount != len(normalized_segments):
                raise TranscriptSegmentStoreError(
                    "database could not safely insert all transcript segments"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()


def _validate_replacement(
    video_id: int, segments: Sequence[TranscriptSegment]
) -> tuple[TranscriptSegment, ...]:
    if isinstance(video_id, bool) or not isinstance(video_id, int) or video_id < 1:
        raise ValueError("video_id must be a positive integer")

    normalized_segments = tuple(segments)
    if not normalized_segments:
        raise ValueError("transcript segment replacement must not be empty")
    for expected_sequence, segment in enumerate(normalized_segments):
        if not isinstance(segment, TranscriptSegment):
            raise ValueError("transcript segment replacement has an invalid segment")
        if segment.sequence != expected_sequence:
            raise ValueError("transcript segment replacement sequences must be contiguous")
    return normalized_segments

"""성공한 자막 원문과 시간 세그먼트를 함께 저장하는 MySQL 경계다."""

from __future__ import annotations

import re
from typing import Any

from common.mysql import MySqlConnectionFactory
from transcript.model import TranscriptExtractionResult, TranscriptSource


class TranscriptStoreError(RuntimeError):
    """자막 저장을 안전하게 완료하지 못한 DB 응답을 나타낸다."""


_YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")

_SELECT_YOUTUBE_VIDEO_ID_SQL = """
SELECT youtube_video_id
FROM videos
WHERE id = %s
"""

_UPSERT_VIDEO_TRANSCRIPT_SQL = """
INSERT INTO video_transcripts (
  video_id,
  raw_text,
  source,
  created_at,
  updated_at
)
VALUES (%s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())
ON DUPLICATE KEY UPDATE
  raw_text = %s,
  source = %s,
  updated_at = UTC_TIMESTAMP()
"""

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


class TranscriptStore:
    """성공한 자막의 원문·출처·세그먼트를 같은 트랜잭션에서 교체한다."""

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

    def replace_success(
        self,
        video_id: int,
        source: TranscriptSource,
        result: TranscriptExtractionResult,
    ) -> None:
        """성공 결과를 저장하고, 실패하면 기존 원문·세그먼트를 모두 보존한다."""

        _validate_video_id(video_id)
        if not isinstance(source, TranscriptSource):
            raise ValueError("transcript source must be a supported source")
        if not result.is_success or result.text is None:
            raise ValueError("only a successful transcript result can be stored")

        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            cursor.execute(
                _UPSERT_VIDEO_TRANSCRIPT_SQL,
                (
                    video_id,
                    result.text,
                    source.value,
                    result.text,
                    source.value,
                ),
            )
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
                    for segment in result.segments
                ],
            )
            if cursor.rowcount != len(result.segments):
                raise TranscriptStoreError(
                    "database could not safely insert all transcript segments"
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

"""동기화된 영상을 다음 파이프라인 단계의 처리 큐에 등록한다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from common.mysql import MySqlConnectionFactory


class ProcessingQueueRegistrationError(RuntimeError):
    """처리 큐를 안전하게 등록할 수 없는 데이터베이스 응답을 나타낸다."""


@dataclass(frozen=True)
class ProcessingQueueRegistrationResult:
    """선택된 영상의 처리 큐 등록 결과다."""

    created_count: int
    existing_count: int
    missing_video_count: int

    @property
    def registered_video_count(self) -> int:
        """videos 테이블에서 찾은 선택 영상 수다."""

        return self.created_count + self.existing_count


@dataclass(frozen=True)
class RegisteredVideo:
    id: int
    youtube_video_id: str


_SELECT_REGISTERED_VIDEOS_SQL = """
SELECT id, youtube_video_id
FROM videos
WHERE youtube_video_id IN ({placeholders})
"""

_SELECT_QUEUED_VIDEO_IDS_SQL = """
SELECT video_id
FROM video_processing_queue
WHERE video_id IN ({placeholders})
"""

_INSERT_PROCESSING_QUEUE_SQL = """
INSERT INTO video_processing_queue (
    video_id,
    stage,
    status,
    attempt_count,
    next_attempt_at,
    started_at,
    last_error,
    created_at,
    updated_at
)
VALUES (%s, 'transcript', 'pending', 0, NULL, NULL, NULL, UTC_TIMESTAMP(), UTC_TIMESTAMP())
ON DUPLICATE KEY UPDATE
    video_id = video_id
"""


class ProcessingQueueRegistrar:
    """videos에 저장된 선택 영상을 처리 큐에 한 번만 등록한다."""

    def __init__(self, connection_factory: MySqlConnectionFactory) -> None:
        self._connection_factory = connection_factory

    def register(
        self, youtube_video_ids: Sequence[str]
    ) -> ProcessingQueueRegistrationResult:
        """아직 큐가 없는 영상에만 transcript/pending 행을 만든다.

        기존 큐에는 UPDATE를 실행하지 않아 진행 단계, 상태, 시도 횟수, 오류
        정보가 재실행으로 바뀌지 않는다.
        """

        requested_ids = tuple(dict.fromkeys(youtube_video_ids))
        if not requested_ids:
            return ProcessingQueueRegistrationResult(
                created_count=0,
                existing_count=0,
                missing_video_count=0,
            )

        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            videos_by_youtube_id = _fetch_registered_videos(cursor, requested_ids)
            registered_videos = tuple(
                videos_by_youtube_id[video_id]
                for video_id in requested_ids
                if video_id in videos_by_youtube_id
            )
            queued_video_ids = _fetch_queued_video_ids(
                cursor, tuple(video.id for video in registered_videos)
            )

            created_count = 0
            existing_count = len(queued_video_ids)
            for video in registered_videos:
                if video.id in queued_video_ids:
                    continue

                cursor.execute(_INSERT_PROCESSING_QUEUE_SQL, (video.id,))
                if cursor.rowcount == 1:
                    created_count += 1
                elif cursor.rowcount == 0:
                    # 다른 실행이 먼저 등록했더라도 기존 상태를 덮어쓰지 않는다.
                    existing_count += 1
                else:
                    raise ProcessingQueueRegistrationError(
                        "database reported an unexpected processing queue insert result"
                    )

            if created_count:
                connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

        return ProcessingQueueRegistrationResult(
            created_count=created_count,
            existing_count=existing_count,
            missing_video_count=len(requested_ids) - len(registered_videos),
        )


def _fetch_registered_videos(
    cursor: Any, youtube_video_ids: Sequence[str]
) -> dict[str, RegisteredVideo]:
    placeholders = ", ".join("%s" for _ in youtube_video_ids)
    cursor.execute(
        _SELECT_REGISTERED_VIDEOS_SQL.format(placeholders=placeholders),
        tuple(youtube_video_ids),
    )

    videos_by_youtube_id: dict[str, RegisteredVideo] = {}
    for row in cursor.fetchall():
        video = _parse_registered_video(row)
        if video.youtube_video_id not in youtube_video_ids:
            raise ProcessingQueueRegistrationError(
                "database returned a video outside the requested selection"
            )
        if video.youtube_video_id in videos_by_youtube_id:
            raise ProcessingQueueRegistrationError(
                "database returned duplicate registered video metadata"
            )
        videos_by_youtube_id[video.youtube_video_id] = video
    return videos_by_youtube_id


def _parse_registered_video(row: Any) -> RegisteredVideo:
    if (
        not isinstance(row, tuple)
        or len(row) != 2
        or isinstance(row[0], bool)
        or not isinstance(row[0], int)
        or not isinstance(row[1], str)
    ):
        raise ProcessingQueueRegistrationError(
            "database returned invalid registered video metadata"
        )
    return RegisteredVideo(id=row[0], youtube_video_id=row[1])


def _fetch_queued_video_ids(cursor: Any, video_ids: Sequence[int]) -> set[int]:
    if not video_ids:
        return set()

    placeholders = ", ".join("%s" for _ in video_ids)
    cursor.execute(
        _SELECT_QUEUED_VIDEO_IDS_SQL.format(placeholders=placeholders), tuple(video_ids)
    )

    queued_video_ids: set[int] = set()
    valid_video_ids = set(video_ids)
    for row in cursor.fetchall():
        if (
            not isinstance(row, tuple)
            or len(row) != 1
            or isinstance(row[0], bool)
            or not isinstance(row[0], int)
            or row[0] not in valid_video_ids
        ):
            raise ProcessingQueueRegistrationError(
                "database returned invalid processing queue metadata"
            )
        if row[0] in queued_video_ids:
            raise ProcessingQueueRegistrationError(
                "database returned duplicate processing queue metadata"
            )
        queued_video_ids.add(row[0])
    return queued_video_ids

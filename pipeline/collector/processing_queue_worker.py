"""자막 단계의 처리 큐 상태 전이와 멈춤 복구를 담당한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from common.config import QueueSettings
from common.mysql import MySqlConnectionFactory


class ProcessingQueueWorkerError(RuntimeError):
    """처리 큐의 상태를 안전하게 전이할 수 없는 DB 응답을 나타낸다."""


_FAILURE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class ClaimedTranscriptJob:
    """짧은 트랜잭션에서 점유를 마친 transcript 단계 작업이다."""

    queue_id: int
    video_id: int
    attempt_count: int


@dataclass(frozen=True)
class QueueFailure:
    """원문이나 비공개 식별자를 담지 않는 처리 실패 코드다."""

    code: str

    def __post_init__(self) -> None:
        if not _FAILURE_CODE_PATTERN.fullmatch(self.code):
            raise ValueError("queue failure code must be a safe identifier")


@dataclass(frozen=True)
class FailureTransition:
    """실패 처리 뒤의 상태를 호출자가 안전하게 요약할 수 있게 한다."""

    queue_id: int
    attempt_count: int
    status: str


_SELECT_NEXT_TRANSCRIPT_JOB_SQL = """
SELECT id, video_id, attempt_count
FROM video_processing_queue
WHERE stage = 'transcript'
  AND status = 'pending'
  AND (next_attempt_at IS NULL OR next_attempt_at <= UTC_TIMESTAMP())
ORDER BY id
LIMIT 1
FOR UPDATE SKIP LOCKED
"""

_MARK_TRANSCRIPT_PROCESSING_SQL = """
UPDATE video_processing_queue
SET status = 'processing',
    started_at = UTC_TIMESTAMP(),
    updated_at = UTC_TIMESTAMP()
WHERE id = %s
  AND stage = 'transcript'
  AND status = 'pending'
"""

_COMPLETE_TRANSCRIPT_SQL = """
UPDATE video_processing_queue
SET stage = 'identify',
    status = 'pending',
    attempt_count = 0,
    next_attempt_at = NULL,
    started_at = NULL,
    last_error = NULL,
    updated_at = UTC_TIMESTAMP()
WHERE id = %s
  AND stage = 'transcript'
  AND status = 'processing'
"""

_FAIL_TRANSCRIPT_FINAL_SQL = """
UPDATE video_processing_queue
SET status = 'failed',
    attempt_count = %s,
    next_attempt_at = NULL,
    started_at = NULL,
    last_error = %s,
    updated_at = UTC_TIMESTAMP()
WHERE id = %s
  AND stage = 'transcript'
  AND status = 'processing'
  AND attempt_count = %s
"""

_SELECT_STUCK_TRANSCRIPT_JOB_SQL = """
SELECT id, video_id, attempt_count
FROM video_processing_queue
WHERE stage = 'transcript'
  AND status = 'processing'
  AND started_at IS NOT NULL
  AND started_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL {stale_after_seconds} SECOND)
ORDER BY started_at, id
LIMIT 1
FOR UPDATE SKIP LOCKED
"""


class ProcessingQueueWorker:
    """D-019의 transcript 단계 큐 상태 전이를 짧은 트랜잭션으로 처리한다."""

    def __init__(
        self,
        connection_factory: MySqlConnectionFactory,
        settings: QueueSettings,
    ) -> None:
        self._connection_factory = connection_factory
        self._settings = settings

    def claim_next_transcript(self) -> ClaimedTranscriptJob | None:
        """실행 가능한 transcript 작업 하나만 점유한다.

        실제 자막 처리는 이 메서드가 반환된 뒤, DB 트랜잭션 밖에서 실행한다.
        """

        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            cursor.execute(_SELECT_NEXT_TRANSCRIPT_JOB_SQL)
            row = cursor.fetchone()
            if row is None:
                connection.commit()
                return None

            job = _parse_claimed_job(row)
            cursor.execute(_MARK_TRANSCRIPT_PROCESSING_SQL, (job.queue_id,))
            _require_single_row(cursor, "claim transcript job")
            connection.commit()
            return job
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def complete_transcript(self, job: ClaimedTranscriptJob) -> None:
        """자막 저장이 끝난 작업을 identify/pending으로 넘긴다."""

        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            cursor.execute(_COMPLETE_TRANSCRIPT_SQL, (job.queue_id,))
            _require_single_row(cursor, "complete transcript job")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def fail_transcript(
        self, job: ClaimedTranscriptJob, failure: QueueFailure
    ) -> FailureTransition:
        """실패한 작업을 백오프 재시도 또는 최종 실패로 전환한다."""

        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            transition = self._fail_claimed_job(cursor, job, failure)
            connection.commit()
            return transition
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def recover_one_stuck_transcript(self) -> FailureTransition | None:
        """30분 이상 멈춘 작업 하나를 일반 실패 처리와 같은 정책으로 복구한다."""

        connection = self._connection_factory.connect()
        cursor = connection.cursor()
        try:
            connection.start_transaction()
            cursor.execute(
                _SELECT_STUCK_TRANSCRIPT_JOB_SQL.format(
                    stale_after_seconds=self._settings.stale_after_seconds
                )
            )
            row = cursor.fetchone()
            if row is None:
                connection.commit()
                return None

            transition = self._fail_claimed_job(
                cursor, _parse_claimed_job(row), QueueFailure("worker_stalled")
            )
            connection.commit()
            return transition
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def _fail_claimed_job(
        self, cursor: Any, job: ClaimedTranscriptJob, failure: QueueFailure
    ) -> FailureTransition:
        next_attempt_count = job.attempt_count + 1
        if next_attempt_count >= self._settings.max_attempts:
            cursor.execute(
                _FAIL_TRANSCRIPT_FINAL_SQL,
                (next_attempt_count, failure.code, job.queue_id, job.attempt_count),
            )
            _require_single_row(cursor, "fail transcript job")
            return FailureTransition(
                queue_id=job.queue_id,
                attempt_count=next_attempt_count,
                status="failed",
            )

        retry_delay_seconds = self._retry_delay_seconds(next_attempt_count)
        cursor.execute(
            _retry_transcript_sql(retry_delay_seconds),
            (next_attempt_count, failure.code, job.queue_id, job.attempt_count),
        )
        _require_single_row(cursor, "retry transcript job")
        return FailureTransition(
            queue_id=job.queue_id,
            attempt_count=next_attempt_count,
            status="pending",
        )

    def _retry_delay_seconds(self, failed_attempt_count: int) -> int:
        return self._settings.retry_backoff_base_seconds * (
            2 ** (failed_attempt_count - 1)
        )


def _retry_transcript_sql(retry_delay_seconds: int) -> str:
    return f"""
UPDATE video_processing_queue
SET status = 'pending',
    attempt_count = %s,
    next_attempt_at = DATE_ADD(UTC_TIMESTAMP(), INTERVAL {retry_delay_seconds} SECOND),
    started_at = NULL,
    last_error = %s,
    updated_at = UTC_TIMESTAMP()
WHERE id = %s
  AND stage = 'transcript'
  AND status = 'processing'
  AND attempt_count = %s
"""


def _parse_claimed_job(row: Any) -> ClaimedTranscriptJob:
    if (
        not isinstance(row, tuple)
        or len(row) != 3
        or any(isinstance(value, bool) or not isinstance(value, int) for value in row)
        or row[0] < 1
        or row[1] < 1
        or row[2] < 0
    ):
        raise ProcessingQueueWorkerError("database returned invalid transcript queue row")
    return ClaimedTranscriptJob(queue_id=row[0], video_id=row[1], attempt_count=row[2])


def _require_single_row(cursor: Any, action: str) -> None:
    if cursor.rowcount != 1:
        raise ProcessingQueueWorkerError(
            f"database could not safely {action} because its state changed"
        )

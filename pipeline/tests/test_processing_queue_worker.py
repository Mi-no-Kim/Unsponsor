from __future__ import annotations

import unittest
from unittest.mock import Mock

from collector.processing_queue_worker import (
    ClaimedTranscriptJob,
    ProcessingQueueWorker,
    QueueFailure,
    _FAIL_TRANSCRIPT_FINAL_SQL,
    _MARK_TRANSCRIPT_PROCESSING_SQL,
    _SELECT_NEXT_TRANSCRIPT_JOB_SQL,
    _SELECT_STUCK_TRANSCRIPT_JOB_SQL,
    _retry_transcript_sql,
)
from common.config import QueueSettings


class ProcessingQueueWorkerTests(unittest.TestCase):
    def test_claim_next_transcript_marks_one_pending_job_as_processing(self) -> None:
        cursor = Mock()
        cursor.fetchone.return_value = (101, 201, 0)
        connection, factory = _connection_factory(cursor)
        worker = ProcessingQueueWorker(factory, _queue_settings())

        job = worker.claim_next_transcript()

        self.assertEqual(job, ClaimedTranscriptJob(101, 201, 0))
        self.assertEqual(
            cursor.execute.call_args_list,
            [
                ((_SELECT_NEXT_TRANSCRIPT_JOB_SQL,),),
                ((_MARK_TRANSCRIPT_PROCESSING_SQL, (101,)),),
            ],
        )
        connection.start_transaction.assert_called_once_with()
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()
        self.assertIn("LIMIT 1", _SELECT_NEXT_TRANSCRIPT_JOB_SQL)
        self.assertIn("FOR UPDATE SKIP LOCKED", _SELECT_NEXT_TRANSCRIPT_JOB_SQL)

    def test_claim_next_transcript_skips_when_no_job_is_ready(self) -> None:
        cursor = Mock()
        cursor.fetchone.return_value = None
        connection, factory = _connection_factory(cursor)
        worker = ProcessingQueueWorker(factory, _queue_settings())

        job = worker.claim_next_transcript()

        self.assertIsNone(job)
        cursor.execute.assert_called_once_with(_SELECT_NEXT_TRANSCRIPT_JOB_SQL)
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()

    def test_fail_transcript_retries_after_the_configured_exponential_backoff(self) -> None:
        cursor = Mock()
        connection, factory = _connection_factory(cursor)
        worker = ProcessingQueueWorker(factory, _queue_settings())

        result = worker.fail_transcript(
            ClaimedTranscriptJob(101, 201, 0), QueueFailure("library_unavailable")
        )

        self.assertEqual(result.queue_id, 101)
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(result.status, "pending")
        cursor.execute.assert_called_once_with(
            _retry_transcript_sql(300),
            (1, "library_unavailable", 101, 0),
        )
        connection.commit.assert_called_once_with()
        self.assertIn("FOR UPDATE SKIP LOCKED", _SELECT_STUCK_TRANSCRIPT_JOB_SQL)
        self.assertIn("INTERVAL 300 SECOND", _retry_transcript_sql(300))
        self.assertIn("status = 'pending'", _retry_transcript_sql(300))

    def test_fail_transcript_doubles_the_backoff_after_a_second_failure(self) -> None:
        cursor = Mock()
        connection, factory = _connection_factory(cursor)
        worker = ProcessingQueueWorker(factory, _queue_settings())

        result = worker.fail_transcript(
            ClaimedTranscriptJob(101, 201, 1), QueueFailure("library_unavailable")
        )

        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(result.status, "pending")
        cursor.execute.assert_called_once_with(
            _retry_transcript_sql(600),
            (2, "library_unavailable", 101, 1),
        )

    def test_fail_transcript_marks_the_third_attempt_as_failed(self) -> None:
        cursor = Mock()
        connection, factory = _connection_factory(cursor)
        worker = ProcessingQueueWorker(factory, _queue_settings())

        result = worker.fail_transcript(
            ClaimedTranscriptJob(101, 201, 2), QueueFailure("library_unavailable")
        )

        self.assertEqual(result.attempt_count, 3)
        self.assertEqual(result.status, "failed")
        cursor.execute.assert_called_once_with(
            _FAIL_TRANSCRIPT_FINAL_SQL,
            (3, "library_unavailable", 101, 2),
        )
        self.assertIn("status = 'failed'", _FAIL_TRANSCRIPT_FINAL_SQL)
        self.assertIn("next_attempt_at = NULL", _FAIL_TRANSCRIPT_FINAL_SQL)

    def test_recover_one_stuck_transcript_reuses_retry_policy(self) -> None:
        cursor = Mock()
        cursor.fetchone.return_value = (101, 201, 0)
        connection, factory = _connection_factory(cursor)
        worker = ProcessingQueueWorker(factory, _queue_settings())

        result = worker.recover_one_stuck_transcript()

        self.assertEqual(result.status, "pending")
        self.assertEqual(
            cursor.execute.call_args_list,
            [
                (
                    (
                        _SELECT_STUCK_TRANSCRIPT_JOB_SQL.format(
                            stale_after_seconds=1800
                        ),
                    ),
                ),
                ((_retry_transcript_sql(300), (1, "worker_stalled", 101, 0)),),
            ],
        )
        connection.commit.assert_called_once_with()

    def test_recover_one_stuck_transcript_returns_none_when_no_job_is_stale(self) -> None:
        cursor = Mock()
        cursor.fetchone.return_value = None
        connection, factory = _connection_factory(cursor)
        worker = ProcessingQueueWorker(factory, _queue_settings())

        result = worker.recover_one_stuck_transcript()

        self.assertIsNone(result)
        cursor.execute.assert_called_once_with(
            _SELECT_STUCK_TRANSCRIPT_JOB_SQL.format(stale_after_seconds=1800)
        )
        connection.commit.assert_called_once_with()

    def test_rejects_failure_codes_that_could_contain_sensitive_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe identifier"):
            QueueFailure("video-id: private transcript")


def _connection_factory(cursor: Mock) -> tuple[Mock, Mock]:
    cursor.rowcount = 1
    connection = Mock()
    connection.cursor.return_value = cursor
    factory = Mock()
    factory.connect.return_value = connection
    return connection, factory


def _queue_settings() -> QueueSettings:
    return QueueSettings(
        max_attempts=3,
        retry_backoff_base_seconds=300,
        stale_after_seconds=1800,
    )

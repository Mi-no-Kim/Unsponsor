from __future__ import annotations

import unittest
from unittest.mock import Mock

from collector.processing_queue_worker import ClaimedTranscriptJob
from transcript.codec import TRANSCRIPT_FORMAT, decode_transcript_payload
from transcript.model import (
    TranscriptExtractionResult,
    TranscriptFailure,
    TranscriptSegment,
    TranscriptSource,
)
from transcript.store import (
    _COMPLETE_CLAIMED_TRANSCRIPT_SQL,
    _SELECT_VIDEO_TRANSCRIPT_SQL,
    _SELECT_YOUTUBE_VIDEO_ID_SQL,
    _UPSERT_VIDEO_TRANSCRIPT_SQL,
    StoredTranscript,
    TranscriptClaimLostError,
    TranscriptStore,
    TranscriptStoreError,
)


class TranscriptStoreTests(unittest.TestCase):
    def test_finds_a_valid_youtube_video_id_without_exposing_it_elsewhere(self) -> None:
        cursor = Mock()
        cursor.fetchone.return_value = ("video000001",)
        _, factory = _connection_factory(cursor)

        actual = TranscriptStore(factory).find_youtube_video_id(101)

        self.assertEqual(actual, "video000001")
        cursor.execute.assert_called_once_with(_SELECT_YOUTUBE_VIDEO_ID_SQL, (101,))

    def test_replaces_format_payload_and_source_in_one_transaction(self) -> None:
        cursor = Mock()
        connection, factory = _connection_factory(cursor)
        result = _successful_result()

        TranscriptStore(factory).replace_success(101, TranscriptSource.YT_DLP, result)

        connection.start_transaction.assert_called_once_with()
        cursor.execute.assert_called_once()
        sql, parameters = cursor.execute.call_args.args
        self.assertEqual(sql, _UPSERT_VIDEO_TRANSCRIPT_SQL)
        self.assertEqual(parameters[0:2], (101, TRANSCRIPT_FORMAT))
        self.assertEqual(parameters[3:5], ("yt_dlp", TRANSCRIPT_FORMAT))
        self.assertEqual(parameters[6], "yt_dlp")
        self.assertEqual(parameters[2], parameters[5])
        self.assertEqual(
            decode_transcript_payload(TRANSCRIPT_FORMAT, parameters[2]),
            result.segments,
        )
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()

    def test_finds_and_decodes_a_stored_success(self) -> None:
        cursor = Mock()
        result = _successful_result()
        _, factory = _connection_factory(cursor)
        TranscriptStore(factory).replace_success(101, TranscriptSource.LIBRARY, result)
        payload = cursor.execute.call_args.args[1][2]
        cursor.reset_mock()
        cursor.fetchone.return_value = (TRANSCRIPT_FORMAT, payload, "library")

        actual = TranscriptStore(factory).find_success(101)

        self.assertEqual(actual, StoredTranscript(TranscriptSource.LIBRARY, result))
        cursor.execute.assert_called_once_with(_SELECT_VIDEO_TRANSCRIPT_SQL, (101,))

    def test_completes_owned_job_and_replaces_payload_in_one_transaction(self) -> None:
        cursor = Mock()
        connection, factory = _connection_factory(cursor)
        result = _successful_result()
        job = ClaimedTranscriptJob(11, 101, 2)

        TranscriptStore(factory).complete_success(
            job, TranscriptSource.YT_DLP, result
        )

        connection.start_transaction.assert_called_once_with()
        self.assertEqual(len(cursor.execute.call_args_list), 2)
        self.assertEqual(
            cursor.execute.call_args_list[0].args,
            (_COMPLETE_CLAIMED_TRANSCRIPT_SQL, (11, 101, 2)),
        )
        sql, parameters = cursor.execute.call_args_list[1].args
        self.assertEqual(sql, _UPSERT_VIDEO_TRANSCRIPT_SQL)
        self.assertEqual(parameters[0:2], (101, TRANSCRIPT_FORMAT))
        self.assertEqual(parameters[3:5], ("yt_dlp", TRANSCRIPT_FORMAT))
        self.assertEqual(parameters[6], "yt_dlp")
        self.assertEqual(
            decode_transcript_payload(TRANSCRIPT_FORMAT, parameters[2]),
            result.segments,
        )
        self.assertEqual(parameters[2], parameters[5])
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        self.assertIn("attempt_count = %s", _COMPLETE_CLAIMED_TRANSCRIPT_SQL)
        self.assertIn("video_id = %s", _COMPLETE_CLAIMED_TRANSCRIPT_SQL)

    def test_rejects_a_lost_claim_before_replacing_the_payload(self) -> None:
        cursor = Mock()
        cursor.rowcount = 0
        connection, factory = _connection_factory(cursor)
        job = ClaimedTranscriptJob(11, 101, 0)

        with self.assertRaisesRegex(TranscriptClaimLostError, "claim changed"):
            TranscriptStore(factory).complete_success(
                job, TranscriptSource.LIBRARY, _successful_result()
            )

        cursor.execute.assert_called_once_with(
            _COMPLETE_CLAIMED_TRANSCRIPT_SQL, (11, 101, 0)
        )
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()

    def test_rolls_back_queue_completion_when_the_payload_upsert_fails(self) -> None:
        cursor = Mock()
        cursor.rowcount = 1
        cursor.execute.side_effect = (None, RuntimeError("write failed"))
        connection, factory = _connection_factory(cursor)

        with self.assertRaises(RuntimeError):
            TranscriptStore(factory).complete_success(
                ClaimedTranscriptJob(11, 101, 0),
                TranscriptSource.LIBRARY,
                _successful_result(),
            )

        self.assertEqual(cursor.execute.call_count, 2)
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()

    def test_rejects_an_invalid_stored_payload_without_exposing_contents(self) -> None:
        cursor = Mock()
        cursor.fetchone.return_value = (
            TRANSCRIPT_FORMAT,
            b"private transcript",
            "library",
        )
        _, factory = _connection_factory(cursor)

        with self.assertRaisesRegex(
            TranscriptStoreError, "invalid transcript payload"
        ) as caught:
            TranscriptStore(factory).find_success(101)

        self.assertNotIn("private transcript", str(caught.exception))

    def test_rolls_back_when_the_upsert_fails(self) -> None:
        cursor = Mock()
        cursor.execute.side_effect = RuntimeError("write failed")
        connection, factory = _connection_factory(cursor)

        with self.assertRaises(RuntimeError):
            TranscriptStore(factory).replace_success(
                101, TranscriptSource.LIBRARY, _successful_result()
            )

        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()

    def test_rejects_a_failed_result_before_opening_a_connection(self) -> None:
        factory = Mock()

        with self.assertRaisesRegex(ValueError, "successful transcript"):
            TranscriptStore(factory).replace_success(
                101,
                TranscriptSource.LIBRARY,
                TranscriptExtractionResult.failed(TranscriptFailure.NO_TRANSCRIPT),
            )

        factory.connect.assert_not_called()


def _connection_factory(cursor: Mock) -> tuple[Mock, Mock]:
    if not isinstance(cursor.rowcount, int):
        cursor.rowcount = 1
    connection = Mock()
    connection.cursor.return_value = cursor
    factory = Mock()
    factory.connect.return_value = connection
    return connection, factory


def _successful_result() -> TranscriptExtractionResult:
    return TranscriptExtractionResult.succeeded(
        (
            TranscriptSegment(0, 0, 1000, "alpha"),
            TranscriptSegment(1, 1000, 2000, "beta"),
        )
    )


if __name__ == "__main__":
    unittest.main()

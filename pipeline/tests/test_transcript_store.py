from __future__ import annotations

import unittest
from unittest.mock import Mock

from transcript.model import (
    TranscriptExtractionResult,
    TranscriptFailure,
    TranscriptSegment,
    TranscriptSource,
)
from transcript.store import (
    _DELETE_VIDEO_TRANSCRIPT_SEGMENTS_SQL,
    _INSERT_VIDEO_TRANSCRIPT_SEGMENT_SQL,
    _SELECT_YOUTUBE_VIDEO_ID_SQL,
    _UPSERT_VIDEO_TRANSCRIPT_SQL,
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

    def test_replaces_raw_text_source_and_segments_in_one_transaction(self) -> None:
        cursor = Mock()
        cursor.rowcount = 2
        connection, factory = _connection_factory(cursor)
        result = _successful_result()

        TranscriptStore(factory).replace_success(101, TranscriptSource.YT_DLP, result)

        connection.start_transaction.assert_called_once_with()
        self.assertEqual(
            cursor.execute.call_args_list,
            [
                (
                    (
                        _UPSERT_VIDEO_TRANSCRIPT_SQL,
                        (101, "alpha\nbeta", "yt_dlp", "alpha\nbeta", "yt_dlp"),
                    ),
                ),
                ((_DELETE_VIDEO_TRANSCRIPT_SEGMENTS_SQL, (101,)),),
            ],
        )
        cursor.executemany.assert_called_once_with(
            _INSERT_VIDEO_TRANSCRIPT_SEGMENT_SQL,
            [(101, 0, 0, 1000, "alpha"), (101, 1, 1000, 2000, "beta")],
        )
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()

    def test_rolls_back_when_not_all_segments_are_stored(self) -> None:
        cursor = Mock()
        cursor.rowcount = 1
        connection, factory = _connection_factory(cursor)

        with self.assertRaises(TranscriptStoreError):
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

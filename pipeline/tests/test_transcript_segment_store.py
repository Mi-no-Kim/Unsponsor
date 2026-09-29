from __future__ import annotations

from unittest.mock import Mock
import unittest

from transcript.model import TranscriptSegment
from transcript.segment_store import (
    _DELETE_VIDEO_TRANSCRIPT_SEGMENTS_SQL,
    _INSERT_VIDEO_TRANSCRIPT_SEGMENT_SQL,
    TranscriptSegmentStore,
    TranscriptSegmentStoreError,
)


class TranscriptSegmentStoreTests(unittest.TestCase):
    def test_replace_for_video_replaces_all_segments_in_one_transaction(self) -> None:
        cursor = Mock()
        cursor.rowcount = 2
        connection, factory = _connection_factory(cursor)

        TranscriptSegmentStore(factory).replace_for_video(101, _segments())

        connection.start_transaction.assert_called_once_with()
        cursor.execute.assert_called_once_with(
            _DELETE_VIDEO_TRANSCRIPT_SEGMENTS_SQL, (101,)
        )
        cursor.executemany.assert_called_once_with(
            _INSERT_VIDEO_TRANSCRIPT_SEGMENT_SQL,
            [(101, 0, 0, 1000, "alpha"), (101, 1, 1000, 2000, "beta")],
        )
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_replace_for_video_rolls_back_when_not_all_segments_are_inserted(self) -> None:
        cursor = Mock()
        cursor.rowcount = 1
        connection, factory = _connection_factory(cursor)

        with self.assertRaises(TranscriptSegmentStoreError):
            TranscriptSegmentStore(factory).replace_for_video(101, _segments())

        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_replace_for_video_rejects_non_contiguous_sequences_before_opening_a_connection(
        self,
    ) -> None:
        factory = Mock()

        with self.assertRaisesRegex(ValueError, "sequences must be contiguous"):
            TranscriptSegmentStore(factory).replace_for_video(
                101, (TranscriptSegment(1, 0, 1000, "alpha"),)
            )

        factory.connect.assert_not_called()


def _connection_factory(cursor: Mock) -> tuple[Mock, Mock]:
    connection = Mock()
    connection.cursor.return_value = cursor
    factory = Mock()
    factory.connect.return_value = connection
    return connection, factory


def _segments() -> tuple[TranscriptSegment, ...]:
    return (
        TranscriptSegment(0, 0, 1000, "alpha"),
        TranscriptSegment(1, 1000, 2000, "beta"),
    )

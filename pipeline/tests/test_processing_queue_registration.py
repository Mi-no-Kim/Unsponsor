from __future__ import annotations

import unittest
from unittest.mock import Mock

from collector.processing_queue_registration import (
    ProcessingQueueRegistrar,
    ProcessingQueueRegistrationError,
    _INSERT_PROCESSING_QUEUE_SQL,
    _SELECT_QUEUED_VIDEO_IDS_SQL,
    _SELECT_REGISTERED_VIDEOS_SQL,
)


class ProcessingQueueRegistrarTests(unittest.TestCase):
    def test_register_creates_transcript_pending_queues_for_registered_videos(self) -> None:
        cursor = Mock()
        cursor.rowcount = 1
        cursor.fetchall.side_effect = [
            [(101, "video000001"), (102, "video000002")],
            [],
        ]
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        registrar = ProcessingQueueRegistrar(factory)

        result = registrar.register(("video000001", "video000002"))

        self.assertEqual(result.created_count, 2)
        self.assertEqual(result.existing_count, 0)
        self.assertEqual(result.missing_video_count, 0)
        self.assertEqual(result.registered_video_count, 2)
        self.assertEqual(
            cursor.execute.call_args_list,
            [
                (
                    (
                        _registered_videos_sql(2),
                        ("video000001", "video000002"),
                    ),
                ),
                (((_queued_videos_sql(2), (101, 102)),)),
                ((_INSERT_PROCESSING_QUEUE_SQL, (101,)),),
                ((_INSERT_PROCESSING_QUEUE_SQL, (102,)),),
            ],
        )
        self.assertIn("ON DUPLICATE KEY UPDATE", _INSERT_PROCESSING_QUEUE_SQL)
        self.assertIn("'transcript'", _INSERT_PROCESSING_QUEUE_SQL)
        self.assertIn("'pending'", _INSERT_PROCESSING_QUEUE_SQL)
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_register_preserves_existing_queue_without_an_update_or_commit(self) -> None:
        cursor = Mock()
        cursor.fetchall.side_effect = [[(101, "video000001")], [(101,)]]
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        registrar = ProcessingQueueRegistrar(factory)

        result = registrar.register(("video000001",))

        self.assertEqual(result.created_count, 0)
        self.assertEqual(result.existing_count, 1)
        self.assertEqual(result.missing_video_count, 0)
        self.assertEqual(
            cursor.execute.call_args_list,
            [
                (((_registered_videos_sql(1), ("video000001",))),),
                (((_queued_videos_sql(1), (101,))),),
            ],
        )
        connection.commit.assert_not_called()
        connection.rollback.assert_not_called()

    def test_register_reports_selected_videos_not_stored_by_metadata_sync(self) -> None:
        cursor = Mock()
        cursor.rowcount = 1
        cursor.fetchall.side_effect = [[(101, "video000001")], []]
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        registrar = ProcessingQueueRegistrar(factory)

        result = registrar.register(("video000001", "video000002"))

        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.existing_count, 0)
        self.assertEqual(result.missing_video_count, 1)
        cursor.execute.assert_any_call(_INSERT_PROCESSING_QUEUE_SQL, (101,))
        connection.commit.assert_called_once_with()

    def test_register_rolls_back_when_a_queue_insert_fails(self) -> None:
        cursor = Mock()
        cursor.fetchall.side_effect = [[(101, "video000001")], []]
        cursor.execute.side_effect = [None, None, RuntimeError("database unavailable")]
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        registrar = ProcessingQueueRegistrar(factory)

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            registrar.register(("video000001",))

        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_register_rejects_unexpected_database_rows(self) -> None:
        cursor = Mock()
        cursor.fetchall.return_value = [(True, "video000001")]
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection
        registrar = ProcessingQueueRegistrar(factory)

        with self.assertRaisesRegex(
            ProcessingQueueRegistrationError, "invalid registered video metadata"
        ):
            registrar.register(("video000001",))

        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()


def _registered_videos_sql(video_count: int) -> str:
    return _SELECT_REGISTERED_VIDEOS_SQL.format(
        placeholders=", ".join("%s" for _ in range(video_count))
    )


def _queued_videos_sql(video_count: int) -> str:
    return _SELECT_QUEUED_VIDEO_IDS_SQL.format(
        placeholders=", ".join("%s" for _ in range(video_count))
    )

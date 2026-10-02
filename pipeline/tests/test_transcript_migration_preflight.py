from __future__ import annotations

import unittest
from unittest.mock import Mock

from transcript.migration_preflight import (
    _SOURCE_COUNTS_SQL,
    _format_summary,
    collect_source_counts,
    is_safe_to_migrate,
)


class TranscriptMigrationPreflightTests(unittest.TestCase):
    def test_collects_only_source_counts(self) -> None:
        cursor = Mock()
        cursor.fetchall.return_value = [("library", 3), ("yt_dlp", 2)]
        connection = Mock()
        connection.cursor.return_value = cursor
        factory = Mock()
        factory.connect.return_value = connection

        actual = collect_source_counts(factory)

        self.assertEqual(actual, {"library": 3, "yt_dlp": 2})
        cursor.execute.assert_called_once_with(_SOURCE_COUNTS_SQL)
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_allows_only_current_sources_with_rows(self) -> None:
        self.assertTrue(is_safe_to_migrate({"library": 1, "yt_dlp": 1}))
        self.assertTrue(is_safe_to_migrate({"historical": 0}))
        self.assertFalse(is_safe_to_migrate({"historical": 1}))

    def test_summary_does_not_expose_unsupported_source_names(self) -> None:
        output = _format_summary({"library": 1, "historical": 2})

        self.assertIn("library=1", output)
        self.assertIn("yt_dlp=0", output)
        self.assertIn("unsupported_source_rows=2", output)
        self.assertIn("safe_to_migrate=False", output)
        self.assertNotIn("historical", output)

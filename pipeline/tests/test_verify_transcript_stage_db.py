from __future__ import annotations

import unittest
from unittest.mock import Mock

from common.config import MySqlSettings
from scripts.verify_transcript_stage_db import (
    _LOCAL_MYSQL_HOSTS,
    _RollbackOnlyConnectionFactory,
    _require_local_mysql,
)


class VerifyTranscriptStageDatabaseTests(unittest.TestCase):
    def test_accepts_only_loopback_mysql_hosts(self) -> None:
        for host in _LOCAL_MYSQL_HOSTS:
            _require_local_mysql(_settings(host))

    def test_refuses_non_local_mysql_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "local MySQL host"):
            _require_local_mysql(_settings("database.internal"))

    def test_rollback_only_connection_never_commits_or_closes_the_outer_connection(
        self,
    ) -> None:
        connection = Mock()
        rollback_connection = _RollbackOnlyConnectionFactory(connection).connect()

        self.assertIs(rollback_connection.cursor(), connection.cursor.return_value)
        rollback_connection.start_transaction()
        rollback_connection.commit()
        rollback_connection.rollback()
        rollback_connection.close()

        connection.start_transaction.assert_not_called()
        connection.commit.assert_not_called()
        connection.rollback.assert_not_called()
        connection.close.assert_not_called()


def _settings(host: str) -> MySqlSettings:
    return MySqlSettings(
        host=host,
        port=3306,
        database="unsponsor",
        user="verification",
        password="verification",
    )

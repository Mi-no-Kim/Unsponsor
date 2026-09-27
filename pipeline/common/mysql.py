"""MySQL 연결을 만드는 공용 경계다."""

from __future__ import annotations

from typing import Any

from common.config import MySqlSettings


class MySqlConnectionFactory:
    """후속 Work Unit이 필요한 시점에만 MySQL 연결을 연다."""

    def __init__(self, settings: MySqlSettings) -> None:
        self._settings = settings

    def connect(self) -> Any:
        import mysql.connector

        return mysql.connector.connect(
            host=self._settings.host,
            port=self._settings.port,
            database=self._settings.database,
            user=self._settings.user,
            password=self._settings.password,
            charset="utf8mb4",
        )

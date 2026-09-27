"""YouTube Data API의 공용 진입점이다."""

from __future__ import annotations

from typing import Any

from common.config import YouTubeSettings


class YouTubeDataClient:
    """후속 Work Unit이 재사용할 YouTube Data API 클라이언트 경계다."""

    def __init__(self, settings: YouTubeSettings) -> None:
        from googleapiclient.discovery import build

        self._service: Any = build(
            "youtube",
            "v3",
            developerKey=settings.api_key,
            cache_discovery=False,
        )

    @property
    def service(self) -> Any:
        """API 요청 구현은 W-009에서 이 서비스 경계 뒤에 추가한다."""

        return self._service

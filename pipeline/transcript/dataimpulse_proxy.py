"""DataImpulse를 통한 라이브러리 자막 요청의 공용 보안 경계다."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from urllib.parse import quote

from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig

from common.config import DataImpulseProxySettings
from transcript.library_extractor import LibraryTranscriptExtractor
from transcript.model import TranscriptExtractionResult, TranscriptFailure


_DATAIMPULSE_HOST = "gw.dataimpulse.com"
_DATAIMPULSE_ROTATING_HTTP_PORT = 823
_DEFAULT_LANGUAGE_CODES = ("ko", "en")


class DataImpulseLibrarySession:
    """DataImpulse 프록시가 강제된 한 번의 라이브러리 요청 세션이다."""

    def __init__(
        self, session: Session, extractor: LibraryTranscriptExtractor
    ) -> None:
        self._session = session
        self._extractor = extractor

    def extract(
        self,
        video_id: str,
        *,
        language_codes: Sequence[str] = _DEFAULT_LANGUAGE_CODES,
    ) -> TranscriptExtractionResult:
        return self._extractor.extract(video_id, language_codes=language_codes)

    def close(self) -> None:
        self._session.close()


class DataImpulseLibraryTranscriptExtractor:
    """영상마다 프록시 전용 세션을 열어 직접 egress를 차단한다."""

    def __init__(
        self,
        settings: DataImpulseProxySettings,
        *,
        error_logger: logging.Logger | None = None,
    ) -> None:
        self._settings = settings
        self._error_logger = error_logger

    def extract(self, video_id: str) -> TranscriptExtractionResult:
        attempt: DataImpulseLibrarySession | None = None
        try:
            attempt = create_dataimpulse_library_session(
                self._settings, self._error_logger
            )
            return attempt.extract(video_id)
        except Exception:
            if self._error_logger is not None:
                self._error_logger.error(
                    "DataImpulse library session failed safely"
                )
            return TranscriptExtractionResult.failed(
                TranscriptFailure.TRANSIENT_ERROR
            )
        finally:
            if attempt is not None:
                try:
                    attempt.close()
                except Exception:
                    if self._error_logger is not None:
                        self._error_logger.error(
                            "DataImpulse library session could not be closed safely"
                        )


def create_dataimpulse_library_session(
    settings: DataImpulseProxySettings,
    error_logger: logging.Logger | None,
) -> DataImpulseLibrarySession:
    """환경 프록시를 무시하고 DataImpulse만 쓰는 라이브러리 세션을 만든다."""

    session = Session()
    session.trust_env = False
    try:
        api = YouTubeTranscriptApi(
            proxy_config=GenericProxyConfig(http_url=dataimpulse_proxy_url(settings)),
            http_client=session,
        )
    except Exception:
        session.close()
        raise
    return DataImpulseLibrarySession(
        session,
        LibraryTranscriptExtractor(api, error_logger=error_logger),
    )


def dataimpulse_proxy_url(settings: DataImpulseProxySettings) -> str:
    """국가 타기팅이 붙은 DataImpulse 회전형 HTTP URL을 만든다."""

    targeted_username = f"{settings.username}__cr.{settings.country_code}"
    return (
        f"http://{quote(targeted_username, safe='')}:{quote(settings.password, safe='')}"
        f"@{_DATAIMPULSE_HOST}:{_DATAIMPULSE_ROTATING_HTTP_PORT}"
    )


def dataimpulse_proxy_secret_values(
    settings: DataImpulseProxySettings,
) -> tuple[str, ...]:
    """로그 포매터가 원문·URL 인코딩 형태를 모두 제거할 값을 반환한다."""

    targeted_username = f"{settings.username}__cr.{settings.country_code}"
    return (
        settings.username,
        settings.password,
        targeted_username,
        quote(settings.username, safe=""),
        quote(settings.password, safe=""),
        quote(targeted_username, safe=""),
        dataimpulse_proxy_url(settings),
    )

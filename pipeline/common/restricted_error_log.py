"""로컬 운영자만 읽는 자막 단계 오류 로그 경계다."""

from __future__ import annotations

import logging
import re
from pathlib import Path


_LOGGER_NAME = "unsponsor.pipeline.transcript.errors"
_CREDENTIAL_VALUE = re.compile(
    r"(?i)(\b(?:cookie|authorization|proxy-authorization|potoken)\s*[:=]\s*)([^\r\n]*)"
)
_CREDENTIAL_QUERY_PARAMETER = re.compile(
    r"(?i)([?&](?:cookie|authorization|token|potoken|key|password)=)([^&#\s]+)"
)


class _RestrictedErrorFormatter(logging.Formatter):
    """오류 원문은 남기되 자격 증명 값만 기록 전에 제거한다."""

    def __init__(self, *args: object, secret_values: tuple[str, ...] = ()) -> None:
        super().__init__(*args)
        self._secret_values = secret_values

    def format(self, record: logging.LogRecord) -> str:
        return _redact_credentials(super().format(record), self._secret_values)


def configure_restricted_error_log(
    path: Path,
    *,
    cookie_file: Path | None = None,
    console_output: bool = True,
) -> logging.Logger:
    """콘솔과 로컬 파일에만 자막 단계의 상세 오류를 기록한다."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = _RestrictedErrorFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        secret_values=_read_cookie_values(cookie_file),
    )
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.ERROR)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    return logger


def transcript_error_logger() -> logging.Logger:
    """설정 전에는 stderr로 새지 않는 기본 오류 로거를 돌려준다."""

    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
    return logger


def _redact_credentials(message: str, secret_values: tuple[str, ...]) -> str:
    message = _CREDENTIAL_VALUE.sub(r"\1<redacted>", message)
    message = _CREDENTIAL_QUERY_PARAMETER.sub(r"\1<redacted>", message)
    for secret_value in secret_values:
        message = message.replace(secret_value, "<redacted>")
    return message


def _read_cookie_values(cookie_file: Path | None) -> tuple[str, ...]:
    if cookie_file is None:
        return ()

    try:
        lines = Path(cookie_file).read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()

    values: list[str] = []
    for line in lines:
        fields = line.removeprefix("#HttpOnly_").split("\t")
        if len(fields) == 7 and fields[6]:
            values.append(fields[6])
    return tuple(values)

"""파이프라인의 비밀값과 비공개 채널 시드를 안전하게 읽는다."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


class ConfigurationError(ValueError):
    """사용자가 수정할 수 있는 설정 파일 또는 환경변수의 오류다."""


@dataclass(frozen=True)
class YouTubeSettings:
    api_key: str = field(repr=False)


@dataclass(frozen=True)
class MySqlSettings:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)


@dataclass(frozen=True)
class ChannelSeed:
    channel_id: str
    language_code: str


@dataclass(frozen=True)
class PipelineSettings:
    youtube: YouTubeSettings
    mysql: MySqlSettings
    channels: tuple[ChannelSeed, ...]
    selected_video_ids: tuple[str, ...] | None


_REQUIRED_ENVIRONMENT_KEYS = (
    "YOUTUBE_API_KEY",
    "MYSQL_HOST",
    "MYSQL_PORT",
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
)
_LANGUAGE_CODE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
_CHANNEL_ID_PATTERN = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")


def load_settings(
    repository_root: Path,
    channel_seed_path: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> PipelineSettings:
    """공용 비밀 설정과 pipeline 전용 채널 시드를 검증해 반환한다.

    명시적으로 전달한 ``environment``는 테스트용이며, 전달하지 않으면 현재
    프로세스 환경변수를 사용한다. 운영체제 환경변수는 루트 ``.env`` 값을
    덮어쓴다.
    """

    repository_root = Path(repository_root)
    channel_seed_path = Path(channel_seed_path)
    values = _read_dotenv(repository_root / ".env")
    values.update(os.environ if environment is None else environment)

    missing_keys = [key for key in _REQUIRED_ENVIRONMENT_KEYS if not values.get(key)]
    if missing_keys:
        raise ConfigurationError(
            "Missing required configuration: " + ", ".join(missing_keys)
        )

    try:
        mysql_port = int(values["MYSQL_PORT"])
    except ValueError as error:
        raise ConfigurationError("MYSQL_PORT must be an integer") from error

    if not 1 <= mysql_port <= 65535:
        raise ConfigurationError("MYSQL_PORT must be between 1 and 65535")

    channel_seed_document = _read_channel_seed_document(channel_seed_path)

    return PipelineSettings(
        youtube=YouTubeSettings(api_key=values["YOUTUBE_API_KEY"]),
        mysql=MySqlSettings(
            host=values["MYSQL_HOST"],
            port=mysql_port,
            database=values["MYSQL_DATABASE"],
            user=values["MYSQL_USER"],
            password=values["MYSQL_PASSWORD"],
        ),
        channels=_read_channel_seeds(channel_seed_document),
        selected_video_ids=_read_selected_video_ids(channel_seed_document),
    )


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            raise ConfigurationError(f".env has an invalid entry at line {line_number}")

        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        if not key.isidentifier():
            raise ConfigurationError(f".env has an invalid key at line {line_number}")

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value

    return values


def _read_channel_seed_document(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ConfigurationError("channels.local.json is required")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigurationError("channels.local.json must contain valid JSON") from error

    if not isinstance(payload, dict):
        raise ConfigurationError("channels.local.json must contain an object")

    return payload


def _read_channel_seeds(payload: dict[str, object]) -> tuple[ChannelSeed, ...]:
    if not isinstance(payload.get("channels"), list):
        raise ConfigurationError("channels.local.json must contain a channels array")

    raw_channels = payload["channels"]
    if not raw_channels:
        raise ConfigurationError("channels.local.json must contain at least one channel")

    channels: list[ChannelSeed] = []
    channel_ids: set[str] = set()
    for index, raw_channel in enumerate(raw_channels, 1):
        if not isinstance(raw_channel, dict):
            raise ConfigurationError(f"Channel entry {index} must be an object")

        channel_id = raw_channel.get("channel_id")
        language_code = raw_channel.get("language_code")
        if not isinstance(channel_id, str) or not channel_id.strip():
            raise ConfigurationError(f"Channel entry {index} requires a channel_id")
        normalized_channel_id = channel_id.strip()
        if not _CHANNEL_ID_PATTERN.fullmatch(normalized_channel_id):
            raise ConfigurationError(
                f"Channel entry {index} requires a valid YouTube channel ID"
            )
        if not isinstance(language_code, str) or not _LANGUAGE_CODE_PATTERN.fullmatch(
            language_code
        ):
            raise ConfigurationError(
                f"Channel entry {index} requires a valid language_code"
            )

        if normalized_channel_id in channel_ids:
            raise ConfigurationError("channels.local.json must not contain duplicate channel IDs")
        channel_ids.add(normalized_channel_id)
        channels.append(
            ChannelSeed(
                channel_id=normalized_channel_id,
                language_code=language_code,
            )
        )

    return tuple(channels)


def _read_selected_video_ids(payload: dict[str, object]) -> tuple[str, ...] | None:
    if "selected_video_ids" not in payload:
        return None

    raw_video_ids = payload["selected_video_ids"]
    if not isinstance(raw_video_ids, list):
        raise ConfigurationError("selected_video_ids must be an array")
    if not 10 <= len(raw_video_ids) <= 50:
        raise ConfigurationError("selected_video_ids must contain between 10 and 50 entries")

    video_ids: list[str] = []
    seen_video_ids: set[str] = set()
    for index, raw_video_id in enumerate(raw_video_ids, 1):
        if not isinstance(raw_video_id, str) or not _VIDEO_ID_PATTERN.fullmatch(
            raw_video_id
        ):
            raise ConfigurationError(
                f"Selected video entry {index} requires a valid YouTube video ID"
            )
        if raw_video_id in seen_video_ids:
            raise ConfigurationError("selected_video_ids must not contain duplicate video IDs")
        seen_video_ids.add(raw_video_id)
        video_ids.append(raw_video_id)

    return tuple(video_ids)

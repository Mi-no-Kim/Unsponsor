"""W-027 자막 단계의 커밋 내구성을 폐기형 MySQL 8.4에서 검증한다."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

from collector.processing_queue_worker import ProcessingQueueWorker
from common.config import MySqlSettings, QueueSettings
from common.mysql import MySqlConnectionFactory
from transcript.fixture_dataset import (
    TranscriptFixtureDataset,
    TranscriptFixtureDatasetError,
)
from transcript.model import (
    TranscriptExtractionResult,
    TranscriptFailure,
    TranscriptSource,
)
from transcript.stage_runner import TranscriptStageRunner
from transcript.store import TranscriptStore


class W027VerificationError(RuntimeError):
    """검증 출력에 원문이나 식별자 없이 남길 수 있는 안전한 오류다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _TranscriptExtractor(Protocol):
    def extract(self, video_id: str) -> TranscriptExtractionResult:
        """검증용 영상에 미리 정한 결과를 반환한다."""


@dataclass
class _FixedExtractor:
    result: TranscriptExtractionResult
    calls: int = 0

    def extract(self, video_id: str) -> TranscriptExtractionResult:
        self.calls += 1
        return self.result


@dataclass(frozen=True)
class _DisposableMySql:
    container_name: str
    settings: MySqlSettings


_LEGACY_FLYWAY_VERSIONS = ("1", "2", "3", "4", "5")
_EXPECTED_FLYWAY_VERSIONS = (*_LEGACY_FLYWAY_VERSIONS, "6")
_DOCKER_PORT_PATTERN = re.compile(r"^127\.0\.0\.1:(?P<port>[0-9]{1,5})$")
_CONTAINER_NAME_PATTERN = re.compile(r"^unsponsor-w027-[0-9a-f]{12}$")
_NAMED_PIPE_PATTERN = re.compile(r"^npipe:////\./pipe/[a-z0-9_.-]+$")
_W027_SCOPE_LABEL = "xyz.unsponsor.scope=w027"
_W027_RUN_LABEL = "xyz.unsponsor.run"
_DOCKER_ENVIRONMENT_KEYS = (
    "MYSQL_DATABASE",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", required=True, type=Path)
    parser.add_argument("--confirm-disposable-database", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.confirm_disposable_database:
        parser.error("--confirm-disposable-database is required")

    repository_root = Path(__file__).resolve().parents[2]
    timings: dict[str, float] = {}
    try:
        started_at = time.monotonic()
        dataset = TranscriptFixtureDataset.load(arguments.fixture_dir)
        timings["fixture_load"] = _elapsed(started_at)

        with _disposable_mysql(timings) as database:
            started_at = time.monotonic()
            _apply_backend_flyway(repository_root, database.settings, target="5")
            factory = MySqlConnectionFactory(database.settings)
            _assert_flyway_versions(factory, _LEGACY_FLYWAY_VERSIONS)
            migrated_rows = _seed_legacy_transcripts(factory, dataset)
            _apply_backend_flyway(repository_root, database.settings, target="6")
            _assert_flyway_versions(factory, _EXPECTED_FLYWAY_VERSIONS)
            timings["flyway"] = _elapsed(started_at)

            started_at = time.monotonic()
            checks = _assert_legacy_transcripts_migrated(factory, migrated_rows)
            checks.extend(_verify_stage_transitions(factory, dataset))
            timings["database_checks"] = _elapsed(started_at)
    except TranscriptFixtureDatasetError:
        _print_failure("fixture_invalid")
    except W027VerificationError as error:
        _print_failure(error.code)
    except AssertionError:
        _print_failure("database_check_failed")
    except Exception:
        _print_failure("unexpected_error")

    print(
        json.dumps(
            {
                "status": "passed",
                "checks": checks,
                "stages_seconds": timings,
            },
            separators=(",", ":"),
        )
    )


def _print_failure(code: str) -> None:
    print(json.dumps({"status": "failed", "error": code}, separators=(",", ":")))
    raise SystemExit(1)


def _elapsed(started_at: float) -> float:
    return round(time.monotonic() - started_at, 3)


@contextmanager
def _disposable_mysql(timings: dict[str, float]) -> Iterator[_DisposableMySql]:
    started_at = time.monotonic()
    endpoint = _docker_endpoint()
    _require_local_docker_endpoint(endpoint)

    container_name = f"unsponsor-w027-{secrets.token_hex(6)}"
    database_name = f"w027db_{secrets.token_hex(6)}"
    database_user = f"w027u_{secrets.token_hex(6)}"
    database_password = secrets.token_urlsafe(32)
    root_password = secrets.token_urlsafe(32)
    docker_environment = _docker_environment(
        os.environ,
        database_name=database_name,
        database_user=database_user,
        database_password=database_password,
        root_password=root_password,
    )
    started = False
    try:
        _run_docker(
            _docker_run_arguments(container_name), environment=docker_environment
        )
        started = True
        _assert_container_has_no_docker_volume(container_name)
        port = _wait_for_published_port(container_name)
        settings = MySqlSettings(
            host="127.0.0.1",
            port=port,
            database=database_name,
            user=database_user,
            password=database_password,
        )
        _wait_for_mysql(settings)
        timings["database_start"] = _elapsed(started_at)
        yield _DisposableMySql(container_name=container_name, settings=settings)
    finally:
        cleanup_started_at = time.monotonic()
        if started and not _remove_container(container_name):
            raise W027VerificationError("database_cleanup_failed")
        timings["cleanup"] = _elapsed(cleanup_started_at)


def _docker_endpoint() -> str:
    completed = _run_docker(
        ["context", "inspect", "--format", '{{(index .Endpoints "docker").Host}}']
    )
    endpoint = completed.stdout.strip()
    if not endpoint:
        raise W027VerificationError("docker_context_unavailable")
    return endpoint


def _require_local_docker_endpoint(endpoint: str) -> None:
    normalized = endpoint.strip().casefold()
    if _NAMED_PIPE_PATTERN.fullmatch(normalized):
        return
    if normalized.startswith("unix:///") and "\x00" not in normalized:
        return
    raise W027VerificationError("docker_context_must_be_local")


def _docker_run_arguments(container_name: str) -> list[str]:
    _validate_container_name(container_name)
    arguments = [
        "run",
        "--detach",
        "--rm",
        "--name",
        container_name,
        "--label",
        _W027_SCOPE_LABEL,
        "--label",
        f"{_W027_RUN_LABEL}={container_name}",
        "--publish",
        "127.0.0.1::3306/tcp",
        "--tmpfs",
        "/var/lib/mysql:rw,noexec,nosuid",
    ]
    for key in _DOCKER_ENVIRONMENT_KEYS:
        arguments.extend(("--env", key))
    return [
        *arguments,
        "mysql:8.4",
        "--character-set-server=utf8mb4",
        "--collation-server=utf8mb4_0900_ai_ci",
    ]


def _docker_environment(
    base: Mapping[str, str],
    *,
    database_name: str,
    database_user: str,
    database_password: str,
    root_password: str,
) -> dict[str, str]:
    environment = dict(base)
    environment.update(
        {
            "MYSQL_DATABASE": database_name,
            "MYSQL_USER": database_user,
            "MYSQL_PASSWORD": database_password,
            "MYSQL_ROOT_PASSWORD": root_password,
        }
    )
    return environment


def _assert_container_has_no_docker_volume(container_name: str) -> None:
    _validate_container_name(container_name)
    completed = _run_docker(
        [
            "inspect",
            "--format",
            (
                '{{json .HostConfig.Tmpfs}}|'
                '{{range .Mounts}}{{if eq .Type "volume"}}volume{{end}}{{end}}'
            ),
            container_name,
        ]
    )
    _parse_mount_inspection(completed.stdout)


def _parse_mount_inspection(output: str) -> None:
    parts = output.strip().split("|", 1)
    if len(parts) != 2:
        raise W027VerificationError("database_mount_invalid")
    try:
        tmpfs = json.loads(parts[0])
    except json.JSONDecodeError as error:
        raise W027VerificationError("database_mount_invalid") from error
    if not isinstance(tmpfs, dict) or set(tmpfs) != {"/var/lib/mysql"}:
        raise W027VerificationError("database_mount_invalid")
    options = tmpfs["/var/lib/mysql"]
    if not isinstance(options, str) or not {"rw", "noexec", "nosuid"}.issubset(
        set(options.split(","))
    ):
        raise W027VerificationError("database_mount_invalid")
    if parts[1]:
        raise W027VerificationError("database_volume_forbidden")


def _wait_for_published_port(container_name: str) -> int:
    _validate_container_name(container_name)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        completed = _run_docker(
            ["port", container_name, "3306/tcp"], allow_failure=True
        )
        if completed.returncode == 0:
            try:
                return _parse_published_port(completed.stdout)
            except W027VerificationError:
                pass
        time.sleep(0.25)
    raise W027VerificationError("database_port_unavailable")


def _parse_published_port(output: str) -> int:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        raise W027VerificationError("database_port_invalid")
    match = _DOCKER_PORT_PATTERN.fullmatch(lines[0])
    if match is None:
        raise W027VerificationError("database_port_invalid")
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise W027VerificationError("database_port_invalid")
    return port


def _wait_for_mysql(settings: MySqlSettings) -> None:
    import mysql.connector

    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            connection = MySqlConnectionFactory(settings).connect()
        except mysql.connector.Error:
            time.sleep(0.5)
            continue
        connection.close()
        return
    raise W027VerificationError("database_start_timeout")


def _run_docker(
    arguments: Sequence[str],
    *,
    allow_failure: bool = False,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["docker", *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            env=None if environment is None else dict(environment),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise W027VerificationError("docker_unavailable") from error
    if completed.returncode != 0 and not allow_failure:
        raise W027VerificationError("docker_command_failed")
    return completed


def _remove_container(container_name: str) -> bool:
    try:
        _validate_container_name(container_name)
    except W027VerificationError:
        return False
    inspected = _run_docker(
        [
            "inspect",
            "--format",
            (
                '{{index .Config.Labels "xyz.unsponsor.scope"}}|'
                '{{index .Config.Labels "xyz.unsponsor.run"}}'
            ),
            container_name,
        ],
        allow_failure=True,
    )
    if inspected.returncode != 0:
        return False
    if inspected.stdout.strip() != f"w027|{container_name}":
        return False
    removed = _run_docker(
        ["rm", "--force", container_name], allow_failure=True
    )
    return removed.returncode == 0


def _validate_container_name(container_name: str) -> None:
    if not _CONTAINER_NAME_PATTERN.fullmatch(container_name):
        raise W027VerificationError("container_name_invalid")


def _apply_backend_flyway(
    repository_root: Path, settings: MySqlSettings, *, target: str
) -> None:
    backend_root = repository_root / "backend"
    wrapper = backend_root / ("gradlew.bat" if os.name == "nt" else "gradlew")
    command = [
        str(wrapper),
        "--no-daemon",
        "bootRun",
        "--args=--spring.main.web-application-type=none --spring.main.banner-mode=off",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=backend_root,
            env=_flyway_environment(settings, os.environ, target=target),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=240,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise W027VerificationError("flyway_execution_failed") from error
    if completed.returncode != 0:
        raise W027VerificationError("flyway_execution_failed")


def _flyway_environment(
    settings: MySqlSettings, base: Mapping[str, str], *, target: str = "6"
) -> dict[str, str]:
    environment = dict(base)
    environment.update(
        {
            "MYSQL_HOST": settings.host,
            "MYSQL_PORT": str(settings.port),
            "MYSQL_DATABASE": settings.database,
            "MYSQL_USER": settings.user,
            "MYSQL_PASSWORD": settings.password,
            "SPRING_MAIN_WEB_APPLICATION_TYPE": "none",
            "SPRING_MAIN_BANNER_MODE": "off",
            "SPRING_FLYWAY_TARGET": target,
        }
    )
    return environment


def _assert_flyway_versions(
    factory: MySqlConnectionFactory, expected: tuple[str, ...]
) -> None:
    rows = _fetch_all(
        factory,
        """
        SELECT version
        FROM flyway_schema_history
        WHERE success = TRUE
        ORDER BY installed_rank
        """,
        (),
    )
    _require(tuple(row[0] for row in rows) == expected)


def _seed_legacy_transcripts(
    factory: MySqlConnectionFactory, dataset: TranscriptFixtureDataset
) -> tuple[tuple[int, TranscriptSource, TranscriptExtractionResult], ...]:
    channel_id = _insert_row(
        factory,
        """
        INSERT INTO channels (
          youtube_channel_id, uploads_playlist_id, name, language_code, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())
        """,
        ("UC" + "c" * 22, "UU" + "d" * 32, "migration", "ko"),
    )
    rows = (
        (
            _insert_video(factory, channel_id, "migration_library"),
            TranscriptSource.LIBRARY,
            dataset.library_success,
        ),
        (
            _insert_video(factory, channel_id, "migration_ytdlp"),
            TranscriptSource.YT_DLP,
            dataset.ytdlp_success,
        ),
    )
    for video_id, source, result in rows:
        _seed_legacy_transcript(factory, video_id, source, result)
    return rows


def _seed_legacy_transcript(
    factory: MySqlConnectionFactory,
    video_id: int,
    source: TranscriptSource,
    result: TranscriptExtractionResult,
) -> None:
    connection = factory.connect()
    cursor = connection.cursor()
    try:
        connection.start_transaction()
        cursor.execute(
            """
            INSERT INTO video_transcripts (
              video_id, raw_text, source, created_at, updated_at
            )
            VALUES (%s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())
            """,
            (video_id, result.text, source.value),
        )
        cursor.executemany(
            """
            INSERT INTO video_transcript_segments (
              video_id, sequence, start_ms, end_ms, text, created_at
            )
            VALUES (%s, %s, %s, %s, %s, UTC_TIMESTAMP())
            """,
            tuple(
                (
                    video_id,
                    segment.sequence,
                    segment.start_ms,
                    segment.end_ms,
                    segment.text,
                )
                for segment in result.segments
            ),
        )
        if cursor.rowcount != len(result.segments):
            raise W027VerificationError("legacy_seed_failed")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _assert_legacy_transcripts_migrated(
    factory: MySqlConnectionFactory,
    rows: tuple[tuple[int, TranscriptSource, TranscriptExtractionResult], ...],
) -> list[str]:
    for video_id, source, result in rows:
        _assert_transcript_result(factory, video_id, source, result)
    segment_table_count = _fetch_one(
        factory,
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = 'video_transcript_segments'
        """,
        (),
    )
    raw_text_count = _fetch_one(
        factory,
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'video_transcripts'
          AND column_name = 'raw_text'
        """,
        (),
    )
    _require(segment_table_count == (0,) and raw_text_count == (0,))
    return ["legacy_fixture_rows_compacted", "legacy_duplicate_storage_removed"]


def _verify_stage_transitions(
    factory: MySqlConnectionFactory, dataset: TranscriptFixtureDataset
) -> list[str]:
    queue = ProcessingQueueWorker(factory, _verification_queue_settings())
    store = TranscriptStore(factory)
    channel_id = _insert_channel(factory)
    no_transcript = TranscriptExtractionResult.failed(
        TranscriptFailure.NO_TRANSCRIPT
    )
    rate_limited = TranscriptExtractionResult.failed(
        TranscriptFailure.RATE_LIMITED
    )
    checks: list[str] = []

    library_video = _insert_video_and_queue(factory, channel_id, "library_success")
    library_fixture = _FixedExtractor(dataset.library_success)
    unused_ytdlp = _FixedExtractor(no_transcript)
    library_result = _run_stage(
        queue,
        store,
        unused_ytdlp,
        library_extractor=library_fixture,
    )
    _require(
        library_result.library_count == 1
        and library_fixture.calls == 1
        and unused_ytdlp.calls == 0
    )
    _assert_queue_state(factory, library_video, "identify", "pending", None, 0)
    _assert_transcript_result(
        factory,
        library_video,
        TranscriptSource.LIBRARY,
        dataset.library_success,
    )
    checks.append("library_fixture_committed")

    success_video = _insert_video_and_queue(factory, channel_id, "success")
    success_library = _FixedExtractor(no_transcript)
    success_ytdlp = _FixedExtractor(dataset.ytdlp_success)
    success_result = _run_stage(
        queue,
        store,
        success_ytdlp,
        library_extractor=success_library,
    )
    _require(
        success_result.ytdlp_count == 1
        and success_library.calls == 1
        and success_ytdlp.calls == 1
    )
    _assert_queue_state(factory, success_video, "identify", "pending", None, 0)
    _assert_transcript_result(
        factory, success_video, TranscriptSource.YT_DLP, dataset.ytdlp_success
    )
    checks.append("ytdlp_fixture_committed")

    no_transcript_video = _insert_video_and_queue(
        factory, channel_id, "no_transcript", attempt_count=2
    )
    _run_stage(
        queue,
        store,
        _FixedExtractor(no_transcript),
        library_extractor=_FixedExtractor(no_transcript),
    )
    _assert_queue_state(
        factory, no_transcript_video, "transcript", "failed", "no_transcript", 3
    )
    _assert_no_transcript(factory, no_transcript_video)
    checks.append("no_transcript_committed_before_identify")

    stuck_video = _insert_video_and_queue(factory, channel_id, "stuck")
    _mark_queue_stuck(factory, stuck_video)
    recovery_library = _FixedExtractor(no_transcript)
    recovery_ytdlp = _FixedExtractor(no_transcript)
    recovery_result = _run_stage(
        queue,
        store,
        recovery_ytdlp,
        library_extractor=recovery_library,
    )
    _require(
        recovery_result.recovered_count == 1
        and recovery_library.calls == 0
        and recovery_ytdlp.calls == 0
    )
    _assert_queue_state(
        factory, stuck_video, "transcript", "pending", "worker_stalled", 1
    )
    _assert_retry_scheduled(factory, stuck_video)
    checks.append("stuck_work_recovery_committed")

    replacement_video = _insert_video_and_queue(
        factory, channel_id, "replacement"
    )
    store.replace_success(
        replacement_video,
        TranscriptSource.LIBRARY,
        dataset.library_success,
    )
    _run_stage(
        queue,
        store,
        _FixedExtractor(dataset.ytdlp_success),
        library_extractor=_FixedExtractor(no_transcript),
    )
    _assert_queue_state(
        factory, replacement_video, "identify", "pending", None, 0
    )
    _assert_transcript_result(
        factory,
        replacement_video,
        TranscriptSource.YT_DLP,
        dataset.ytdlp_success,
    )
    _assert_single_transcript(factory, replacement_video)
    checks.append("latest_fixture_replaces_payload_atomically")

    rate_limited_video = _insert_video_and_queue(
        factory, channel_id, "rate_limited"
    )
    pending_video = _insert_video_and_queue(factory, channel_id, "unclaimed")
    rate_library = _FixedExtractor(no_transcript)
    rate_ytdlp = _FixedExtractor(rate_limited)
    rate_result = _run_stage(
        queue,
        store,
        rate_ytdlp,
        library_extractor=rate_library,
    )
    _require(
        rate_result.rate_limited_stop_count == 1
        and rate_library.calls == 1
        and rate_ytdlp.calls == 1
    )
    _assert_queue_state(
        factory, rate_limited_video, "transcript", "pending", "rate_limited", 1
    )
    _assert_queue_state(factory, pending_video, "transcript", "pending", None, 0)
    checks.append("rate_limit_preserves_committed_pending_work")
    return checks


def _verification_queue_settings() -> QueueSettings:
    return QueueSettings(
        max_attempts=3,
        retry_backoff_base_seconds=60,
        stale_after_seconds=60,
    )


def _run_stage(
    queue: ProcessingQueueWorker,
    store: TranscriptStore,
    ytdlp_extractor: _TranscriptExtractor,
    *,
    library_extractor: _TranscriptExtractor,
):
    return TranscriptStageRunner(
        queue,
        store,
        ytdlp_extractor,
        library_extractor=library_extractor,
    ).run()


def _insert_channel(factory: MySqlConnectionFactory) -> int:
    return _insert_row(
        factory,
        """
        INSERT INTO channels (
          youtube_channel_id, uploads_playlist_id, name, language_code, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())
        """,
        ("UC" + "a" * 22, "UU" + "b" * 32, "verification", "ko"),
    )


def _insert_video(factory: MySqlConnectionFactory, channel_id: int, label: str) -> int:
    return _insert_row(
        factory,
        """
        INSERT INTO videos (
          youtube_video_id, channel_id, title, description, published_at, language_code,
          created_at, updated_at
        )
        VALUES (%s, %s, %s, NULL, UTC_TIMESTAMP(), %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())
        """,
        (_verification_youtube_video_id(label), channel_id, "verification", "ko"),
    )


def _insert_video_and_queue(
    factory: MySqlConnectionFactory,
    channel_id: int,
    label: str,
    *,
    attempt_count: int = 0,
) -> int:
    video_id = _insert_video(factory, channel_id, label)
    _insert_row(
        factory,
        """
        INSERT INTO video_processing_queue (
          video_id, stage, status, attempt_count, created_at, updated_at
        )
        VALUES (%s, 'transcript', 'pending', %s, UTC_TIMESTAMP(), UTC_TIMESTAMP())
        """,
        (video_id, attempt_count),
    )
    return video_id


def _insert_row(
    factory: MySqlConnectionFactory, statement: str, values: tuple[object, ...]
) -> int:
    connection = factory.connect()
    cursor = connection.cursor()
    try:
        cursor.execute(statement, values)
        connection.commit()
        identifier = cursor.lastrowid
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
        raise W027VerificationError("database_insert_failed")
    return identifier


def _verification_youtube_video_id(label: str) -> str:
    values = {
        "library_success": "verify00008",
        "success": "verify00001",
        "no_transcript": "verify00003",
        "rate_limited": "verify00004",
        "unclaimed": "verify00005",
        "stuck": "verify00006",
        "replacement": "verify00007",
        "migration_library": "verify00009",
        "migration_ytdlp": "verify00010",
    }
    return values[label]


def _mark_queue_stuck(factory: MySqlConnectionFactory, video_id: int) -> None:
    _execute(
        factory,
        """
        UPDATE video_processing_queue
        SET status = 'processing',
            started_at = DATE_SUB(UTC_TIMESTAMP(), INTERVAL 2 MINUTE)
        WHERE video_id = %s
        """,
        (video_id,),
    )


def _assert_queue_state(
    factory: MySqlConnectionFactory,
    video_id: int,
    stage: str,
    status: str,
    last_error: str | None,
    attempt_count: int,
) -> None:
    row = _fetch_one(
        factory,
        """
        SELECT stage, status, last_error, attempt_count
        FROM video_processing_queue
        WHERE video_id = %s
        """,
        (video_id,),
    )
    _require(row == (stage, status, last_error, attempt_count))


def _assert_retry_scheduled(factory: MySqlConnectionFactory, video_id: int) -> None:
    row = _fetch_one(
        factory,
        """
        SELECT next_attempt_at IS NOT NULL, started_at IS NULL
        FROM video_processing_queue
        WHERE video_id = %s
        """,
        (video_id,),
    )
    _require(row == (1, 1))


def _assert_transcript_result(
    factory: MySqlConnectionFactory,
    video_id: int,
    source: TranscriptSource,
    result: TranscriptExtractionResult,
) -> None:
    stored = TranscriptStore(factory).find_success(video_id)
    _require(stored is not None and stored.source is source and stored.result == result)


def _assert_no_transcript(factory: MySqlConnectionFactory, video_id: int) -> None:
    transcript_count = _fetch_one(
        factory,
        "SELECT COUNT(*) FROM video_transcripts WHERE video_id = %s",
        (video_id,),
    )
    _require(transcript_count == (0,))


def _assert_single_transcript(
    factory: MySqlConnectionFactory, video_id: int
) -> None:
    row = _fetch_one(
        factory,
        "SELECT COUNT(*) FROM video_transcripts WHERE video_id = %s",
        (video_id,),
    )
    _require(row == (1,))


def _execute(
    factory: MySqlConnectionFactory, statement: str, values: tuple[object, ...]
) -> None:
    connection = factory.connect()
    cursor = connection.cursor()
    try:
        cursor.execute(statement, values)
        if cursor.rowcount != 1:
            raise W027VerificationError("database_update_failed")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _fetch_one(
    factory: MySqlConnectionFactory, statement: str, values: tuple[object, ...]
) -> tuple[object, ...] | None:
    rows = _fetch_all(factory, statement, values)
    if len(rows) > 1:
        raise W027VerificationError("database_query_invalid")
    return rows[0] if rows else None


def _fetch_all(
    factory: MySqlConnectionFactory, statement: str, values: tuple[object, ...]
) -> tuple[tuple[object, ...], ...]:
    connection = factory.connect()
    cursor = connection.cursor()
    try:
        cursor.execute(statement, values)
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    if not isinstance(rows, list) or any(not isinstance(row, tuple) for row in rows):
        raise W027VerificationError("database_query_invalid")
    return tuple(rows)


def _require(condition: bool) -> None:
    if not condition:
        raise AssertionError("W-027 database verification failed")


if __name__ == "__main__":
    main()

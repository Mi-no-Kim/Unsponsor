"""W-021 자막 단계의 DB 전이를 롤백 전용 트랜잭션으로 검증한다."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from collector.processing_queue_worker import ProcessingQueueWorker
from common.config import MySqlSettings, QueueSettings, load_settings
from common.mysql import MySqlConnectionFactory
from transcript.model import (
    TranscriptExtractionResult,
    TranscriptFailure,
    TranscriptSegment,
    TranscriptSource,
)
from transcript.stage_runner import TranscriptStageRunner
from transcript.store import TranscriptStore


_LOCAL_MYSQL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
class _TranscriptExtractor(Protocol):
    def extract(self, video_id: str) -> TranscriptExtractionResult:
        """검증용 외부 영상 ID에 대해 미리 정한 결과를 반환한다."""


@dataclass
class _FixedExtractor:
    """네트워크 요청 없이 자막 경로별 결과를 재현한다."""

    result: TranscriptExtractionResult
    calls: int = 0

    def extract(self, video_id: str) -> TranscriptExtractionResult:
        self.calls += 1
        return self.result


class _RollbackOnlyConnectionFactory:
    """하나의 열린 DB 트랜잭션을 여러 저장 경계에 안전하게 공유한다."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def connect(self) -> _RollbackOnlyConnection:
        return _RollbackOnlyConnection(self._connection)


class _RollbackOnlyConnection:
    """저장 경계의 commit·close가 바깥 검증 트랜잭션에 영향을 주지 않게 한다."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def cursor(self) -> Any:
        return self._connection.cursor()

    def start_transaction(self) -> None:
        return None

    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def main(argv: Sequence[str] | None = None) -> None:
    """현재 로컬 DB에서 W-021 상태 전이를 커밋 없이 검증한다."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--confirm-rollback-transaction",
        action="store_true",
        help="현재 로컬 MySQL에서 가짜 검증 행을 만든 뒤 모두 롤백하는 작업을 확인한다",
    )
    arguments = parser.parse_args(argv)
    if not arguments.confirm_rollback_transaction:
        parser.error("--confirm-rollback-transaction is required")

    pipeline_root = Path(__file__).resolve().parents[1]
    repository_root = pipeline_root.parent
    settings = load_settings(repository_root, pipeline_root / "channels.local.json")
    _require_local_mysql(settings.mysql)

    connection = MySqlConnectionFactory(settings.mysql).connect()
    try:
        connection.start_transaction()
        checks = _verify_stage_transitions(_RollbackOnlyConnectionFactory(connection))
    finally:
        connection.rollback()
        connection.close()

    print(json.dumps({"status": "passed", "checks": checks}, separators=(",", ":")))


def _require_local_mysql(settings: MySqlSettings) -> None:
    if settings.host.casefold() not in _LOCAL_MYSQL_HOSTS:
        raise ValueError("temporary database verification requires a local MySQL host")


def _verify_stage_transitions(factory: MySqlConnectionFactory) -> list[str]:
    queue = ProcessingQueueWorker(factory, _verification_queue_settings())
    store = TranscriptStore(factory)
    channel_id = _insert_channel(factory)
    checks: list[str] = []

    library_video = _insert_video_and_queue(factory, channel_id, "library")
    _run_stage(
        queue,
        store,
        _FixedExtractor(_successful_result("library")),
        _FixedExtractor(_failed_result(TranscriptFailure.NO_TRANSCRIPT)),
    )
    _assert_queue_state(factory, library_video, "identify", "pending", None, 0)
    _assert_transcript(factory, library_video, TranscriptSource.LIBRARY, 2)
    checks.append("library_success_to_identify")

    fallback_video = _insert_video_and_queue(factory, channel_id, "fallback")
    library = _FixedExtractor(_failed_result(TranscriptFailure.ACCESS_RESTRICTED))
    ytdlp = _FixedExtractor(_successful_result("fallback"))
    result = _run_stage(queue, store, library, ytdlp)
    _require(result.ytdlp_count == 1 and library.calls == 1 and ytdlp.calls == 1)
    _assert_queue_state(factory, fallback_video, "identify", "pending", None, 0)
    _assert_transcript(factory, fallback_video, TranscriptSource.YT_DLP, 2)
    checks.append("ytdlp_fallback_to_identify")

    no_transcript_video = _insert_video_and_queue(
        factory, channel_id, "no_transcript", attempt_count=2
    )
    _run_stage(
        queue,
        store,
        _FixedExtractor(_failed_result(TranscriptFailure.NO_TRANSCRIPT)),
        _FixedExtractor(_failed_result(TranscriptFailure.NO_TRANSCRIPT)),
    )
    _assert_queue_state(
        factory, no_transcript_video, "transcript", "failed", "no_transcript", 3
    )
    _assert_no_transcript(factory, no_transcript_video)
    checks.append("no_transcript_stops_before_identify")

    rate_limited_video = _insert_video_and_queue(factory, channel_id, "rate_limited")
    pending_video = _insert_video_and_queue(factory, channel_id, "unclaimed")
    result = _run_stage(
        queue,
        store,
        _FixedExtractor(_failed_result(TranscriptFailure.ACCESS_RESTRICTED)),
        _FixedExtractor(_failed_result(TranscriptFailure.RATE_LIMITED)),
    )
    _require(result.rate_limited_stop_count == 1)
    _assert_queue_state(
        factory, rate_limited_video, "transcript", "pending", "rate_limited", 1
    )
    _assert_queue_state(factory, pending_video, "transcript", "pending", None, 0)
    checks.append("rate_limit_preserves_unclaimed_work")

    stuck_video = _insert_video_and_queue(factory, channel_id, "stuck")
    _mark_queue_stuck(factory, stuck_video)
    transition = queue.recover_one_stuck_transcript()
    _require(transition is not None and transition.status == "pending")
    _assert_queue_state(
        factory, stuck_video, "transcript", "pending", "worker_stalled", 1
    )
    checks.append("stuck_work_recovers_with_retry_policy")

    replacement_video = _insert_video(factory, channel_id, "replacement")
    store.replace_success(
        replacement_video, TranscriptSource.LIBRARY, _successful_result("first")
    )
    store.replace_success(
        replacement_video,
        TranscriptSource.YT_DLP,
        TranscriptExtractionResult.succeeded(
            (TranscriptSegment(0, 0, 1000, "replacement segment"),)
        ),
    )
    _assert_transcript(factory, replacement_video, TranscriptSource.YT_DLP, 1)
    checks.append("latest_success_replaces_segments_atomically")

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
    library: _TranscriptExtractor,
    ytdlp: _TranscriptExtractor,
):
    return TranscriptStageRunner(queue, store, library, ytdlp).run()


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
    video_id = _insert_row(
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
    return video_id


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
        raise RuntimeError("verification database did not return an inserted row ID")
    return identifier


def _verification_youtube_video_id(label: str) -> str:
    values = {
        "library": "verify00001",
        "fallback": "verify00002",
        "no_transcript": "verify00003",
        "rate_limited": "verify00004",
        "unclaimed": "verify00005",
        "stuck": "verify00006",
        "replacement": "verify00007",
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


def _assert_transcript(
    factory: MySqlConnectionFactory,
    video_id: int,
    source: TranscriptSource,
    segment_count: int,
) -> None:
    row = _fetch_one(
        factory,
        """
        SELECT t.source, COUNT(s.id)
        FROM video_transcripts t
        LEFT JOIN video_transcript_segments s ON s.video_id = t.video_id
        WHERE t.video_id = %s
        GROUP BY t.source
        """,
        (video_id,),
    )
    _require(row == (source.value, segment_count))


def _assert_no_transcript(factory: MySqlConnectionFactory, video_id: int) -> None:
    row = _fetch_one(
        factory,
        "SELECT COUNT(*) FROM video_transcripts WHERE video_id = %s",
        (video_id,),
    )
    _require(row == (0,))


def _execute(
    factory: MySqlConnectionFactory, statement: str, values: tuple[object, ...]
) -> None:
    connection = factory.connect()
    cursor = connection.cursor()
    try:
        cursor.execute(statement, values)
        if cursor.rowcount != 1:
            raise RuntimeError("verification database did not update exactly one row")
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
    connection = factory.connect()
    cursor = connection.cursor()
    try:
        cursor.execute(statement, values)
        row = cursor.fetchone()
    finally:
        cursor.close()
        connection.close()
    if row is not None and not isinstance(row, tuple):
        raise RuntimeError("verification database returned an invalid row")
    return row


def _successful_result(label: str) -> TranscriptExtractionResult:
    return TranscriptExtractionResult.succeeded(
        (
            TranscriptSegment(0, 0, 1000, f"{label} first segment"),
            TranscriptSegment(1, 1000, 2000, f"{label} second segment"),
        )
    )


def _failed_result(failure: TranscriptFailure) -> TranscriptExtractionResult:
    return TranscriptExtractionResult.failed(failure)


def _require(condition: bool) -> None:
    if not condition:
        raise AssertionError("W-021 temporary database verification failed")


if __name__ == "__main__":
    main()

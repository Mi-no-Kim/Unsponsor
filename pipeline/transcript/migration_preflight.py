"""자막 출처 enum 마이그레이션 전, 보존해야 할 기존 값을 안전하게 점검한다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from common.config import load_settings
from common.mysql import MySqlConnectionFactory
from transcript.model import TranscriptSource


_SOURCE_COUNTS_SQL = """
SELECT source, COUNT(*)
FROM video_transcripts
GROUP BY source
"""


def collect_source_counts(connection_factory: MySqlConnectionFactory) -> dict[str, int]:
    """출처별 행 수만 읽는다. 영상 식별자·원문·예외 세부 내용은 다루지 않는다."""

    connection = connection_factory.connect()
    cursor = connection.cursor()
    try:
        cursor.execute(_SOURCE_COUNTS_SQL)
        rows = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    counts: dict[str, int] = {}
    for row in rows:
        if (
            not isinstance(row, tuple)
            or len(row) != 2
            or not isinstance(row[0], str)
            or isinstance(row[1], bool)
            or not isinstance(row[1], int)
            or row[1] < 0
        ):
            raise RuntimeError("database returned invalid transcript source counts")
        counts[row[0]] = row[1]
    return counts


def is_safe_to_migrate(source_counts: Mapping[str, int]) -> bool:
    """현재 W-021 enum에 없는 출처의 저장 행이 없을 때만 True를 반환한다."""

    supported_sources = {source.value for source in TranscriptSource}
    return all(source in supported_sources or count == 0 for source, count in source_counts.items())


def _format_summary(source_counts: Mapping[str, int]) -> str:
    """식별자·원문·미지원 출처명 없이 판단 결과만 표시한다."""

    supported_source_values = {source.value for source in TranscriptSource}
    supported_counts = ", ".join(
        f"{source.value}={source_counts.get(source.value, 0)}"
        for source in TranscriptSource
    )
    unsupported_row_count = sum(
        count
        for source, count in source_counts.items()
        if source not in supported_source_values
    )
    safe_to_migrate = unsupported_row_count == 0
    return (
        "Transcript source migration preflight: "
        f"{supported_counts}, "
        f"unsupported_source_rows={unsupported_row_count}, "
        f"safe_to_migrate={safe_to_migrate}."
    )


def main(argv: Sequence[str] | None = None) -> int:
    """로컬 DB가 source enum 축소를 안전하게 받을 수 있는지만 확인한다."""

    del argv
    pipeline_root = Path(__file__).resolve().parents[1]
    settings = load_settings(pipeline_root.parent, pipeline_root / "channels.local.json")
    source_counts = collect_source_counts(MySqlConnectionFactory(settings.mysql))
    print(_format_summary(source_counts))
    return 0 if is_safe_to_migrate(source_counts) else 2


if __name__ == "__main__":
    raise SystemExit(main())

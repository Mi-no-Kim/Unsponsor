"""큐의 transcript 단계를 실제 자막 확보·저장 경로로 실행한다."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from collector.processing_queue_worker import (
    ClaimedTranscriptJob,
    FailureTransition,
    ProcessingQueueWorker,
    QueueFailure,
)
from common.config import PipelineSettings, load_settings
from common.mysql import MySqlConnectionFactory
from transcript.library_extractor import LibraryTranscriptExtractor
from transcript.model import TranscriptExtractionResult, TranscriptFailure, TranscriptSource
from transcript.store import TranscriptStore
from transcript.ytdlp_extractor import YtDlpTranscriptExtractor


class TranscriptExtractor(Protocol):
    """실행기가 필요한 자막 추출기의 공통 경계다."""

    def extract(self, video_id: str) -> TranscriptExtractionResult:
        """영상 ID의 자막을 안전한 결과로 반환한다."""


class TranscriptQueue(Protocol):
    """자막 단계에서 필요한 큐 상태 전이만 표현한다."""

    def claim_next_transcript(self) -> ClaimedTranscriptJob | None:
        """다음 transcript 작업을 점유한다."""

    def complete_transcript(self, job: ClaimedTranscriptJob) -> None:
        """저장이 끝난 작업을 identify/pending으로 넘긴다."""

    def fail_transcript(
        self, job: ClaimedTranscriptJob, failure: QueueFailure
    ) -> FailureTransition:
        """안전한 실패 코드로 작업을 재시도 또는 종료한다."""

    def recover_one_stuck_transcript(self) -> FailureTransition | None:
        """멈춘 작업 하나를 재시도 정책으로 복구한다."""


class TranscriptPersistence(Protocol):
    """실행 중 조회·저장에 필요한 자막 DB 경계다."""

    def find_youtube_video_id(self, video_id: int) -> str | None:
        """내부 영상 ID의 외부 ID를 반환한다."""

    def replace_success(
        self,
        video_id: int,
        source: TranscriptSource,
        result: TranscriptExtractionResult,
    ) -> None:
        """성공 자막을 원자적으로 교체한다."""


@dataclass(frozen=True)
class TranscriptStageRunResult:
    """콘솔에 원문·식별자 없이 표시할 실행 요약이다."""

    library_count: int = 0
    ytdlp_count: int = 0
    retry_scheduled_count: int = 0
    failed_count: int = 0
    recovered_count: int = 0
    rate_limited_stop_count: int = 0

    def with_success(self, source: TranscriptSource) -> TranscriptStageRunResult:
        if source is TranscriptSource.LIBRARY:
            return TranscriptStageRunResult(
                library_count=self.library_count + 1,
                ytdlp_count=self.ytdlp_count,
                retry_scheduled_count=self.retry_scheduled_count,
                failed_count=self.failed_count,
                recovered_count=self.recovered_count,
                rate_limited_stop_count=self.rate_limited_stop_count,
            )
        return TranscriptStageRunResult(
            library_count=self.library_count,
            ytdlp_count=self.ytdlp_count + 1,
            retry_scheduled_count=self.retry_scheduled_count,
            failed_count=self.failed_count,
            recovered_count=self.recovered_count,
            rate_limited_stop_count=self.rate_limited_stop_count,
        )

    def with_failure(self, transition: FailureTransition) -> TranscriptStageRunResult:
        if transition.status == "pending":
            return TranscriptStageRunResult(
                library_count=self.library_count,
                ytdlp_count=self.ytdlp_count,
                retry_scheduled_count=self.retry_scheduled_count + 1,
                failed_count=self.failed_count,
                recovered_count=self.recovered_count,
                rate_limited_stop_count=self.rate_limited_stop_count,
            )
        return TranscriptStageRunResult(
            library_count=self.library_count,
            ytdlp_count=self.ytdlp_count,
            retry_scheduled_count=self.retry_scheduled_count,
            failed_count=self.failed_count + 1,
            recovered_count=self.recovered_count,
            rate_limited_stop_count=self.rate_limited_stop_count,
        )

    def with_recovery(self) -> TranscriptStageRunResult:
        return TranscriptStageRunResult(
            library_count=self.library_count,
            ytdlp_count=self.ytdlp_count,
            retry_scheduled_count=self.retry_scheduled_count,
            failed_count=self.failed_count,
            recovered_count=self.recovered_count + 1,
            rate_limited_stop_count=self.rate_limited_stop_count,
        )

    def with_rate_limited_stop(self) -> TranscriptStageRunResult:
        """현재 실행이 HTTP 429에 도달해 이후 큐 점유를 중단했음을 표시한다."""

        return TranscriptStageRunResult(
            library_count=self.library_count,
            ytdlp_count=self.ytdlp_count,
            retry_scheduled_count=self.retry_scheduled_count,
            failed_count=self.failed_count,
            recovered_count=self.recovered_count,
            rate_limited_stop_count=self.rate_limited_stop_count + 1,
        )


class TranscriptStageRunner:
    """라이브러리 → yt-dlp 순서로 transcript 큐를 처리한다."""

    def __init__(
        self,
        queue: TranscriptQueue,
        persistence: TranscriptPersistence,
        library_extractor: TranscriptExtractor,
        ytdlp_extractor: TranscriptExtractor,
    ) -> None:
        self._queue = queue
        self._persistence = persistence
        self._library_extractor = library_extractor
        self._ytdlp_extractor = ytdlp_extractor

    def run(self) -> TranscriptStageRunResult:
        """멈춘 작업을 먼저 복구하고, 현재 실행 가능한 작업을 모두 처리한다."""

        result = TranscriptStageRunResult()
        while self._queue.recover_one_stuck_transcript() is not None:
            result = result.with_recovery()

        while (job := self._queue.claim_next_transcript()) is not None:
            result, should_stop = self._run_claimed_job(result, job)
            if should_stop:
                return result.with_rate_limited_stop()
        return result

    def _run_claimed_job(
        self, result: TranscriptStageRunResult, job: ClaimedTranscriptJob
    ) -> tuple[TranscriptStageRunResult, bool]:
        try:
            youtube_video_id = self._persistence.find_youtube_video_id(job.video_id)
        except Exception:
            return (
                self._record_failure(result, job, TranscriptFailure.TRANSIENT_ERROR),
                False,
            )
        if youtube_video_id is None:
            return (
                self._record_failure(result, job, TranscriptFailure.INVALID_RESPONSE),
                False,
            )

        extraction_result, source = self._extract(youtube_video_id)
        if not extraction_result.is_success or source is None:
            return (
                self._record_failure(result, job, extraction_result.failure),
                extraction_result.failure is TranscriptFailure.RATE_LIMITED,
            )

        try:
            self._persistence.replace_success(job.video_id, source, extraction_result)
            self._queue.complete_transcript(job)
        except Exception:
            return (
                self._record_failure(result, job, TranscriptFailure.TRANSIENT_ERROR),
                False,
            )
        return result.with_success(source), False

    def _extract(
        self, youtube_video_id: str
    ) -> tuple[TranscriptExtractionResult, TranscriptSource | None]:
        try:
            library_result = self._library_extractor.extract(youtube_video_id)
        except Exception:
            library_result = TranscriptExtractionResult.failed(
                TranscriptFailure.TRANSIENT_ERROR
            )
        if library_result.is_success:
            return library_result, TranscriptSource.LIBRARY
        if library_result.failure is TranscriptFailure.RATE_LIMITED:
            return library_result, None

        try:
            ytdlp_result = self._ytdlp_extractor.extract(youtube_video_id)
        except Exception:
            ytdlp_result = TranscriptExtractionResult.failed(
                TranscriptFailure.TRANSIENT_ERROR
            )
        if ytdlp_result.is_success:
            return ytdlp_result, TranscriptSource.YT_DLP
        return ytdlp_result, None

    def _record_failure(
        self,
        result: TranscriptStageRunResult,
        job: ClaimedTranscriptJob,
        failure: TranscriptFailure | None,
    ) -> TranscriptStageRunResult:
        safe_failure = failure or TranscriptFailure.TRANSIENT_ERROR
        transition = self._queue.fail_transcript(job, QueueFailure(safe_failure.value))
        return result.with_failure(transition)


def run_transcript_stage(
    settings: PipelineSettings,
    *,
    provider_home: Path | None = None,
) -> TranscriptStageRunResult:
    """설정된 로컬 DB에서 transcript 단계 실행기를 구성해 실행한다."""

    connection_factory = MySqlConnectionFactory(settings.mysql)
    return TranscriptStageRunner(
        ProcessingQueueWorker(connection_factory, settings.queue),
        TranscriptStore(connection_factory),
        LibraryTranscriptExtractor(),
        YtDlpTranscriptExtractor(provider_home=provider_home),
    ).run()


def _print_summary(result: TranscriptStageRunResult) -> None:
    print(
        "Transcript summary: "
        f"library={result.library_count}, "
        f"yt_dlp={result.ytdlp_count}, "
        f"retry_scheduled={result.retry_scheduled_count}, "
        f"failed={result.failed_count}, "
        f"recovered={result.recovered_count}, "
        f"rate_limited_stop={result.rate_limited_stop_count}."
    )


def main(argv: Sequence[str] | None = None) -> None:
    """비공개 로컬 설정으로 큐의 transcript 단계를 실행한다."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider-home",
        type=Path,
        help="필요할 때만 시작할 bgutil Provider의 빌드 디렉터리",
    )
    arguments = parser.parse_args(argv)

    pipeline_root = Path(__file__).resolve().parents[1]
    settings = load_settings(pipeline_root.parent, pipeline_root / "channels.local.json")
    _print_summary(run_transcript_stage(settings, provider_home=arguments.provider_home))


if __name__ == "__main__":
    main()

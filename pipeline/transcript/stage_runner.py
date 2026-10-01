"""큐의 transcript 단계를 실제 자막 확보·저장 경로로 실행한다."""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, Sequence

from collector.processing_queue_worker import (
    ClaimedTranscriptJob,
    FailureTransition,
    ProcessingQueueWorker,
    QueueFailure,
)
from common.config import (
    ConfigurationError,
    DataImpulseProxySettings,
    PipelineSettings,
    load_dataimpulse_proxy_settings,
    load_settings,
)
from common.restricted_error_log import configure_restricted_error_log
from common.mysql import MySqlConnectionFactory
from transcript.dataimpulse_proxy import (
    DataImpulseLibraryTranscriptExtractor,
    dataimpulse_proxy_secret_values,
)
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
    library_failure_count: int = 0
    ytdlp_failure_count: int = 0
    retry_scheduled_count: int = 0
    failed_count: int = 0
    recovered_count: int = 0
    rate_limited_stop_count: int = 0

    def with_success(self, source: TranscriptSource) -> TranscriptStageRunResult:
        if source is TranscriptSource.LIBRARY:
            return replace(self, library_count=self.library_count + 1)
        return replace(self, ytdlp_count=self.ytdlp_count + 1)

    def with_extraction_failure(
        self, source: TranscriptSource
    ) -> TranscriptStageRunResult:
        if source is TranscriptSource.LIBRARY:
            return replace(
                self, library_failure_count=self.library_failure_count + 1
            )
        return replace(self, ytdlp_failure_count=self.ytdlp_failure_count + 1)

    def with_failure(self, transition: FailureTransition) -> TranscriptStageRunResult:
        if transition.status == "pending":
            return replace(
                self, retry_scheduled_count=self.retry_scheduled_count + 1
            )
        return replace(self, failed_count=self.failed_count + 1)

    def with_recovery(self) -> TranscriptStageRunResult:
        return replace(self, recovered_count=self.recovered_count + 1)

    def with_rate_limited_stop(self) -> TranscriptStageRunResult:
        """현재 실행이 HTTP 429에 도달해 이후 큐 점유를 중단했음을 표시한다."""

        return replace(
            self, rate_limited_stop_count=self.rate_limited_stop_count + 1
        )


class TranscriptStageRunner:
    """DataImpulse 라이브러리 뒤 yt-dlp로 transcript 큐를 처리한다."""

    def __init__(
        self,
        queue: TranscriptQueue,
        persistence: TranscriptPersistence,
        ytdlp_extractor: TranscriptExtractor,
        *,
        library_extractor: TranscriptExtractor | None = None,
        error_logger: logging.Logger | None = None,
    ) -> None:
        self._queue = queue
        self._persistence = persistence
        self._library_extractor = library_extractor
        self._ytdlp_extractor = ytdlp_extractor
        self._error_logger = error_logger

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
            self._log_safe_error("could not read queued video's YouTube ID")
            return (
                self._record_failure(result, job, TranscriptFailure.TRANSIENT_ERROR),
                False,
            )
        if youtube_video_id is None:
            return (
                self._record_failure(result, job, TranscriptFailure.INVALID_RESPONSE),
                False,
            )

        library_result = self._extract_library(youtube_video_id)
        if library_result.is_success:
            return self._persist_success(
                result,
                job,
                TranscriptSource.LIBRARY,
                library_result,
            )

        result = result.with_extraction_failure(TranscriptSource.LIBRARY)
        if library_result.failure is TranscriptFailure.RATE_LIMITED:
            return (
                self._record_failure(result, job, library_result.failure),
                True,
            )

        ytdlp_result = self._extract_ytdlp(youtube_video_id)
        if not ytdlp_result.is_success:
            result = result.with_extraction_failure(TranscriptSource.YT_DLP)
            return (
                self._record_failure(result, job, ytdlp_result.failure),
                ytdlp_result.failure is TranscriptFailure.RATE_LIMITED,
            )
        return self._persist_success(
            result,
            job,
            TranscriptSource.YT_DLP,
            ytdlp_result,
        )

    def _persist_success(
        self,
        result: TranscriptStageRunResult,
        job: ClaimedTranscriptJob,
        source: TranscriptSource,
        extraction_result: TranscriptExtractionResult,
    ) -> tuple[TranscriptStageRunResult, bool]:
        try:
            self._persistence.replace_success(
                job.video_id, source, extraction_result
            )
            self._queue.complete_transcript(job)
        except Exception:
            self._log_safe_error("could not persist a successful transcript")
            return (
                self._record_failure(result, job, TranscriptFailure.TRANSIENT_ERROR),
                False,
            )
        return result.with_success(source), False

    def _log_safe_error(self, message: str) -> None:
        if self._error_logger is not None:
            self._error_logger.error(message)

    def _extract_library(self, youtube_video_id: str) -> TranscriptExtractionResult:
        if self._library_extractor is None:
            return TranscriptExtractionResult.failed(
                TranscriptFailure.TRANSIENT_ERROR
            )
        try:
            return self._library_extractor.extract(youtube_video_id)
        except Exception:
            self._log_safe_error(
                "DataImpulse library extractor raised an unexpected error"
            )
            return TranscriptExtractionResult.failed(
                TranscriptFailure.TRANSIENT_ERROR
            )

    def _extract_ytdlp(self, youtube_video_id: str) -> TranscriptExtractionResult:
        try:
            return self._ytdlp_extractor.extract(youtube_video_id)
        except Exception:
            self._log_safe_error(
                "yt-dlp transcript extractor raised an unexpected error"
            )
            return TranscriptExtractionResult.failed(
                TranscriptFailure.TRANSIENT_ERROR
            )

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
    dataimpulse_settings: DataImpulseProxySettings | None = None,
    provider_home: Path | None = None,
    cookie_file: Path | None = None,
    error_logger: logging.Logger | None = None,
) -> TranscriptStageRunResult:
    """설정된 로컬 DB에서 transcript 단계 실행기를 구성해 실행한다."""

    connection_factory = MySqlConnectionFactory(settings.mysql)
    library_extractor = (
        DataImpulseLibraryTranscriptExtractor(
            dataimpulse_settings,
            error_logger=error_logger,
        )
        if dataimpulse_settings is not None
        else None
    )
    return TranscriptStageRunner(
        ProcessingQueueWorker(connection_factory, settings.queue),
        TranscriptStore(connection_factory),
        YtDlpTranscriptExtractor(
            provider_home=provider_home,
            cookie_file=cookie_file,
            error_logger=error_logger,
        ),
        library_extractor=library_extractor,
        error_logger=error_logger,
    ).run()


def _print_summary(result: TranscriptStageRunResult) -> None:
    print(
        "Transcript summary: "
        f"library={result.library_count}, "
        f"yt_dlp={result.ytdlp_count}, "
        f"library_failures={result.library_failure_count}, "
        f"yt_dlp_failures={result.ytdlp_failure_count}, "
        f"retry_scheduled={result.retry_scheduled_count}, "
        f"failed={result.failed_count}, "
        f"recovered={result.recovered_count}, "
        f"rate_limited_stop={result.rate_limited_stop_count}."
    )


def _default_error_log_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Unsponsor" / "logs" / "transcript-errors.log"
    return Path.cwd() / ".unsponsor" / "logs" / "transcript-errors.log"


def main(argv: Sequence[str] | None = None) -> None:
    """비공개 로컬 설정으로 큐의 transcript 단계를 실행한다."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--provider-home",
        type=Path,
        help="필요할 때만 시작할 bgutil Provider의 빌드 디렉터리",
    )
    parser.add_argument(
        "--cookie-file",
        type=Path,
        help="yt-dlp에만 전달할 Netscape 형식 YouTube 쿠키 파일",
    )
    parser.add_argument(
        "--error-log",
        type=Path,
        default=_default_error_log_path(),
        help="상세 외부 오류를 기록할 로컬 전용 파일",
    )
    arguments = parser.parse_args(argv)
    if arguments.cookie_file is not None and not arguments.cookie_file.is_file():
        parser.error("--cookie-file must name an existing file")

    pipeline_root = Path(__file__).resolve().parents[1]
    settings = load_settings(pipeline_root.parent, pipeline_root / "channels.local.json")
    try:
        dataimpulse_settings = load_dataimpulse_proxy_settings(pipeline_root.parent)
    except ConfigurationError:
        dataimpulse_settings = None
    error_logger = configure_restricted_error_log(
        arguments.error_log,
        cookie_file=arguments.cookie_file,
        secret_values=(
            dataimpulse_proxy_secret_values(dataimpulse_settings)
            if dataimpulse_settings is not None
            else ()
        ),
    )
    _print_summary(
        run_transcript_stage(
            settings,
            dataimpulse_settings=dataimpulse_settings,
            provider_home=arguments.provider_home,
            cookie_file=arguments.cookie_file,
            error_logger=error_logger,
        )
    )


if __name__ == "__main__":
    main()

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import logging
import unittest

from collector.processing_queue_worker import (
    ClaimedTranscriptJob,
    FailureTransition,
    QueueFailure,
)
from transcript.model import (
    TranscriptExtractionResult,
    TranscriptFailure,
    TranscriptSegment,
    TranscriptSource,
)
from transcript.stage_runner import TranscriptStageRunner, _print_summary
from transcript.store import TranscriptClaimLostError


class TranscriptStageRunnerTests(unittest.TestCase):
    def test_persists_a_library_success_without_calling_ytdlp(self) -> None:
        queue = _Queue([ClaimedTranscriptJob(1, 101, 0)])
        persistence = _Persistence({101: "video000001"})
        library = _Extractor(_success())
        ytdlp = _Extractor(_success())

        result = TranscriptStageRunner(
            queue,
            persistence,
            ytdlp,
            library_extractor=library,
        ).run()

        self.assertEqual(result.library_count, 1)
        self.assertEqual(result.ytdlp_count, 0)
        self.assertEqual(
            persistence.saved,
            [(ClaimedTranscriptJob(1, 101, 0), TranscriptSource.LIBRARY, _success())],
        )
        self.assertEqual(library.calls, ["video000001"])
        self.assertEqual(ytdlp.calls, [])

    def test_falls_back_to_ytdlp_and_persists_its_source(self) -> None:
        queue = _Queue([ClaimedTranscriptJob(1, 101, 0)])
        persistence = _Persistence({101: "video000001"})
        library = _Extractor(_failure(TranscriptFailure.ACCESS_RESTRICTED))
        ytdlp = _Extractor(_success())

        result = TranscriptStageRunner(
            queue,
            persistence,
            ytdlp,
            library_extractor=library,
        ).run()

        self.assertEqual(result.library_count, 0)
        self.assertEqual(result.library_failure_count, 1)
        self.assertEqual(result.ytdlp_count, 1)
        self.assertEqual(
            persistence.saved,
            [(ClaimedTranscriptJob(1, 101, 0), TranscriptSource.YT_DLP, _success())],
        )
        self.assertEqual(library.calls, ["video000001"])
        self.assertEqual(ytdlp.calls, ["video000001"])

    def test_missing_dataimpulse_configuration_falls_back_to_ytdlp(self) -> None:
        queue = _Queue([ClaimedTranscriptJob(1, 101, 0)])
        persistence = _Persistence({101: "video000001"})
        ytdlp = _Extractor(_success())

        result = TranscriptStageRunner(queue, persistence, ytdlp).run()

        self.assertEqual(result.library_failure_count, 1)
        self.assertEqual(result.ytdlp_count, 1)
        self.assertEqual(persistence.saved[0][1], TranscriptSource.YT_DLP)

    def test_dataimpulse_authentication_error_falls_back_without_logging_secrets(self) -> None:
        queue = _Queue([ClaimedTranscriptJob(1, 101, 0)])
        persistence = _Persistence({101: "video000001"})
        ytdlp = _Extractor(_success())
        captured = StringIO()
        logger = logging.getLogger(self.id())
        logger.setLevel(logging.ERROR)
        logger.propagate = False
        handler = logging.StreamHandler(captured)
        logger.addHandler(handler)
        try:
            result = TranscriptStageRunner(
                queue,
                persistence,
                ytdlp,
                library_extractor=_RaisingExtractor(
                    RuntimeError(
                        "proxy auth failed for http://private-login:private-password@"
                        "gw.dataimpulse.com video000001 private transcript"
                    )
                ),
                error_logger=logger,
            ).run()
        finally:
            logger.removeHandler(handler)
            handler.close()

        self.assertEqual(result.library_failure_count, 1)
        self.assertEqual(result.ytdlp_count, 1)
        self.assertIn("DataImpulse library extractor", captured.getvalue())
        for private_value in (
            "private-login",
            "private-password",
            "video000001",
            "private transcript",
        ):
            self.assertNotIn(private_value, captured.getvalue())

    def test_records_only_the_safe_ytdlp_failure(self) -> None:
        queue = _Queue([ClaimedTranscriptJob(1, 101, 2)], failure_status="failed")
        persistence = _Persistence({101: "video000001"})
        ytdlp = _Extractor(_failure(TranscriptFailure.RATE_LIMITED))

        result = TranscriptStageRunner(
            queue,
            persistence,
            ytdlp,
            library_extractor=_Extractor(
                _failure(TranscriptFailure.ACCESS_RESTRICTED)
            ),
        ).run()

        self.assertEqual(result.failed_count, 1)
        self.assertEqual(result.ytdlp_failure_count, 1)
        self.assertEqual(queue.failures, [(ClaimedTranscriptJob(1, 101, 2), "rate_limited")])
        self.assertEqual(persistence.saved, [])

    def test_rate_limit_stops_the_batch_without_claiming_another_video(self) -> None:
        first_job = ClaimedTranscriptJob(1, 101, 0)
        second_job = ClaimedTranscriptJob(2, 102, 0)
        queue = _Queue([first_job, second_job])
        persistence = _Persistence({101: "video000001", 102: "video000002"})
        ytdlp = _Extractor(_failure(TranscriptFailure.RATE_LIMITED))

        result = TranscriptStageRunner(
            queue,
            persistence,
            ytdlp,
            library_extractor=_Extractor(
                _failure(TranscriptFailure.ACCESS_RESTRICTED)
            ),
        ).run()

        self.assertEqual(result.rate_limited_stop_count, 1)
        self.assertEqual(result.retry_scheduled_count, 1)
        self.assertEqual(queue.failures, [(first_job, "rate_limited")])
        self.assertEqual(ytdlp.calls, ["video000001"])
        self.assertEqual(queue.remaining_jobs, [second_job])

    def test_library_rate_limit_stops_before_ytdlp_and_the_next_claim(self) -> None:
        first_job = ClaimedTranscriptJob(1, 101, 0)
        second_job = ClaimedTranscriptJob(2, 102, 0)
        queue = _Queue([first_job, second_job])
        persistence = _Persistence({101: "video000001", 102: "video000002"})
        library = _Extractor(_failure(TranscriptFailure.RATE_LIMITED))
        ytdlp = _Extractor(_success())

        result = TranscriptStageRunner(
            queue,
            persistence,
            ytdlp,
            library_extractor=library,
        ).run()

        self.assertEqual(result.rate_limited_stop_count, 1)
        self.assertEqual(result.library_failure_count, 1)
        self.assertEqual(queue.failures, [(first_job, "rate_limited")])
        self.assertEqual(library.calls, ["video000001"])
        self.assertEqual(ytdlp.calls, [])
        self.assertEqual(queue.remaining_jobs, [second_job])

    def test_converts_storage_failure_to_a_safe_retry(self) -> None:
        queue = _Queue([ClaimedTranscriptJob(1, 101, 0)], failure_status="pending")
        persistence = _Persistence({101: "video000001"}, storage_error=RuntimeError("private"))
        ytdlp = _Extractor(_success())

        result = TranscriptStageRunner(queue, persistence, ytdlp).run()

        self.assertEqual(result.retry_scheduled_count, 1)
        self.assertEqual(queue.failures, [(ClaimedTranscriptJob(1, 101, 0), "transient_error")])

    def test_discards_a_success_when_the_worker_has_lost_its_claim(self) -> None:
        job = ClaimedTranscriptJob(1, 101, 0)
        queue = _Queue([job])
        persistence = _Persistence(
            {101: "video000001"},
            storage_error=TranscriptClaimLostError("claim changed"),
        )

        result = TranscriptStageRunner(
            queue,
            persistence,
            _Extractor(_success()),
        ).run()

        self.assertEqual(result.library_count, 0)
        self.assertEqual(result.ytdlp_count, 0)
        self.assertEqual(result.retry_scheduled_count, 0)
        self.assertEqual(queue.failures, [])
        self.assertEqual(persistence.saved, [])

    def test_missing_video_metadata_never_starts_an_extractor(self) -> None:
        queue = _Queue([ClaimedTranscriptJob(1, 101, 0)], failure_status="failed")
        persistence = _Persistence({})
        ytdlp = _Extractor(_success())

        result = TranscriptStageRunner(queue, persistence, ytdlp).run()

        self.assertEqual(result.failed_count, 1)
        self.assertEqual(queue.failures, [(ClaimedTranscriptJob(1, 101, 0), "invalid_response")])
        self.assertEqual(ytdlp.calls, [])

    def test_metadata_lookup_failure_becomes_a_safe_retry(self) -> None:
        queue = _Queue([ClaimedTranscriptJob(1, 101, 0)], failure_status="pending")
        persistence = _Persistence({}, lookup_error=RuntimeError("private database error"))
        ytdlp = _Extractor(_success())

        result = TranscriptStageRunner(queue, persistence, ytdlp).run()

        self.assertEqual(result.retry_scheduled_count, 1)
        self.assertEqual(queue.failures, [(ClaimedTranscriptJob(1, 101, 0), "transient_error")])
        self.assertEqual(ytdlp.calls, [])

    def test_recovers_every_stuck_job_before_claiming_new_work(self) -> None:
        queue = _Queue([], recovery_count=2)
        persistence = _Persistence({})

        result = TranscriptStageRunner(
            queue,
            persistence,
            _Extractor(_success()),
        ).run()

        self.assertEqual(result.recovered_count, 2)

    def test_summary_never_prints_video_identifiers_or_transcript_text(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            _print_summary(
                TranscriptStageRunner(
                    _Queue([]),
                    _Persistence({}),
                    _Extractor(_success()),
                ).run()
            )

        self.assertIn("yt_dlp=0", output.getvalue())
        self.assertIn("library=0", output.getvalue())
        self.assertIn("library_failures=0", output.getvalue())
        self.assertIn("yt_dlp_failures=0", output.getvalue())
        self.assertIn("rate_limited_stop=0", output.getvalue())
        self.assertNotIn("video000001", output.getvalue())
        self.assertNotIn("private transcript", output.getvalue())


class _Queue:
    def __init__(
        self,
        jobs: list[ClaimedTranscriptJob],
        *,
        failure_status: str = "pending",
        recovery_count: int = 0,
    ) -> None:
        self._jobs = jobs.copy()
        self._failure_status = failure_status
        self._recovery_count = recovery_count
        self.failures: list[tuple[ClaimedTranscriptJob, str]] = []

    @property
    def remaining_jobs(self) -> list[ClaimedTranscriptJob]:
        return self._jobs.copy()

    def claim_next_transcript(self) -> ClaimedTranscriptJob | None:
        return self._jobs.pop(0) if self._jobs else None

    def fail_transcript(
        self, job: ClaimedTranscriptJob, failure: QueueFailure
    ) -> FailureTransition:
        self.failures.append((job, failure.code))
        return FailureTransition(job.queue_id, job.attempt_count + 1, self._failure_status)

    def recover_one_stuck_transcript(self) -> FailureTransition | None:
        if self._recovery_count == 0:
            return None
        self._recovery_count -= 1
        return FailureTransition(99, 1, "pending")


class _Persistence:
    def __init__(
        self,
        video_ids: dict[int, str],
        *,
        storage_error: Exception | None = None,
        lookup_error: Exception | None = None,
    ) -> None:
        self._video_ids = video_ids
        self._storage_error = storage_error
        self._lookup_error = lookup_error
        self.saved: list[
            tuple[ClaimedTranscriptJob, TranscriptSource, TranscriptExtractionResult]
        ] = []

    def find_youtube_video_id(self, video_id: int) -> str | None:
        if self._lookup_error is not None:
            raise self._lookup_error
        return self._video_ids.get(video_id)

    def complete_success(
        self,
        job: ClaimedTranscriptJob,
        source: TranscriptSource,
        result: TranscriptExtractionResult,
    ) -> None:
        if self._storage_error is not None:
            raise self._storage_error
        self.saved.append((job, source, result))


class _Extractor:
    def __init__(self, result: TranscriptExtractionResult) -> None:
        self._result = result
        self.calls: list[str] = []

    def extract(self, video_id: str) -> TranscriptExtractionResult:
        self.calls.append(video_id)
        return self._result


class _RaisingExtractor:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def extract(self, video_id: str) -> TranscriptExtractionResult:
        raise self._error


def _success() -> TranscriptExtractionResult:
    return TranscriptExtractionResult.succeeded(
        (TranscriptSegment(0, 0, 1000, "private transcript"),)
    )


def _failure(failure: TranscriptFailure) -> TranscriptExtractionResult:
    return TranscriptExtractionResult.failed(failure)

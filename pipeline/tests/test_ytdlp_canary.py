from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from transcript.library_extractor import TranscriptExtractionResult, TranscriptFailure
from transcript.model import TranscriptSegment
from transcript.ytdlp_canary import YtDlpCanary


class _Extractor:
    def __init__(
        self,
        token_free: TranscriptExtractionResult,
        potoken: TranscriptExtractionResult,
    ) -> None:
        self._token_free = token_free
        self._potoken = potoken

    def extract_token_free(
        self, video_id: str, *, language_codes: tuple[str, ...]
    ) -> TranscriptExtractionResult:
        return self._token_free

    def extract_with_provider(
        self, video_id: str, *, language_codes: tuple[str, ...]
    ) -> TranscriptExtractionResult:
        return self._potoken


class _ExtractorFactory:
    def __init__(
        self,
        token_free: TranscriptExtractionResult,
        potoken: TranscriptExtractionResult,
    ) -> None:
        self._token_free = token_free
        self._potoken = potoken
        self.provider_homes: list[Path | None] = []

    def __call__(self, provider_home: Path | None, node_executable: str) -> _Extractor:
        self.provider_homes.append(provider_home)
        return _Extractor(self._token_free, self._potoken)


class YtDlpCanaryTests(unittest.TestCase):
    def test_reports_two_successful_paths_without_transcript_or_video_id(self) -> None:
        factory = _ExtractorFactory(
            _success("one\ntwo"),
            _success("one\ntwo\nthree"),
        )

        with _provider_home() as provider_home:
            report = self._canary(provider_home, factory).run("video000001")

        self.assertEqual(report.exit_code, 0)
        self.assertEqual(report.environment_code, "ok")
        self.assertEqual(report.token_free.text_length, 7)
        self.assertEqual(report.potoken.text_length, 13)
        encoded = json.dumps(report.to_dict())
        self.assertNotIn("one", encoded)
        self.assertNotIn("video000001", encoded)
        self.assertEqual(factory.provider_homes, [None, provider_home])

    def test_reports_provider_unready_without_attempting_potoken_path(self) -> None:
        factory = _ExtractorFactory(
            _success("text"),
            _success("text"),
        )

        with TemporaryDirectory() as directory:
            report = self._canary(Path(directory), factory).run("video000001")

        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.environment_code, "provider_unready")
        self.assertEqual(report.potoken.code, "provider_unready")
        self.assertEqual(factory.provider_homes, [None])

    def test_rate_limit_stops_the_potoken_request_in_the_same_canary(self) -> None:
        factory = _ExtractorFactory(
            TranscriptExtractionResult.failed(TranscriptFailure.RATE_LIMITED),
            _success("text"),
        )

        with _provider_home() as provider_home:
            report = self._canary(provider_home, factory).run("video000001")

        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.token_free.code, "rate_limited")
        self.assertEqual(report.potoken.code, "skipped_rate_limited")
        self.assertEqual(factory.provider_homes, [None])

    def test_classifies_potoken_requirement_without_exposing_failure_detail(self) -> None:
        factory = _ExtractorFactory(
            _success("text"),
            TranscriptExtractionResult.failed(TranscriptFailure.PO_TOKEN_REQUIRED),
        )

        with _provider_home() as provider_home:
            report = self._canary(provider_home, factory).run("video000001")

        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.potoken.code, "token_mint_failed")
        self.assertNotIn("po_token_required", json.dumps(report.to_dict()))

    def test_reports_invalid_transcript_and_newer_upstream_release(self) -> None:
        factory = _ExtractorFactory(
            TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE),
            _success("text"),
        )

        with _provider_home() as provider_home:
            report = self._canary(
                provider_home, factory, upstream_version="2026.10.01"
            ).run("video000001")

        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.token_free.code, "invalid_transcript")
        self.assertEqual(report.upstream_code, "upstream_version_available")

    def test_uses_update_exit_code_when_paths_are_healthy(self) -> None:
        factory = _ExtractorFactory(
            _success("text"),
            _success("text"),
        )

        with _provider_home() as provider_home:
            report = self._canary(
                provider_home, factory, upstream_version="2026.10.01"
            ).run("video000001")

        self.assertEqual(report.exit_code, 3)
        self.assertEqual(report.upstream_code, "upstream_version_available")

    def test_reports_failed_upstream_check_as_a_safe_error_code(self) -> None:
        factory = _ExtractorFactory(
            _success("text"),
            _success("text"),
        )

        with _provider_home() as provider_home:
            report = self._canary(
                provider_home, factory, upstream_version=None
            ).run("video000001")

        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.upstream_code, "upstream_version_check_failed")

    def test_rejects_video_id_before_it_can_reach_a_result(self) -> None:
        with _provider_home() as provider_home:
            with self.assertRaises(ValueError):
                self._canary(
                    provider_home,
                    _ExtractorFactory(
                        _success("text"),
                        _success("text"),
                    ),
                ).run("not-a-video-id")

    def _canary(
        self,
        provider_home: Path,
        factory: _ExtractorFactory,
        *,
        upstream_version: str | None = "2026.09.27",
    ) -> YtDlpCanary:
        return YtDlpCanary(
            provider_home=provider_home,
            extractor_factory=factory,
            locked_version_reader=lambda path: {
                "yt-dlp": "2026.09.27.232945.dev0",
                "bgutil-ytdlp-pot-provider": "2.0.0",
            },
            installed_version_reader=lambda name: {
                "yt-dlp": "2026.09.27.232945.dev0",
                "bgutil-ytdlp-pot-provider": "2.0.0",
            }.get(name),
            node_version_reader=lambda node: "v24.13.0",
            provider_version_reader=lambda home: "2.0.0",
            upstream_version_fetcher=lambda: upstream_version,
            monotonic_clock=_clock(),
        )


@contextmanager
def _provider_home():
    with TemporaryDirectory() as directory:
        provider_home = Path(directory)
        (provider_home / "build").mkdir()
        (provider_home / "build" / "main.js").write_text("", encoding="utf-8")
        yield provider_home


def _clock():
    values = iter((10.0, 10.125, 20.0, 20.250))
    return lambda: next(values)


def _success(text: str) -> TranscriptExtractionResult:
    return TranscriptExtractionResult.succeeded(
        tuple(
            TranscriptSegment(
                sequence=index,
                start_ms=index * 1000,
                end_ms=(index + 1) * 1000,
                text=line,
            )
            for index, line in enumerate(text.splitlines())
        )
    )


if __name__ == "__main__":
    unittest.main()

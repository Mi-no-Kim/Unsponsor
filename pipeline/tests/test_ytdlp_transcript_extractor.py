from __future__ import annotations

import json
import logging
from contextlib import AbstractContextManager, redirect_stderr
from io import StringIO
from pathlib import Path
import unittest
from urllib.error import HTTPError

from transcript.library_extractor import TranscriptExtractionResult, TranscriptFailure
from transcript.model import TranscriptSegment
from transcript.ytdlp_extractor import (
    ProviderUnavailable,
    YtDlpTranscriptExtractor,
)
from yt_dlp.utils import DownloadError


class _Downloader(AbstractContextManager["_Downloader"]):
    def __init__(self, options: dict[str, object], action: str) -> None:
        self.options = options
        self.action = action
        self.urls: list[list[str]] = []

    def __exit__(self, *args: object) -> None:
        return None

    def download(self, url_list: list[str]) -> int:
        self.urls.append(url_list)
        if self.action == "download_error":
            raise DownloadError("private transcript contents")
        if self.action == "pot_required":
            raise DownloadError("PO Token required for this request")
        if self.action == "pot_warning_no_subtitle":
            self.options["logger"].warning("PO Token required for this request")
            return 0
        if self.action == "wrapped_429":
            cause = HTTPError("https://private.example", 429, "private", {}, None)
            cause.close()
            raise DownloadError("private transcript contents", (HTTPError, cause, None))
        if self.action == "message_429":
            raise DownloadError("private request failed: HTTP Error 429")
        if self.action == "direct_429":
            error = HTTPError("https://private.example", 429, "private", {}, None)
            error.close()
            raise error
        if self.action == "write_error_to_logger":
            self.options["logger"].error("video000001 private upstream error")
            return 1
        if self.action == "nonzero":
            return 1
        if self.action == "write_json3":
            directory = Path(self.options["paths"]["home"])
            (directory / "video000001.ko.json3").write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "tStartMs": 1250,
                                "dDurationMs": 500,
                                "segs": [{"utf8": " First\nfragment "}],
                            },
                            {
                                "tStartMs": 2000,
                                "dDurationMs": 375,
                                "segs": [{"utf8": " second   fragment "}],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
        if self.action == "write_srv1":
            directory = Path(self.options["paths"]["home"])
            (directory / "video000001.ko.srv1").write_text(
                """<?xml version="1.0" encoding="utf-8" ?>
<transcript>
<text start="1.250" dur="0.500"> First &amp;\nfragment </text>
<text start="2" dur="0.375"> second   fragment </text>
</transcript>""",
                encoding="utf-8",
            )
        if self.action == "write_invalid_srv1":
            directory = Path(self.options["paths"]["home"])
            (directory / "video000001.ko.srv1").write_text(
                '<transcript><text start="1">private transcript</text></transcript>',
                encoding="utf-8",
            )
        if self.action == "write_invalid_json3":
            directory = Path(self.options["paths"]["home"])
            (directory / "video000001.ko.json3").write_text(
                "not json", encoding="utf-8"
            )
        if self.action == "write_json3_without_timestamps":
            directory = Path(self.options["paths"]["home"])
            (directory / "video000001.ko.json3").write_text(
                json.dumps({"events": [{"segs": [{"utf8": "private transcript"}]}]}),
                encoding="utf-8",
            )
        return 0


class _DownloaderFactory:
    def __init__(self, *actions: str) -> None:
        self.actions = list(actions)
        self.downloaders: list[_Downloader] = []

    def __call__(self, options: dict[str, object]) -> _Downloader:
        downloader = _Downloader(options, self.actions.pop(0))
        self.downloaders.append(downloader)
        return downloader


class _ProviderSession(AbstractContextManager["_ProviderSession"]):
    base_url = "http://127.0.0.1:49000"

    def __exit__(self, *args: object) -> None:
        return None


class _UnavailableProviderSession(AbstractContextManager["_UnavailableProviderSession"]):
    @property
    def base_url(self) -> str:
        raise AssertionError("unavailable provider has no URL")

    def __enter__(self) -> "_UnavailableProviderSession":
        raise ProviderUnavailable("private provider details")

    def __exit__(self, *args: object) -> None:
        return None


class YtDlpTranscriptExtractorTests(unittest.TestCase):
    def test_extract_prefers_and_normalizes_a_token_free_srv1_subtitle(self) -> None:
        downloader_factory = _DownloaderFactory("write_srv1")

        result = YtDlpTranscriptExtractor(
            ytdlp_factory=downloader_factory
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.succeeded(
                (
                    TranscriptSegment(0, 1250, 1750, "First & fragment"),
                    TranscriptSegment(1, 2000, 2375, "second fragment"),
                )
            ),
        )
        self.assertEqual(len(downloader_factory.downloaders), 1)
        options = downloader_factory.downloaders[0].options
        self.assertEqual(
            options["extractor_args"], {"youtube": {"skip": ["translated_subs"]}}
        )
        self.assertEqual(options["retries"], 0)
        self.assertEqual(options["extractor_retries"], 0)
        self.assertEqual(options["fragment_retries"], 0)
        self.assertEqual(options["sleep_interval_requests"], 2.0)
        self.assertEqual(options["sleep_interval_subtitles"], 35.0)
        self.assertEqual(options["subtitlesformat"], "srv1/json3")
        self.assertNotIn("cookiefile", options)
        self.assertFalse(Path(options["paths"]["home"]).exists())

    def test_extract_uses_json3_as_a_compatible_fallback(self) -> None:
        result = YtDlpTranscriptExtractor(
            ytdlp_factory=_DownloaderFactory("write_json3")
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.succeeded(
                (
                    TranscriptSegment(0, 1250, 1750, "First fragment"),
                    TranscriptSegment(1, 2000, 2375, "second fragment"),
                )
            ),
        )

    def test_extract_passes_an_explicit_cookie_file_only_to_ytdlp(self) -> None:
        downloader_factory = _DownloaderFactory("write_json3")

        result = YtDlpTranscriptExtractor(
            ytdlp_factory=downloader_factory,
            cookie_file=Path(r"C:\Users\test\youtube-cookies.txt"),
        ).extract("video000001")

        self.assertTrue(result.is_success)
        self.assertEqual(
            downloader_factory.downloaders[0].options["cookiefile"],
            r"C:\Users\test\youtube-cookies.txt",
        )

    def test_extract_retries_through_provider_only_after_explicit_token_requirement(self) -> None:
        downloader_factory = _DownloaderFactory(
            "pot_warning_no_subtitle", "write_json3"
        )

        result = YtDlpTranscriptExtractor(
            ytdlp_factory=downloader_factory,
            provider_session_factory=_ProviderSession,
        ).extract("video000001")

        self.assertTrue(result.is_success)
        self.assertEqual(len(downloader_factory.downloaders), 2)
        fallback_options = downloader_factory.downloaders[1].options
        self.assertEqual(
            fallback_options["extractor_args"],
            {
                "youtube": {
                    "skip": ["translated_subs"],
                    "player_client": ["web"],
                },
                "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:49000"]},
            },
        )

    def test_extract_does_not_start_provider_for_a_generic_failure(self) -> None:
        downloader_factory = _DownloaderFactory("download_error")
        provider_starts: list[None] = []

        def provider_factory() -> _ProviderSession:
            provider_starts.append(None)
            return _ProviderSession()

        result = YtDlpTranscriptExtractor(
            ytdlp_factory=downloader_factory,
            provider_session_factory=provider_factory,
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR),
        )
        self.assertEqual(provider_starts, [])

    def test_extract_preserves_wrapped_429_without_starting_provider(self) -> None:
        downloader_factory = _DownloaderFactory("wrapped_429", "write_json3")
        provider_starts: list[None] = []

        def provider_factory() -> _ProviderSession:
            provider_starts.append(None)
            return _ProviderSession()

        result = YtDlpTranscriptExtractor(
            ytdlp_factory=downloader_factory,
            provider_session_factory=provider_factory,
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.RATE_LIMITED),
        )
        self.assertEqual(len(downloader_factory.downloaders), 1)
        self.assertEqual(provider_starts, [])
        self.assertNotIn("private", repr(result))

    def test_extract_preserves_direct_429_without_trying_next_language(self) -> None:
        downloader_factory = _DownloaderFactory("direct_429", "write_json3")

        result = YtDlpTranscriptExtractor(
            ytdlp_factory=downloader_factory
        ).extract_token_free("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.RATE_LIMITED),
        )
        self.assertEqual(len(downloader_factory.downloaders), 1)

    def test_extract_preserves_429_when_ytdlp_loses_the_original_exception(self) -> None:
        result = YtDlpTranscriptExtractor(
            ytdlp_factory=_DownloaderFactory("message_429")
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.RATE_LIMITED),
        )
        self.assertNotIn("private", repr(result))

    def test_extract_reports_po_token_requirement_when_no_provider_is_configured(self) -> None:
        result = YtDlpTranscriptExtractor(
            ytdlp_factory=_DownloaderFactory("pot_required")
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.PO_TOKEN_REQUIRED),
        )

    def test_extract_preserves_no_transcript_when_no_provider_is_configured(self) -> None:
        result = YtDlpTranscriptExtractor(
            ytdlp_factory=_DownloaderFactory("none", "none")
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.NO_TRANSCRIPT),
        )

    def test_extract_reports_provider_startup_failure_without_exposing_details(self) -> None:
        result = YtDlpTranscriptExtractor(
            ytdlp_factory=_DownloaderFactory("pot_required"),
            provider_session_factory=_UnavailableProviderSession,
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.PO_TOKEN_REQUIRED),
        )
        self.assertNotIn("private provider details", repr(result))

    def test_extract_reports_malformed_json3_without_exposing_its_contents(self) -> None:
        result = YtDlpTranscriptExtractor(
            ytdlp_factory=_DownloaderFactory("write_invalid_json3")
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE),
        )
        self.assertNotIn("not json", repr(result))

    def test_extract_rejects_malformed_srv1_without_exposing_its_contents(self) -> None:
        result = YtDlpTranscriptExtractor(
            ytdlp_factory=_DownloaderFactory("write_invalid_srv1")
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE),
        )
        self.assertNotIn("private transcript", repr(result))

    def test_extract_rejects_json3_text_without_timestamps(self) -> None:
        downloader_factory = _DownloaderFactory("write_json3_without_timestamps")

        result = YtDlpTranscriptExtractor(
            ytdlp_factory=downloader_factory
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE),
        )
        self.assertNotIn("private transcript", repr(result))

    def test_extract_sends_ytdlp_error_to_restricted_logger_not_stderr(self) -> None:
        output = StringIO()
        error_logger = logging.getLogger(self.id())
        error_logger.setLevel(logging.ERROR)
        error_logger.propagate = False
        captured = StringIO()
        handler = logging.StreamHandler(captured)
        error_logger.addHandler(handler)

        try:
            with redirect_stderr(output):
                result = YtDlpTranscriptExtractor(
                    ytdlp_factory=_DownloaderFactory("write_error_to_logger"),
                    error_logger=error_logger,
                ).extract_token_free("video000001")
        finally:
            error_logger.removeHandler(handler)
            handler.close()

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR),
        )
        self.assertEqual(output.getvalue(), "")
        self.assertIn("yt-dlp reported an error", captured.getvalue())
        self.assertNotIn("video000001", captured.getvalue())
        self.assertNotIn("private upstream error", captured.getvalue())

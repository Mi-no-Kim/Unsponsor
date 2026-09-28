from __future__ import annotations

import json
from contextlib import AbstractContextManager
from pathlib import Path
import unittest

from transcript.library_extractor import TranscriptExtractionResult, TranscriptFailure
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
        if self.action == "nonzero":
            return 1
        if self.action == "write_json3":
            directory = Path(self.options["paths"]["home"])
            (directory / "video000001.ko.json3").write_text(
                json.dumps(
                    {
                        "events": [
                            {"segs": [{"utf8": " First\nfragment "}]},
                            {"segs": [{"utf8": " second   fragment "}]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
        if self.action == "write_invalid_json3":
            directory = Path(self.options["paths"]["home"])
            (directory / "video000001.ko.json3").write_text(
                "not json", encoding="utf-8"
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
    def test_extract_normalizes_a_token_free_json3_subtitle(self) -> None:
        downloader_factory = _DownloaderFactory("write_json3")

        result = YtDlpTranscriptExtractor(
            ytdlp_factory=downloader_factory
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.succeeded("First fragment\nsecond fragment"),
        )
        self.assertEqual(len(downloader_factory.downloaders), 1)
        options = downloader_factory.downloaders[0].options
        self.assertNotIn("extractor_args", options)
        self.assertFalse(Path(options["paths"]["home"]).exists())

    def test_extract_retries_through_loopback_provider_after_token_free_failure(self) -> None:
        downloader_factory = _DownloaderFactory("download_error", "write_json3")

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
                "youtube": {"player_client": ["web"]},
                "youtubepot-bgutilhttp": {"base_url": ["http://127.0.0.1:49000"]},
            },
        )

    def test_extract_reports_po_token_requirement_when_no_provider_is_configured(self) -> None:
        result = YtDlpTranscriptExtractor(
            ytdlp_factory=_DownloaderFactory("download_error")
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
            ytdlp_factory=_DownloaderFactory("download_error"),
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

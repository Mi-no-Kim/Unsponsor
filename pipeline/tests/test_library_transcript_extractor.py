from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
import logging
import unittest

from transcript.library_extractor import (
    LibraryTranscriptExtractor,
    TranscriptExtractionResult,
    TranscriptFailure,
)
from transcript.model import TranscriptSegment
from youtube_transcript_api._errors import IpBlocked, RequestBlocked, TranscriptsDisabled


@dataclass(frozen=True)
class _Snippet:
    text: str
    start: float
    duration: float


class _TranscriptApiStub:
    def __init__(self, response: object = (), error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[str, list[str]]] = []

    def fetch(self, video_id: str, languages: list[str]) -> object:
        self.calls.append((video_id, languages))
        if self.error is not None:
            raise self.error
        return self.response


class LibraryTranscriptExtractorTests(unittest.TestCase):
    def test_extract_normalizes_non_empty_snippets_in_their_original_order(self) -> None:
        api = _TranscriptApiStub(
            response=(
                _Snippet(" First\nfragment ", 1.25, 0.5),
                _Snippet(" second   fragment ", 2.0, 0.375),
            )
        )

        result = LibraryTranscriptExtractor(api).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.succeeded(
                (
                    TranscriptSegment(0, 1250, 1750, "First fragment"),
                    TranscriptSegment(1, 2000, 2375, "second fragment"),
                )
            ),
        )
        self.assertEqual(result.text, "First fragment\nsecond fragment")
        self.assertEqual(api.calls, [("video000001", ["ko", "en"])])

    def test_extract_reports_disabled_captions_as_no_transcript(self) -> None:
        result = LibraryTranscriptExtractor(
            _TranscriptApiStub(error=TranscriptsDisabled("video000001"))
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.NO_TRANSCRIPT),
        )

    def test_extract_reports_a_blocked_library_request_as_access_restricted(self) -> None:
        result = LibraryTranscriptExtractor(
            _TranscriptApiStub(error=RequestBlocked("video000001"))
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.ACCESS_RESTRICTED),
        )

    def test_extract_preserves_an_http_429_as_rate_limited(self) -> None:
        result = LibraryTranscriptExtractor(
            _TranscriptApiStub(error=IpBlocked("video000001"))
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.RATE_LIMITED),
        )

    def test_extract_reports_unexpected_errors_without_exposing_their_message(self) -> None:
        secret_error = RuntimeError("private transcript contents")

        result = LibraryTranscriptExtractor(
            _TranscriptApiStub(error=secret_error)
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR),
        )
        self.assertNotIn("private transcript contents", repr(result))

    def test_extract_omits_identifiers_transcript_and_proxy_secrets_from_error_log(self) -> None:
        captured = StringIO()
        logger = logging.getLogger(self.id())
        logger.setLevel(logging.ERROR)
        logger.propagate = False
        handler = logging.StreamHandler(captured)
        logger.addHandler(handler)
        try:
            result = LibraryTranscriptExtractor(
                _TranscriptApiStub(
                    error=RuntimeError(
                        "video000001 private transcript "
                        "http://private-login:private-password@gw.dataimpulse.com"
                    )
                ),
                error_logger=logger,
            ).extract("video000001")
        finally:
            logger.removeHandler(handler)
            handler.close()

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.TRANSIENT_ERROR),
        )
        self.assertIn("raised an unexpected error", captured.getvalue())
        for private_value in (
            "video000001",
            "private transcript",
            "private-login",
            "private-password",
        ):
            self.assertNotIn(private_value, captured.getvalue())

    def test_extract_rejects_an_empty_or_malformed_response_without_exposing_it(self) -> None:
        for response in ((), (_Snippet("  \n  ", 0.0, 1.0),), object()):
            with self.subTest(response_type=type(response).__name__):
                result = LibraryTranscriptExtractor(_TranscriptApiStub(response)).extract(
                    "video000001"
                )

                self.assertEqual(
                    result,
                    TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE),
                )
                if response != ():
                    self.assertNotIn(repr(response), repr(result))

    def test_extract_rejects_a_segment_without_a_valid_timestamp(self) -> None:
        result = LibraryTranscriptExtractor(
            _TranscriptApiStub(response=(_Snippet("private transcript", -1.0, 1.0),))
        ).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE),
        )
        self.assertNotIn("private transcript", repr(result))

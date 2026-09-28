from __future__ import annotations

from dataclasses import dataclass
import unittest

from transcript.library_extractor import (
    LibraryTranscriptExtractor,
    TranscriptExtractionResult,
    TranscriptFailure,
)
from youtube_transcript_api._errors import RequestBlocked, TranscriptsDisabled


@dataclass(frozen=True)
class _Snippet:
    text: str


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
            response=(_Snippet(" First\nfragment "), _Snippet(" second   fragment "))
        )

        result = LibraryTranscriptExtractor(api).extract("video000001")

        self.assertEqual(
            result,
            TranscriptExtractionResult.succeeded("First fragment\nsecond fragment"),
        )
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

    def test_extract_rejects_an_empty_or_malformed_response_without_exposing_it(self) -> None:
        for response in ((), (_Snippet("  \n  "),), object()):
            with self.subTest(response_type=type(response).__name__):
                result = LibraryTranscriptExtractor(_TranscriptApiStub(response)).extract(
                    "video000001"
                )

                self.assertEqual(
                    result,
                    TranscriptExtractionResult.failed(TranscriptFailure.INVALID_RESPONSE),
                )
                self.assertNotIn(repr(response), repr(result))

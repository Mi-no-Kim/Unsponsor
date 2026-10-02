from __future__ import annotations

import gzip
import json
import unittest
from unittest.mock import patch

from transcript.codec import (
    TRANSCRIPT_FORMAT,
    TranscriptPayloadError,
    decode_transcript_payload,
    encode_transcript_payload,
    transcript_text,
)
from transcript.model import TranscriptSegment


class TranscriptCodecTests(unittest.TestCase):
    def test_round_trip_is_deterministic_and_recreates_plain_text(self) -> None:
        segments = _segments()

        first = encode_transcript_payload(segments)
        second = encode_transcript_payload(segments)

        self.assertEqual(first, second)
        self.assertEqual(
            decode_transcript_payload(TRANSCRIPT_FORMAT, first),
            segments,
        )
        self.assertEqual(transcript_text(segments), "첫 문장\nsecond sentence")

    def test_rejects_corrupt_truncated_and_trailing_gzip_data(self) -> None:
        payload = encode_transcript_payload(_segments())

        for invalid in (
            payload[:-1],
            payload + b"trailing",
            payload[:-10] + bytes([payload[-10] ^ 1]) + payload[-9:],
        ):
            with self.subTest(size=len(invalid)):
                with self.assertRaises(TranscriptPayloadError):
                    decode_transcript_payload(TRANSCRIPT_FORMAT, invalid)

    def test_rejects_unknown_format_and_non_binary_payload(self) -> None:
        payload = encode_transcript_payload(_segments())

        with self.assertRaisesRegex(TranscriptPayloadError, "unsupported"):
            decode_transcript_payload("gzip_json_v2", payload)
        with self.assertRaisesRegex(TranscriptPayloadError, "binary"):
            decode_transcript_payload(TRANSCRIPT_FORMAT, "private transcript")

    def test_rejects_a_payload_that_expands_beyond_the_limit(self) -> None:
        payload = gzip.compress(b"a" * 65, mtime=0)

        with patch("transcript.codec.MAX_UNCOMPRESSED_BYTES", 64):
            with self.assertRaisesRegex(TranscriptPayloadError, "expands"):
                decode_transcript_payload(TRANSCRIPT_FORMAT, payload)

    def test_rejects_invalid_contract_timeline_and_text(self) -> None:
        documents = (
            {"v": 2, "segments": [[0, 1000, "valid"]]},
            {"v": 1, "segments": [[1000, 1000, "valid"], [0, 1000, "valid"]]},
            {"v": 1, "segments": [[0, 0, "valid"]]},
            {"v": 1, "segments": [[0, 1000, " not normalized "]]},
            {"v": 1, "segments": []},
        )

        for document in documents:
            with self.subTest(document_keys=tuple(document)):
                encoded = json.dumps(
                    document,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                with self.assertRaises(TranscriptPayloadError):
                    decode_transcript_payload(
                        TRANSCRIPT_FORMAT,
                        gzip.compress(encoded, mtime=0),
                    )


def _segments() -> tuple[TranscriptSegment, ...]:
    return (
        TranscriptSegment(0, 0, 1250, "첫 문장"),
        TranscriptSegment(1, 1250, 2500, "second sentence"),
    )


if __name__ == "__main__":
    unittest.main()

"""자막 세그먼트를 하나의 검증 가능한 영구 payload로 변환한다."""

from __future__ import annotations

import gzip
import json
import zlib
from collections.abc import Sequence

from transcript.model import TranscriptSegment, normalize_segment_text


TRANSCRIPT_FORMAT = "gzip_json_v1"
MAX_COMPRESSED_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_SEGMENTS = 100_000
MAX_SEGMENT_TEXT_BYTES = 65_535
MAX_TIMESTAMP_MS = 4_294_967_295


class TranscriptPayloadError(ValueError):
    """영구 payload가 canonical 계약을 만족하지 않음을 나타낸다."""


def encode_transcript_payload(segments: Sequence[TranscriptSegment]) -> bytes:
    """정규화 세그먼트를 compact JSON 뒤 결정적인 gzip payload로 만든다."""

    normalized = _validate_segments(tuple(segments))
    document = {
        "v": 1,
        "segments": [
            [
                segment.start_ms,
                segment.end_ms - segment.start_ms,
                segment.text,
            ]
            for segment in normalized
        ],
    }
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_UNCOMPRESSED_BYTES:
        raise TranscriptPayloadError("transcript payload is too large before compression")

    payload = gzip.compress(encoded, compresslevel=9, mtime=0)
    if not payload or len(payload) > MAX_COMPRESSED_BYTES:
        raise TranscriptPayloadError("transcript payload is too large after compression")
    return payload


def decode_transcript_payload(
    transcript_format: object,
    payload: object,
) -> tuple[TranscriptSegment, ...]:
    """크기·gzip·JSON·타임라인 계약을 모두 검증해 세그먼트를 복원한다."""

    if transcript_format != TRANSCRIPT_FORMAT:
        raise TranscriptPayloadError("transcript payload format is unsupported")
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TranscriptPayloadError("transcript payload must be binary")

    compressed = bytes(payload)
    if not compressed or len(compressed) > MAX_COMPRESSED_BYTES:
        raise TranscriptPayloadError("transcript payload has an invalid compressed size")

    encoded = _decompress_gzip(compressed)
    try:
        document = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TranscriptPayloadError("transcript payload JSON is invalid") from error

    if (
        not isinstance(document, dict)
        or set(document) != {"v", "segments"}
        or isinstance(document["v"], bool)
        or document["v"] != 1
        or not isinstance(document["segments"], list)
    ):
        raise TranscriptPayloadError("transcript payload contract is invalid")

    raw_segments = document["segments"]
    if not raw_segments or len(raw_segments) > MAX_SEGMENTS:
        raise TranscriptPayloadError("transcript payload segment count is invalid")

    segments: list[TranscriptSegment] = []
    previous_start_ms = -1
    for sequence, raw_segment in enumerate(raw_segments):
        if not isinstance(raw_segment, list) or len(raw_segment) != 3:
            raise TranscriptPayloadError("transcript payload segment is invalid")
        start_ms, duration_ms, text = raw_segment
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (start_ms, duration_ms)
        ):
            raise TranscriptPayloadError("transcript payload timestamp is invalid")
        if (
            start_ms < 0
            or start_ms > MAX_TIMESTAMP_MS
            or duration_ms <= 0
            or duration_ms > MAX_TIMESTAMP_MS
            or start_ms + duration_ms > MAX_TIMESTAMP_MS
            or start_ms < previous_start_ms
        ):
            raise TranscriptPayloadError("transcript payload timeline is invalid")
        if not isinstance(text, str):
            raise TranscriptPayloadError("transcript payload text is invalid")
        try:
            normalized_text = normalize_segment_text(text)
        except ValueError as error:
            raise TranscriptPayloadError("transcript payload text is invalid") from error
        if (
            text != normalized_text
            or len(text.encode("utf-8")) > MAX_SEGMENT_TEXT_BYTES
        ):
            raise TranscriptPayloadError("transcript payload text is invalid")

        segments.append(
            TranscriptSegment(
                sequence=sequence,
                start_ms=start_ms,
                end_ms=start_ms + duration_ms,
                text=text,
            )
        )
        previous_start_ms = start_ms

    return tuple(segments)


def transcript_text(segments: Sequence[TranscriptSegment]) -> str:
    """LLM 등 일반 텍스트 소비자가 쓸 원문을 결정적으로 재생성한다."""

    normalized = _validate_segments(tuple(segments))
    return "\n".join(segment.text for segment in normalized)


def _decompress_gzip(payload: bytes) -> bytes:
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        decoded = decompressor.decompress(payload, MAX_UNCOMPRESSED_BYTES + 1)
        if len(decoded) > MAX_UNCOMPRESSED_BYTES or decompressor.unconsumed_tail:
            raise TranscriptPayloadError("transcript payload expands beyond its limit")
        decoded += decompressor.flush(MAX_UNCOMPRESSED_BYTES + 1 - len(decoded))
    except zlib.error as error:
        raise TranscriptPayloadError("transcript payload gzip stream is invalid") from error

    if len(decoded) > MAX_UNCOMPRESSED_BYTES:
        raise TranscriptPayloadError("transcript payload expands beyond its limit")
    if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
        raise TranscriptPayloadError("transcript payload gzip stream is incomplete")
    return decoded


def _validate_segments(
    segments: tuple[TranscriptSegment, ...],
) -> tuple[TranscriptSegment, ...]:
    if not segments or len(segments) > MAX_SEGMENTS:
        raise TranscriptPayloadError("transcript payload segment count is invalid")

    previous_start_ms = -1
    for expected_sequence, segment in enumerate(segments):
        if not isinstance(segment, TranscriptSegment):
            raise TranscriptPayloadError("transcript payload has an invalid segment")
        if segment.sequence != expected_sequence:
            raise TranscriptPayloadError("transcript payload sequences must be contiguous")
        if segment.start_ms < previous_start_ms or segment.end_ms > MAX_TIMESTAMP_MS:
            raise TranscriptPayloadError("transcript payload timeline is invalid")
        if len(segment.text.encode("utf-8")) > MAX_SEGMENT_TEXT_BYTES:
            raise TranscriptPayloadError("transcript payload text is too large")
        previous_start_ms = segment.start_ms
    return segments

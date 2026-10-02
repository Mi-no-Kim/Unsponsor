"""LLM 1차 제품 식별·광고·협찬 판정의 입출력 계약이다."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from transcript.codec import MAX_TIMESTAMP_MS
from transcript.model import TranscriptSegment


MAX_MARKER_LENGTH = 100
MAX_PRODUCT_NAME_LENGTH = 500


class IdentificationInputError(ValueError):
    """LLM 1차 입력이 내부 계약을 만족하지 않음을 나타낸다."""


class IdentificationOutputError(ValueError):
    """LLM 1차 출력이 정상 판정 결과로 사용할 수 없음을 나타낸다."""


class ProductRole(StrEnum):
    """한 영상에서 식별된 제품이 차지하는 리뷰 역할이다."""

    PRIMARY = "primary"
    SECONDARY = "secondary"


class MarkerStatus(StrEnum):
    """제품 마커를 자동 매칭에 쓸 수 있는지 나타낸다."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class IdentificationInput:
    """메타데이터와 밀리초 자막 타임라인을 보존하는 LLM 1차 입력이다."""

    title: str
    description: str | None
    has_paid_product_placement: bool | None
    transcript_segments: tuple[TranscriptSegment, ...]

    def __post_init__(self) -> None:
        _validate_text(
            self.title,
            "input.title",
            IdentificationInputError,
            max_length=500,
        )
        if self.description is not None and not isinstance(self.description, str):
            raise IdentificationInputError("input.description must be a string or null")
        if self.has_paid_product_placement is not None and not isinstance(
            self.has_paid_product_placement, bool
        ):
            raise IdentificationInputError(
                "input.has_paid_product_placement must be a boolean or null"
            )
        if not isinstance(self.transcript_segments, tuple):
            raise IdentificationInputError(
                "input.transcript_segments must be a tuple"
            )
        if not self.transcript_segments:
            raise IdentificationInputError(
                "input.transcript_segments must not be empty"
            )

        previous_start_ms = -1
        for expected_sequence, segment in enumerate(self.transcript_segments):
            if not isinstance(segment, TranscriptSegment):
                raise IdentificationInputError(
                    f"input.transcript_segments[{expected_sequence}] is invalid"
                )
            if segment.sequence != expected_sequence:
                raise IdentificationInputError(
                    "input transcript segment sequences must be contiguous"
                )
            if (
                segment.start_ms < previous_start_ms
                or segment.end_ms > MAX_TIMESTAMP_MS
            ):
                raise IdentificationInputError(
                    "input transcript segment timeline is invalid"
                )
            previous_start_ms = segment.start_ms

    @property
    def transcript_end_ms(self) -> int:
        """광고 구간의 닫힌 상한으로 사용할 자막의 마지막 끝 시각이다."""

        return max(segment.end_ms for segment in self.transcript_segments)

    def to_payload(self) -> dict[str, Any]:
        """프롬프트·비공개 평가 파일이 공유할 JSON 호환 표현을 만든다."""

        return {
            "title": self.title,
            "description": self.description,
            "has_paid_product_placement": self.has_paid_product_placement,
            "transcript_segments": [
                {
                    "sequence": segment.sequence,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                }
                for segment in self.transcript_segments
            ],
        }

    @classmethod
    def from_payload(cls, payload: object) -> IdentificationInput:
        """JSON에서 읽은 입력을 기본값 보충 없이 검증한다."""

        document = _require_object(
            payload,
            "input",
            {
                "title",
                "description",
                "has_paid_product_placement",
                "transcript_segments",
            },
            IdentificationInputError,
        )
        raw_segments = document["transcript_segments"]
        if not isinstance(raw_segments, list):
            raise IdentificationInputError(
                "input.transcript_segments must be an array"
            )

        segments: list[TranscriptSegment] = []
        for index, raw_segment in enumerate(raw_segments):
            path = f"input.transcript_segments[{index}]"
            segment = _require_object(
                raw_segment,
                path,
                {"sequence", "start_ms", "end_ms", "text"},
                IdentificationInputError,
            )
            sequence = _require_integer(
                segment["sequence"], f"{path}.sequence", IdentificationInputError
            )
            start_ms = _require_integer(
                segment["start_ms"], f"{path}.start_ms", IdentificationInputError
            )
            end_ms = _require_integer(
                segment["end_ms"], f"{path}.end_ms", IdentificationInputError
            )
            text = segment["text"]
            if not isinstance(text, str):
                raise IdentificationInputError(f"{path}.text must be a string")
            try:
                segments.append(
                    TranscriptSegment(
                        sequence=sequence,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        text=text,
                    )
                )
            except ValueError as error:
                raise IdentificationInputError(f"{path} is invalid") from error

        return cls(
            title=document["title"],
            description=document["description"],
            has_paid_product_placement=document["has_paid_product_placement"],
            transcript_segments=tuple(segments),
        )


@dataclass(frozen=True)
class ProductMarkers:
    """브랜드·시리즈·제품 후보와 그 완전성·불확실성을 함께 보존한다."""

    brand: str | None
    series: str | None
    product: str | None
    status: MarkerStatus

    def __post_init__(self) -> None:
        for name, value in (
            ("brand", self.brand),
            ("series", self.series),
            ("product", self.product),
        ):
            _validate_marker(value, f"output.products[].markers.{name}")
        if not isinstance(self.status, MarkerStatus):
            raise IdentificationOutputError(
                "output.products[].markers.status is invalid"
            )

        values = (self.brand, self.series, self.product)
        if self.status is MarkerStatus.COMPLETE and any(
            value is None for value in values
        ):
            raise IdentificationOutputError(
                "complete product markers must include brand, series, and product"
            )
        if self.status is MarkerStatus.INCOMPLETE and all(
            value is not None for value in values
        ):
            raise IdentificationOutputError(
                "incomplete product markers must contain a null marker"
            )
        if self.status is MarkerStatus.UNCERTAIN and all(
            value is None for value in values
        ):
            raise IdentificationOutputError(
                "uncertain product markers must include at least one candidate"
            )

    @property
    def matching_key(self) -> tuple[str, str, str] | None:
        """자동 매칭 가능한 확정 마커만 키로 노출한다."""

        if self.status is not MarkerStatus.COMPLETE:
            return None
        assert self.brand is not None
        assert self.series is not None
        assert self.product is not None
        return (self.brand, self.series, self.product)

    def to_payload(self) -> dict[str, Any]:
        return {
            "brand": self.brand,
            "series": self.series,
            "product": self.product,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class IdentifiedProduct:
    """영상에서 한 번만 나타나야 하는 제품별 LLM 1차 판정이다."""

    original_name: str
    markers: ProductMarkers
    role: ProductRole
    is_sponsored_review: bool

    def __post_init__(self) -> None:
        _validate_text(
            self.original_name,
            "output.products[].original_name",
            IdentificationOutputError,
            max_length=MAX_PRODUCT_NAME_LENGTH,
            require_normalized_whitespace=True,
        )
        if not isinstance(self.markers, ProductMarkers):
            raise IdentificationOutputError("output product markers are invalid")
        if not isinstance(self.role, ProductRole):
            raise IdentificationOutputError("output product role is invalid")
        if not isinstance(self.is_sponsored_review, bool):
            raise IdentificationOutputError(
                "output product is_sponsored_review must be a boolean"
            )

    def to_payload(self) -> dict[str, Any]:
        return {
            "original_name": self.original_name,
            "markers": self.markers.to_payload(),
            "role": self.role.value,
            "is_sponsored_review": self.is_sponsored_review,
        }


@dataclass(frozen=True)
class IdentifiedAdSegment:
    """자막과 같은 밀리초 단위를 쓰는 LLM 탐지 광고 구간이다."""

    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        for name, value in (("start_ms", self.start_ms), ("end_ms", self.end_ms)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise IdentificationOutputError(
                    f"output ad segment {name} must be an integer"
                )
        if (
            self.start_ms < 0
            or self.end_ms <= self.start_ms
            or self.end_ms > MAX_TIMESTAMP_MS
        ):
            raise IdentificationOutputError("output ad segment timeline is invalid")

    def to_storage_seconds(self) -> tuple[int, int]:
        """식별 구간 전체를 덮도록 시작은 내림, 끝은 올림해 DB 초로 바꾼다."""

        return (self.start_ms // 1000, (self.end_ms + 999) // 1000)

    def to_payload(self) -> dict[str, int]:
        return {"start_ms": self.start_ms, "end_ms": self.end_ms}


@dataclass(frozen=True)
class IdentificationOutput:
    """검증을 통과해 정상 판정으로 취급할 수 있는 LLM 1차 출력이다."""

    products: tuple[IdentifiedProduct, ...]
    ad_segments: tuple[IdentifiedAdSegment, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.products, tuple) or not self.products:
            raise IdentificationOutputError(
                "output.products must be a non-empty array"
            )
        if not isinstance(self.ad_segments, tuple):
            raise IdentificationOutputError("output.ad_segments must be an array")

        primary_count = 0
        seen_names: set[str] = set()
        seen_marker_candidates: set[tuple[str, str, str]] = set()
        for product in self.products:
            if not isinstance(product, IdentifiedProduct):
                raise IdentificationOutputError("output contains an invalid product")
            if product.role is ProductRole.PRIMARY:
                primary_count += 1

            name_key = _identity_text(product.original_name)
            marker_key = _candidate_marker_key(product.markers)
            if name_key in seen_names or (
                marker_key is not None and marker_key in seen_marker_candidates
            ):
                raise IdentificationOutputError(
                    "output must contain each identified product only once"
                )
            seen_names.add(name_key)
            if marker_key is not None:
                seen_marker_candidates.add(marker_key)

        if primary_count == 0:
            raise IdentificationOutputError(
                "output must contain at least one primary product"
            )

        previous_end_ms = -1
        for segment in self.ad_segments:
            if not isinstance(segment, IdentifiedAdSegment):
                raise IdentificationOutputError(
                    "output contains an invalid ad segment"
                )
            if segment.start_ms < previous_end_ms:
                raise IdentificationOutputError(
                    "output ad segments must be ordered and non-overlapping"
                )
            previous_end_ms = segment.end_ms

    def validate_against(self, source: IdentificationInput) -> None:
        """출력 광고 구간이 해당 입력 자막의 시간 범위를 넘지 않는지 확인한다."""

        if not isinstance(source, IdentificationInput):
            raise IdentificationOutputError("output source input is invalid")
        for segment in self.ad_segments:
            if segment.end_ms > source.transcript_end_ms:
                raise IdentificationOutputError(
                    "output ad segment exceeds the input transcript timeline"
                )

    def to_payload(self) -> dict[str, Any]:
        """평가·호출 모듈이 저장할 JSON 호환 표현을 만든다."""

        return {
            "products": [product.to_payload() for product in self.products],
            "ad_segments": [segment.to_payload() for segment in self.ad_segments],
        }

    @classmethod
    def from_payload(
        cls,
        payload: object,
        source: IdentificationInput,
    ) -> IdentificationOutput:
        """LLM JSON 객체를 누락값 보충·정렬·구간 보정 없이 검증한다."""

        document = _require_object(
            payload,
            "output",
            {"products", "ad_segments"},
            IdentificationOutputError,
        )
        raw_products = document["products"]
        raw_ad_segments = document["ad_segments"]
        if not isinstance(raw_products, list):
            raise IdentificationOutputError("output.products must be an array")
        if not isinstance(raw_ad_segments, list):
            raise IdentificationOutputError("output.ad_segments must be an array")

        products = tuple(
            _parse_product(raw_product, index)
            for index, raw_product in enumerate(raw_products)
        )
        ad_segments = tuple(
            _parse_ad_segment(raw_segment, index)
            for index, raw_segment in enumerate(raw_ad_segments)
        )
        result = cls(products=products, ad_segments=ad_segments)
        result.validate_against(source)
        return result


def _parse_product(payload: object, index: int) -> IdentifiedProduct:
    path = f"output.products[{index}]"
    product = _require_object(
        payload,
        path,
        {"original_name", "markers", "role", "is_sponsored_review"},
        IdentificationOutputError,
    )
    raw_markers = _require_object(
        product["markers"],
        f"{path}.markers",
        {"brand", "series", "product", "status"},
        IdentificationOutputError,
    )
    try:
        marker_status = MarkerStatus(raw_markers["status"])
    except (TypeError, ValueError) as error:
        raise IdentificationOutputError(
            f"{path}.markers.status is invalid"
        ) from error
    try:
        role = ProductRole(product["role"])
    except (TypeError, ValueError) as error:
        raise IdentificationOutputError(f"{path}.role is invalid") from error

    return IdentifiedProduct(
        original_name=product["original_name"],
        markers=ProductMarkers(
            brand=raw_markers["brand"],
            series=raw_markers["series"],
            product=raw_markers["product"],
            status=marker_status,
        ),
        role=role,
        is_sponsored_review=product["is_sponsored_review"],
    )


def _parse_ad_segment(payload: object, index: int) -> IdentifiedAdSegment:
    path = f"output.ad_segments[{index}]"
    segment = _require_object(
        payload,
        path,
        {"start_ms", "end_ms"},
        IdentificationOutputError,
    )
    return IdentifiedAdSegment(
        start_ms=_require_integer(
            segment["start_ms"], f"{path}.start_ms", IdentificationOutputError
        ),
        end_ms=_require_integer(
            segment["end_ms"], f"{path}.end_ms", IdentificationOutputError
        ),
    )


def _require_object(
    value: object,
    path: str,
    expected_keys: set[str],
    error_type: type[ValueError],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise error_type(f"{path} must be an object")
    actual_keys = set(value)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        unexpected = sorted(actual_keys - expected_keys)
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        raise error_type(f"{path} fields are invalid ({', '.join(details)})")
    return value


def _require_integer(
    value: object,
    path: str,
    error_type: type[ValueError],
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise error_type(f"{path} must be an integer")
    return value


def _validate_text(
    value: object,
    path: str,
    error_type: type[ValueError],
    *,
    max_length: int,
    require_normalized_whitespace: bool = False,
) -> None:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise error_type(f"{path} must be a non-empty string up to {max_length} chars")
    if require_normalized_whitespace and value != " ".join(value.split()):
        raise error_type(f"{path} whitespace must be normalized")


def _validate_marker(value: object, path: str) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_MARKER_LENGTH
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise IdentificationOutputError(
            f"{path} must be null or a whitespace-free string up to "
            f"{MAX_MARKER_LENGTH} chars"
        )


def _identity_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _candidate_marker_key(markers: ProductMarkers) -> tuple[str, str, str] | None:
    if markers.brand is None or markers.series is None or markers.product is None:
        return None
    return tuple(
        _identity_text(value)
        for value in (markers.brand, markers.series, markers.product)
    )

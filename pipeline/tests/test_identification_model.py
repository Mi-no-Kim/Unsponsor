from __future__ import annotations

import copy
import unittest

from summarizer.identification_model import (
    IdentificationInput,
    IdentificationInputError,
    IdentificationOutput,
    IdentificationOutputError,
    MarkerStatus,
    ProductMarkers,
)
from transcript.model import TranscriptSegment


class IdentificationInputTests(unittest.TestCase):
    def test_round_trip_preserves_nullable_metadata_and_millisecond_segments(self) -> None:
        source = _input(description=None, paid_placement=None)

        restored = IdentificationInput.from_payload(source.to_payload())

        self.assertEqual(restored, source)
        self.assertIsNone(restored.description)
        self.assertIsNone(restored.has_paid_product_placement)
        self.assertEqual(restored.transcript_end_ms, 4_250)

    def test_rejects_missing_fields_invalid_nullable_types_and_bad_segments(self) -> None:
        valid = _input().to_payload()
        cases = []

        missing_description = copy.deepcopy(valid)
        del missing_description["description"]
        cases.append(missing_description)

        false_for_unknown = copy.deepcopy(valid)
        false_for_unknown["has_paid_product_placement"] = 0
        cases.append(false_for_unknown)

        non_contiguous = copy.deepcopy(valid)
        non_contiguous["transcript_segments"][1]["sequence"] = 2
        cases.append(non_contiguous)

        reversed_segment = copy.deepcopy(valid)
        reversed_segment["transcript_segments"][0]["end_ms"] = 0
        cases.append(reversed_segment)

        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(IdentificationInputError):
                    IdentificationInput.from_payload(payload)


class ProductMarkersTests(unittest.TestCase):
    def test_exposes_a_matching_key_only_for_complete_markers(self) -> None:
        complete = ProductMarkers(
            brand="acme",
            series="phone",
            product="pro",
            status=MarkerStatus.COMPLETE,
        )
        incomplete = ProductMarkers(
            brand="acme",
            series="phone",
            product=None,
            status=MarkerStatus.INCOMPLETE,
        )
        uncertain = ProductMarkers(
            brand="acme",
            series="phone",
            product="pro",
            status=MarkerStatus.UNCERTAIN,
        )

        self.assertEqual(complete.matching_key, ("acme", "phone", "pro"))
        self.assertIsNone(incomplete.matching_key)
        self.assertIsNone(uncertain.matching_key)

    def test_rejects_marker_status_and_value_contradictions(self) -> None:
        invalid = (
            ("acme", "phone", None, MarkerStatus.COMPLETE),
            ("acme", "phone", "pro", MarkerStatus.INCOMPLETE),
            (None, None, None, MarkerStatus.UNCERTAIN),
            ("acme brand", "phone", "pro", MarkerStatus.COMPLETE),
        )

        for brand, series, product, status in invalid:
            with self.subTest(status=status):
                with self.assertRaises(IdentificationOutputError):
                    ProductMarkers(brand, series, product, status)


class IdentificationOutputTests(unittest.TestCase):
    def test_accepts_multiple_primary_products_no_ads_and_missing_markers(self) -> None:
        payload = _valid_output()
        payload["products"].append(
            {
                "original_name": "비교 제품 B",
                "markers": {
                    "brand": "other",
                    "series": None,
                    "product": None,
                    "status": "incomplete",
                },
                "role": "primary",
                "is_sponsored_review": True,
            }
        )
        payload["ad_segments"] = []

        result = IdentificationOutput.from_payload(payload, _input())

        self.assertEqual(len(result.products), 2)
        self.assertEqual(result.products[1].role.value, "primary")
        self.assertTrue(result.products[1].is_sponsored_review)
        self.assertEqual(result.ad_segments, ())
        self.assertEqual(result.to_payload(), payload)

    def test_preserves_explicit_false_without_filling_a_missing_sponsorship(self) -> None:
        payload = _valid_output()

        result = IdentificationOutput.from_payload(payload, _input())

        self.assertFalse(result.products[0].is_sponsored_review)
        del payload["products"][0]["is_sponsored_review"]
        with self.assertRaisesRegex(IdentificationOutputError, "missing"):
            IdentificationOutput.from_payload(payload, _input())

    def test_rejects_invalid_types_roles_and_reversed_or_out_of_range_ads(self) -> None:
        cases = []

        invalid_bool = _valid_output()
        invalid_bool["products"][0]["is_sponsored_review"] = None
        cases.append(invalid_bool)

        invalid_role = _valid_output()
        invalid_role["products"][0]["role"] = "main"
        cases.append(invalid_role)

        bool_timestamp = _valid_output()
        bool_timestamp["ad_segments"][0]["start_ms"] = False
        cases.append(bool_timestamp)

        reversed_ad = _valid_output()
        reversed_ad["ad_segments"][0] = {"start_ms": 2_500, "end_ms": 2_000}
        cases.append(reversed_ad)

        out_of_range_ad = _valid_output()
        out_of_range_ad["ad_segments"][0]["end_ms"] = 4_251
        cases.append(out_of_range_ad)

        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(IdentificationOutputError):
                    IdentificationOutput.from_payload(payload, _input())

    def test_rejects_duplicates_no_primary_and_overlapping_or_unsorted_ads(self) -> None:
        duplicate_name = _valid_output()
        duplicate_name["products"].append(
            {
                "original_name": "제품 A",
                "markers": {
                    "brand": "other",
                    "series": "other-series",
                    "product": "other-product",
                    "status": "complete",
                },
                "role": "secondary",
                "is_sponsored_review": False,
            }
        )

        duplicate_markers = _valid_output()
        duplicate_markers["products"].append(
            {
                "original_name": "Product A alias",
                "markers": {
                    "brand": "ACME",
                    "series": "PHONE",
                    "product": "PRO",
                    "status": "uncertain",
                },
                "role": "secondary",
                "is_sponsored_review": False,
            }
        )

        no_primary = _valid_output()
        no_primary["products"][0]["role"] = "secondary"

        overlapping_ads = _valid_output()
        overlapping_ads["ad_segments"].append(
            {"start_ms": 1_999, "end_ms": 3_000}
        )

        for payload in (
            duplicate_name,
            duplicate_markers,
            no_primary,
            overlapping_ads,
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(IdentificationOutputError):
                    IdentificationOutput.from_payload(payload, _input())

    def test_converts_ad_milliseconds_to_covering_integer_seconds(self) -> None:
        result = IdentificationOutput.from_payload(_valid_output(), _input())

        self.assertEqual(result.ad_segments[0].to_storage_seconds(), (1, 3))


def _input(
    *,
    description: str | None = "설명",
    paid_placement: bool | None = False,
) -> IdentificationInput:
    return IdentificationInput(
        title="두 제품 비교",
        description=description,
        has_paid_product_placement=paid_placement,
        transcript_segments=(
            TranscriptSegment(0, 0, 2_000, "첫 자막"),
            TranscriptSegment(1, 2_000, 4_250, "두 번째 자막"),
        ),
    )


def _valid_output() -> dict[str, object]:
    return {
        "products": [
            {
                "original_name": "제품 A",
                "markers": {
                    "brand": "acme",
                    "series": "phone",
                    "product": "pro",
                    "status": "complete",
                },
                "role": "primary",
                "is_sponsored_review": False,
            }
        ],
        "ad_segments": [{"start_ms": 1_001, "end_ms": 2_001}],
    }


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common.config import ConfigurationError, DataImpulseProxySettings
from transcript.dataimpulse_canary import (
    CanaryConfigurationError,
    DataImpulseCanary,
    _create_attempt,
    _load_video_ids,
    _proxy_url,
    main,
)
from transcript.model import (
    TranscriptExtractionResult,
    TranscriptFailure,
    TranscriptSegment,
)


VIDEO_IDS = ("video000001", "video000002", "video000003")


class _Attempt:
    def __init__(self, result: TranscriptExtractionResult) -> None:
        self._result = result
        self.closed = False
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def extract(
        self, video_id: str, *, language_codes: tuple[str, ...]
    ) -> TranscriptExtractionResult:
        self.calls.append((video_id, language_codes))
        return self._result

    def close(self) -> None:
        self.closed = True


class _AttemptFactory:
    def __init__(self, results: list[TranscriptExtractionResult]) -> None:
        self._results = iter(results)
        self.attempts: list[_Attempt] = []

    def __call__(self, settings, error_logger) -> _Attempt:
        attempt = _Attempt(next(self._results))
        self.attempts.append(attempt)
        return attempt


class DataImpulseCanaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = DataImpulseProxySettings(
            username="private-login",
            password="private:p@ssword",
            country_code="kr",
        )

    def test_reports_pass_without_identifiers_transcripts_or_credentials(self) -> None:
        factory = _AttemptFactory([_success("one"), _success("two"), _success("three")])

        report = DataImpulseCanary(
            self.settings,
            attempt_factory=factory,
            monotonic_clock=_clock(),
        ).run(VIDEO_IDS)

        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(report.exit_code, 0)
        self.assertEqual([check.sequence for check in report.checks], [1, 2, 3])
        self.assertEqual([check.text_length for check in report.checks], [3, 3, 5])
        self.assertTrue(all(attempt.closed for attempt in factory.attempts))
        encoded = json.dumps(report.to_dict())
        for private_value in (
            *VIDEO_IDS,
            "one",
            "two",
            "three",
            self.settings.username,
            self.settings.password,
        ):
            self.assertNotIn(private_value, encoded)

    def test_reports_conditional_when_a_fresh_session_recovers_a_retry(self) -> None:
        factory = _AttemptFactory(
            [
                _failure(TranscriptFailure.ACCESS_RESTRICTED),
                _success("recovered"),
                _success("second"),
                _success("third"),
            ]
        )

        report = DataImpulseCanary(
            self.settings,
            attempt_factory=factory,
            monotonic_clock=_clock(),
        ).run(VIDEO_IDS)

        self.assertEqual(report.verdict, "CONDITIONAL")
        self.assertEqual(report.exit_code, 3)
        self.assertEqual(report.checks[0].attempts, 2)
        self.assertEqual(len(factory.attempts), 4)
        self.assertTrue(all(attempt.closed for attempt in factory.attempts))

    def test_repeated_rate_limit_stops_before_remaining_videos(self) -> None:
        factory = _AttemptFactory(
            [
                _failure(TranscriptFailure.RATE_LIMITED),
                _failure(TranscriptFailure.RATE_LIMITED),
            ]
        )

        report = DataImpulseCanary(
            self.settings,
            attempt_factory=factory,
            monotonic_clock=_clock(),
        ).run(VIDEO_IDS)

        self.assertEqual(report.verdict, "FAIL")
        self.assertEqual(report.exit_code, 2)
        self.assertEqual(report.checks[0].code, "rate_limited")
        self.assertEqual(report.checks[0].attempts, 2)
        self.assertEqual(
            [check.code for check in report.checks[1:]],
            ["skipped_after_rate_limit", "skipped_after_rate_limit"],
        )
        self.assertEqual(report.to_dict()["checked_count"], 1)
        self.assertEqual(len(factory.attempts), 2)

    def test_permanent_failure_is_not_retried(self) -> None:
        factory = _AttemptFactory(
            [
                _failure(TranscriptFailure.NO_TRANSCRIPT),
                _success("second"),
                _success("third"),
            ]
        )

        report = DataImpulseCanary(
            self.settings,
            attempt_factory=factory,
            monotonic_clock=_clock(),
        ).run(VIDEO_IDS)

        self.assertEqual(report.verdict, "FAIL")
        self.assertEqual(report.checks[0].code, "no_transcript")
        self.assertEqual(report.checks[0].attempts, 1)
        self.assertEqual(len(factory.attempts), 3)

    def test_proxy_url_uses_rotating_endpoint_country_and_encoded_credentials(self) -> None:
        proxy_url = _proxy_url(self.settings)

        self.assertEqual(
            proxy_url,
            "http://private-login__cr.kr:private%3Ap%40ssword@"
            "gw.dataimpulse.com:823",
        )
        attempt = _create_attempt(self.settings, None)
        try:
            self.assertFalse(attempt._session.trust_env)
            self.assertEqual(
                attempt._session.proxies,
                {"http": proxy_url, "https": proxy_url},
            )
        finally:
            attempt.close()

    def test_library_proxy_session_has_no_cookie_input_or_direct_egress(self) -> None:
        attempt = _create_attempt(self.settings, None)
        try:
            self.assertFalse(attempt._session.trust_env)
            self.assertEqual(
                attempt._session.proxies,
                {
                    "http": _proxy_url(self.settings),
                    "https": _proxy_url(self.settings),
                },
            )
            self.assertFalse(hasattr(attempt, "cookie_file"))
        finally:
            attempt.close()

    def test_video_file_requires_three_to_five_unique_valid_ids(self) -> None:
        for payload, message in (
            ({"video_ids": list(VIDEO_IDS[:2])}, "between 3 and 5"),
            ({"video_ids": [VIDEO_IDS[0]] * 3}, "must not contain duplicates"),
            (
                {"video_ids": [*VIDEO_IDS[:2], "private-invalid-id"]},
                "11-character YouTube ID",
            ),
        ):
            with self.subTest(message=message):
                with TemporaryDirectory() as directory:
                    path = Path(directory) / "canary.json"
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(
                        CanaryConfigurationError, message
                    ) as error:
                        _load_video_ids(path)
                    self.assertNotIn("private-invalid-id", str(error.exception))

    def test_configuration_error_returns_two_without_exposing_a_secret(self) -> None:
        stderr = StringIO()
        with patch(
            "transcript.dataimpulse_canary.load_dataimpulse_proxy_settings",
            side_effect=ConfigurationError(
                "Missing required DataImpulse configuration: DATAIMPULSE_PROXY_PASSWORD"
            ),
        ):
            with redirect_stderr(stderr):
                exit_code = main(["--video-id-file", "private-video-file.json"])

        self.assertEqual(exit_code, 2)
        self.assertIn("DATAIMPULSE_PROXY_PASSWORD", stderr.getvalue())
        self.assertNotIn("private-video-file.json", stderr.getvalue())


def _success(text: str) -> TranscriptExtractionResult:
    return TranscriptExtractionResult.succeeded(
        (TranscriptSegment(0, 0, 1000, text),)
    )


def _failure(failure: TranscriptFailure) -> TranscriptExtractionResult:
    return TranscriptExtractionResult.failed(failure)


def _clock():
    value = 0.0

    def next_value() -> float:
        nonlocal value
        value += 0.1
        return value

    return next_value


if __name__ == "__main__":
    unittest.main()

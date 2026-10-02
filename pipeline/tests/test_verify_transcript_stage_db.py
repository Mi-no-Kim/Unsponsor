from __future__ import annotations

import io
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from common.config import MySqlSettings
from scripts.verify_transcript_stage_db import (
    W027VerificationError,
    _docker_environment,
    _docker_run_arguments,
    _flyway_environment,
    _parse_mount_inspection,
    _parse_published_port,
    _remove_container,
    _require_local_docker_endpoint,
    _run_stage,
    _validate_container_name,
    main,
)
from transcript.model import TranscriptExtractionResult, TranscriptFailure


class VerifyTranscriptStageDatabaseTests(unittest.TestCase):
    def test_accepts_only_local_docker_endpoints(self) -> None:
        _require_local_docker_endpoint("npipe:////./pipe/docker_engine")
        _require_local_docker_endpoint("unix:///var/run/docker.sock")

        for endpoint in (
            "tcp://127.0.0.1:2375",
            "tcp://database.internal:2375",
            "ssh://builder",
            "npipe://remote/pipe/docker_engine",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaisesRegex(
                    W027VerificationError, "docker_context_must_be_local"
                ):
                    _require_local_docker_endpoint(endpoint)

    def test_parses_only_loopback_published_mysql_port(self) -> None:
        self.assertEqual(_parse_published_port("127.0.0.1:49152\n"), 49152)

        for output in (
            "0.0.0.0:49152",
            "[::]:49152",
            "127.0.0.1:0",
            "127.0.0.1:65536",
            "",
        ):
            with self.subTest(output=output):
                with self.assertRaisesRegex(
                    W027VerificationError, "database_port_invalid"
                ):
                    _parse_published_port(output)

    def test_mount_inspection_requires_mysql_tmpfs_and_no_docker_volume(self) -> None:
        _parse_mount_inspection(
            '{"/var/lib/mysql":"rw,noexec,nosuid"}|\n'
        )

        for output, error_code in (
            ("{}|", "database_mount_invalid"),
            (
                '{"/var/lib/mysql":"rw,noexec,nosuid"}|volume',
                "database_volume_forbidden",
            ),
        ):
            with self.subTest(output=output):
                with self.assertRaisesRegex(W027VerificationError, error_code):
                    _parse_mount_inspection(output)

    def test_disposable_container_is_labeled_tmpfs_only_and_hides_secrets(self) -> None:
        arguments = _docker_run_arguments("unsponsor-w027-012345abcdef")
        serialized = " ".join(arguments)

        self.assertIn("127.0.0.1::3306/tcp", arguments)
        self.assertIn("mysql:8.4", arguments)
        self.assertIn("xyz.unsponsor.scope=w027", arguments)
        self.assertIn("--tmpfs", arguments)
        self.assertIn("/var/lib/mysql:rw,noexec,nosuid", arguments)
        self.assertNotIn("--volume", arguments)
        self.assertNotIn("-v", arguments)
        self.assertNotIn("database-secret", serialized)
        self.assertNotIn("root-secret", serialized)
        self.assertIn("MYSQL_PASSWORD", arguments)
        self.assertNotIn("MYSQL_PASSWORD=", serialized)

    def test_database_environments_override_development_settings(self) -> None:
        docker_environment = _docker_environment(
            {
                "MYSQL_DATABASE": "development",
                "MYSQL_PASSWORD": "development-secret",
            },
            database_name="w027db_random",
            database_user="w027u_random",
            database_password="ephemeral-secret",
            root_password="ephemeral-root-secret",
        )
        settings = MySqlSettings(
            host="127.0.0.1",
            port=49152,
            database="w027db_random",
            user="w027u_random",
            password="ephemeral-secret",
        )
        flyway_environment = _flyway_environment(
            settings,
            {
                "MYSQL_HOST": "development.internal",
                "MYSQL_DATABASE": "development",
                "MYSQL_PASSWORD": "development-secret",
            },
        )

        self.assertEqual(docker_environment["MYSQL_DATABASE"], "w027db_random")
        self.assertEqual(docker_environment["MYSQL_USER"], "w027u_random")
        self.assertEqual(docker_environment["MYSQL_PASSWORD"], "ephemeral-secret")
        self.assertEqual(
            docker_environment["MYSQL_ROOT_PASSWORD"], "ephemeral-root-secret"
        )
        self.assertEqual(flyway_environment["MYSQL_HOST"], "127.0.0.1")
        self.assertEqual(flyway_environment["MYSQL_PORT"], "49152")
        self.assertEqual(flyway_environment["MYSQL_DATABASE"], "w027db_random")
        self.assertEqual(flyway_environment["MYSQL_USER"], "w027u_random")
        self.assertEqual(flyway_environment["MYSQL_PASSWORD"], "ephemeral-secret")
        self.assertEqual(
            flyway_environment["SPRING_MAIN_WEB_APPLICATION_TYPE"], "none"
        )
        self.assertEqual(flyway_environment["SPRING_FLYWAY_TARGET"], "6")

    def test_cleanup_rejects_unscoped_names_and_mismatched_labels(self) -> None:
        with patch("scripts.verify_transcript_stage_db._run_docker") as run_docker:
            self.assertFalse(_remove_container("mysql-development"))
            run_docker.assert_not_called()

        inspected = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="other|unsponsor-w027-012345abcdef\n", stderr=""
        )
        with patch(
            "scripts.verify_transcript_stage_db._run_docker",
            return_value=inspected,
        ) as run_docker:
            self.assertFalse(_remove_container("unsponsor-w027-012345abcdef"))
            run_docker.assert_called_once()

    def test_cleanup_removes_only_the_exact_labeled_container(self) -> None:
        name = "unsponsor-w027-012345abcdef"
        inspected = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"w027|{name}\n", stderr=""
        )
        removed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with patch(
            "scripts.verify_transcript_stage_db._run_docker",
            side_effect=(inspected, removed),
        ) as run_docker:
            self.assertTrue(_remove_container(name))

        self.assertEqual(run_docker.call_count, 2)
        self.assertEqual(
            run_docker.call_args_list[1].args[0], ["rm", "--force", name]
        )

    def test_container_name_requires_w027_prefix_and_random_suffix(self) -> None:
        _validate_container_name("unsponsor-w027-012345abcdef")
        for name in (
            "unsponsor-w027-test",
            "unsponsor-w027-012345abcdef-extra",
            "mysql-012345abcdef",
            "",
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    W027VerificationError, "container_name_invalid"
                ):
                    _validate_container_name(name)

    def test_confirmation_is_required_before_disposable_database_work(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["--fixture-dir", "private-value"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--confirm-disposable-database is required", stderr.getvalue())
        self.assertNotIn("private-value", stderr.getvalue())

    def test_fixture_failure_prints_only_a_safe_code(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            main(
                [
                    "--fixture-dir",
                    "private-value",
                    "--confirm-disposable-database",
                ]
            )

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(
            stdout.getvalue().strip(), '{"status":"failed","error":"fixture_invalid"}'
        )
        self.assertNotIn("private-value", stdout.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue())

    def test_unexpected_error_prints_only_a_safe_code(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "scripts.verify_transcript_stage_db.TranscriptFixtureDataset.load",
                side_effect=RuntimeError("private database detail"),
            ),
            redirect_stdout(stdout),
            self.assertRaises(SystemExit) as raised,
        ):
            main(
                [
                    "--fixture-dir",
                    "private-value",
                    "--confirm-disposable-database",
                ]
            )

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(
            stdout.getvalue().strip(),
            '{"status":"failed","error":"unexpected_error"}',
        )
        self.assertNotIn("private", stdout.getvalue())
        self.assertNotIn("Traceback", stdout.getvalue())

    def test_stage_helper_matches_current_runner_constructor(self) -> None:
        queue = Mock()
        queue.recover_one_stuck_transcript.return_value = None
        queue.claim_next_transcript.return_value = None
        ytdlp = Mock()
        library = Mock()
        safe_result = TranscriptExtractionResult.failed(
            TranscriptFailure.NO_TRANSCRIPT
        )
        ytdlp.extract.return_value = safe_result
        library.extract.return_value = safe_result

        result = _run_stage(
            queue,
            Mock(),
            ytdlp,
            library_extractor=library,
        )

        self.assertEqual(result.ytdlp_count, 0)
        ytdlp.extract.assert_not_called()
        library.extract.assert_not_called()


if __name__ == "__main__":
    unittest.main()

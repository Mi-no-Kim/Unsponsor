from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common.restricted_error_log import configure_restricted_error_log


class RestrictedErrorLogTests(unittest.TestCase):
    def test_keeps_the_error_detail_but_redacts_credentials(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "errors.log"
            cookie_file = Path(directory) / "cookies.txt"
            cookie_file.write_text(
                ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\topaque-cookie-value\n",
                encoding="utf-8",
            )
            logger = configure_restricted_error_log(
                path, cookie_file=cookie_file, console_output=False
            )
            try:
                logger.error(
                    "video000001 upstream failed"
                )
                logger.error("Cookie: secret-cookie")
                logger.error("Authorization=Bearer secret-token")
                logger.error("https://example.test/?potoken=secret-potoken")
                logger.error("PoTokenResponse(po_token='secret-snake-token')")
                logger.error("provider returned poToken: secret-camel-token")
                logger.error("provider generated POT: secret-pot")
                logger.error(
                    "https://example.test/subtitle?sig=secret-signature"
                    "&lsig=secret-lsig&spc=secret-spc"
                )
                logger.error("unexpected parser detail: opaque-cookie-value")
                for handler in logger.handlers:
                    handler.flush()

                log_contents = path.read_text(encoding="utf-8")
            finally:
                for handler in tuple(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()

        self.assertIn("video000001 upstream failed", log_contents)
        self.assertIn("Cookie: <redacted>", log_contents)
        self.assertIn("Authorization=<redacted>", log_contents)
        self.assertIn("potoken=<redacted>", log_contents)
        self.assertNotIn("secret-cookie", log_contents)
        self.assertNotIn("secret-token", log_contents)
        self.assertNotIn("secret-potoken", log_contents)
        self.assertNotIn("secret-snake-token", log_contents)
        self.assertNotIn("secret-camel-token", log_contents)
        self.assertNotIn("secret-pot", log_contents)
        self.assertNotIn("secret-signature", log_contents)
        self.assertNotIn("secret-lsig", log_contents)
        self.assertNotIn("secret-spc", log_contents)
        self.assertNotIn("opaque-cookie-value", log_contents)

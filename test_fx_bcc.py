"""The FX briefs must BCC every recipient: no address in any header, all in the envelope."""

from __future__ import annotations

import os
import unittest
from unittest import mock

import usd_inr_tracker
from fx_config import Config


class BccTests(unittest.TestCase):
    def test_recipients_hidden_but_all_delivered(self):
        env = {
            "FX_RECEIVER_EMAIL": "a@example.com",
            "FX_EXTRA_RECEIVERS": "b@example.com",
            "FX_ADDITIONAL_RECEIVERS": "c@example.edu, A@example.com",
            "SENDER_EMAIL": "me@example.com",
            "SENDER_PASSWORD": "x",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = Config()
        self.assertEqual(config.receivers, ["a@example.com", "b@example.com", "c@example.edu"])

        with mock.patch("smtplib.SMTP") as smtp, \
                mock.patch.object(usd_inr_tracker.email_thread, "get_thread_headers", return_value={}), \
                mock.patch.object(usd_inr_tracker.email_thread, "mark_sent", return_value="<id@x>"):
            usd_inr_tracker.send_email(config, "subject", "<p>hi</p>")
            server = smtp.return_value.__enter__.return_value
            _, envelope, raw = server.sendmail.call_args.args

        self.assertEqual(envelope, config.receivers)
        headers = raw.split("\n\n", 1)[0]
        for address in config.receivers:
            self.assertNotIn(address, headers)
        self.assertNotIn("Bcc:", headers)
        self.assertIn("To: me@example.com", headers)


if __name__ == "__main__":
    unittest.main()

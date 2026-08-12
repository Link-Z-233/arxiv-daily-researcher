import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from notifications.notifier import (  # noqa: E402
    NotifierAgent,
    RunResult,
    WebhookNotifier,
)
from utils.daily_research_store import DailyResearchStore  # noqa: E402


class _Response:
    def __init__(self, payload=None, text="ok"):
        self.payload = payload
        self.text = text

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class NotificationOutboxTests(unittest.TestCase):
    def _store(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return DailyResearchStore(Path(temp_dir.name) / "daily.db")

    @staticmethod
    def _notifier(channels):
        notifier = NotifierAgent.__new__(NotifierAgent)
        notifier.settings = SimpleNamespace(
            RETRY_MAX_ATTEMPTS=2,
            RETRY_MIN_WAIT=1,
            RETRY_MAX_WAIT=2,
            NOTIFY_ON_SUCCESS=True,
            NOTIFY_ON_FAILURE=True,
            NOTIFY_ATTACH_REPORTS=False,
        )
        notifier.notifiers_by_channel = {channel: object() for channel in channels}
        notifier.notifiers = list(notifier.notifiers_by_channel.values())
        return notifier

    def test_outbox_is_idempotent_and_recovers_stale_claims(self):
        store = self._store()
        payload = {"result": {"run_timestamp": "2026-08-12 12:00:00"}}
        self.assertTrue(store.enqueue_notification("run-1", "daily_run_result", "email", payload))
        self.assertFalse(store.enqueue_notification("run-1", "daily_run_result", "email", payload))

        claimed = store.claim_due_notifications(event_type="daily_run_result")
        self.assertEqual(len(claimed), 1)
        row = claimed[0]
        self.assertEqual(row["status"], "sending")
        self.assertEqual(row["attempt_count"], 1)

        # A normal second process cannot claim it. Simulate a sender that died
        # long ago before recording a result, then recover the stale claim.
        self.assertEqual(store.claim_due_notifications(event_type="daily_run_result"), [])
        with store._connect() as conn:
            conn.execute(
                "UPDATE notification_outbox SET claimed_at = ? WHERE outbox_id = ?",
                ((datetime.now() - timedelta(seconds=10)).isoformat(), row["outbox_id"]),
            )
        recovered = store.claim_due_notifications(
            event_type="daily_run_result", stale_claim_seconds=1
        )
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["attempt_count"], 2)

        store.mark_notification_sent(recovered[0]["outbox_id"])
        sent = store.get_notification_outbox(recovered[0]["outbox_id"])
        self.assertEqual(sent["status"], "sent")
        self.assertIsNotNone(sent["sent_at"])
        self.assertEqual(store.get_pending_notification_count(), 0)

    def test_delivery_retries_only_the_failed_channel_and_keeps_it_pending(self):
        store = self._store()
        notifier = self._notifier(["email", "wechat_work"])
        result = RunResult(run_timestamp="2026-08-12 12:00:00", success=True)

        self.assertEqual(notifier.enqueue_run_result(store, "run-1", result), 2)
        calls = []

        def send(channel, _result):
            calls.append(channel)
            if channel == "wechat_work":
                raise RuntimeError("robot rejected message")

        with patch.object(notifier, "send_run_result_to_channel", side_effect=send), patch(
            "notifications.notifier.time.sleep"
        ):
            summary = notifier.deliver_pending_run_results(store)

        self.assertEqual(summary, {"claimed": 2, "sent": 1, "deferred": 1})
        self.assertEqual(calls.count("email"), 1)
        self.assertEqual(calls.count("wechat_work"), 2)

        rows = store.claim_due_notifications(event_type="daily_run_result", limit=10)
        # The failed row has a future retry time and must not be immediately sent
        # again as part of a second report or a duplicate channel delivery.
        self.assertEqual(rows, [])

        with store._connect() as conn:
            status_rows = conn.execute(
                "SELECT channel, status, attempt_count, last_error FROM notification_outbox "
                "ORDER BY channel"
            ).fetchall()
        by_channel = {row["channel"]: row for row in status_rows}
        self.assertEqual(by_channel["email"]["status"], "sent")
        self.assertEqual(by_channel["wechat_work"]["status"], "pending")
        self.assertEqual(by_channel["wechat_work"]["attempt_count"], 2)
        self.assertIn("robot rejected", by_channel["wechat_work"]["last_error"])

    def test_outbox_reuses_existing_rows_after_restart_without_duplicate_enqueue(self):
        store = self._store()
        first = self._notifier(["email"])
        second = self._notifier(["email"])
        result = RunResult(run_timestamp="2026-08-12 12:00:00", success=True)

        self.assertEqual(first.enqueue_run_result(store, "run-1", result), 1)
        self.assertEqual(second.enqueue_run_result(store, "run-1", result), 0)

        with patch.object(second, "send_run_result_to_channel") as send:
            summary = second.deliver_pending_run_results(store)
        self.assertEqual(summary, {"claimed": 1, "sent": 1, "deferred": 0})
        send.assert_called_once_with("email", result)

    def test_webhook_application_errors_are_not_accepted_as_success(self):
        with self.assertRaisesRegex(RuntimeError, "errcode=93000"):
            WebhookNotifier("wechat_work", "https://example.invalid")._validate_platform_response(
                _Response({"errcode": 93000, "errmsg": "invalid webhook"})
            )
        with self.assertRaisesRegex(RuntimeError, "description"):
            WebhookNotifier("telegram", "https://example.invalid")._validate_platform_response(
                _Response({"ok": False, "description": "chat not found"})
            )
        with self.assertRaisesRegex(RuntimeError, "invalid_payload"):
            WebhookNotifier("slack", "https://example.invalid")._validate_platform_response(
                _Response(text="invalid_payload")
            )

        WebhookNotifier("dingtalk", "https://example.invalid")._validate_platform_response(
            _Response({"errcode": 0, "errmsg": "ok"})
        )
        WebhookNotifier("telegram", "https://example.invalid")._validate_platform_response(
            _Response({"ok": True, "result": {}})
        )
        WebhookNotifier("slack", "https://example.invalid")._validate_platform_response(
            _Response(text="ok")
        )


if __name__ == "__main__":
    unittest.main()

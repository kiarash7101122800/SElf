import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon.errors import FloodWaitError

from control_store import (
    add_auto_reply_response,
    claim_due_schedule_jobs,
    create_auto_reply_rule,
    create_schedule_job,
    delete_auto_reply_rule,
    find_auto_reply_candidates,
    finish_schedule_job_run,
    get_self_settings,
    list_auto_reply_rules,
    list_schedule_jobs,
    set_schedule_job_status,
)
from send_queue import SmartSendQueue
from self_bot import TelegramAccount


class V211StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name)
        self.phone = "+989120000000"

    def tearDown(self):
        self.temporary.cleanup()

    def test_multi_response_rule_supports_text_and_media(self):
        rule_id = create_auto_reply_rule(
            self.data_dir,
            self.phone,
            ["قیمت", "هزینه"],
        )
        add_auto_reply_response(
            self.data_dir,
            self.phone,
            rule_id,
            response_type="text",
            content_text="پاسخ اول",
        )
        media = self.data_dir / "answer.jpg"
        media.write_bytes(b"image")
        add_auto_reply_response(
            self.data_dir,
            self.phone,
            rule_id,
            response_type="photo",
            media_path=str(media),
            caption="پاسخ دوم",
        )

        candidates = find_auto_reply_candidates(
            self.data_dir,
            self.phone,
            message_text="هزینه سرویس چقدر است؟",
            scope="private",
        )
        self.assertEqual(2, len(candidates))
        self.assertEqual({"text", "photo"}, {
            item["response_type"] for item in candidates
        })
        rules = list_auto_reply_rules(self.data_dir, self.phone)
        self.assertEqual(2, int(rules[0]["response_count"]))
        self.assertTrue(delete_auto_reply_rule(
            self.data_dir,
            self.phone,
            rule_id,
        ))
        self.assertEqual([], list_auto_reply_rules(
            self.data_dir,
            self.phone,
        ))

    def test_professional_schedule_can_pause_claim_and_complete(self):
        now = datetime.now(timezone.utc)
        job_id = create_schedule_job(
            self.data_dir,
            self.phone,
            target="@example",
            message_type="text",
            message_text="سلام",
            recurrence_type="daily",
            recurrence_value="18:30",
            next_run_at=(now - timedelta(minutes=1)).isoformat(),
        )
        self.assertTrue(set_schedule_job_status(
            self.data_dir,
            self.phone,
            job_id,
            "paused",
        ))
        self.assertEqual(
            "paused",
            list_schedule_jobs(self.data_dir, self.phone)[0]["status"],
        )
        self.assertTrue(set_schedule_job_status(
            self.data_dir,
            self.phone,
            job_id,
            "active",
        ))
        claimed = claim_due_schedule_jobs(
            self.data_dir,
            self.phone,
            now_iso=now.isoformat(),
            stale_before_iso=(now - timedelta(minutes=10)).isoformat(),
        )
        self.assertEqual([job_id], [item["id"] for item in claimed])
        next_run = (now + timedelta(days=1)).isoformat()
        finish_schedule_job_run(
            self.data_dir,
            self.phone,
            job_id,
            next_run_at=next_run,
            message_id=123,
        )
        row = list_schedule_jobs(self.data_dir, self.phone)[0]
        self.assertEqual("active", row["status"])
        self.assertEqual(1, row["run_count"])
        self.assertEqual(123, row["last_message_id"])

    def test_new_defaults_are_added_without_overwriting_old_settings(self):
        old_db = self.data_dir / "bot_data_989120000000.db"
        with sqlite3.connect(old_db) as connection:
            connection.execute(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)"
            )
            connection.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("online_status", "off"),
            )
        settings = get_self_settings(self.data_dir, self.phone)
        self.assertEqual("off", settings["online_status"])
        self.assertEqual("off", settings["presence_emoji_enabled"])
        self.assertEqual("🟢", settings["online_name_emoji"])
        self.assertEqual("🔴", settings["offline_name_emoji"])
        self.assertEqual(
            "Abolfazl 🟢",
            TelegramAccount.presence_name("Abolfazl", "🟢"),
        )


class V211QueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_floodwait_pauses_and_retries_same_operation(self):
        queue = SmartSendQueue(min_interval_seconds=0.05)
        attempts = 0

        async def operation():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise FloodWaitError(None, 0)
            return "sent"

        try:
            result = await queue.execute(operation)
        finally:
            await queue.close()
        self.assertEqual("sent", result)
        self.assertEqual(2, attempts)
        self.assertEqual(1, queue.last_floodwait_seconds)


if __name__ == "__main__":
    unittest.main()

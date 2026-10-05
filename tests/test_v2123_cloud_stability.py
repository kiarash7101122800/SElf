import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from admin_center import AdminCenterStore
from control_store import (
    anti_delete_directory,
    create_scheduled_once,
    list_due_scheduled_once,
    list_scheduled_once,
)
from main_bot import TelegramAuthBot
from self_bot import TelegramAccount
from send_queue import SmartSendQueue


class V2123CloudStabilityTests(unittest.TestCase):
    def make_main_bot(self, db_path: Path):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        bot.user_coins = {}
        bot.active_games = {}
        bot.game_operation_locks = {}
        bot.owner_id = 999
        with patch("main_bot.USERS_DB", db_path), patch(
            "main_bot.DATA_DIR", db_path.parent
        ):
            bot.init_users_db()
        return bot

    def test_activation_reservation_refunds_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "users.db"
            bot = self.make_main_bot(db)
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "INSERT INTO users(user_id, coins, join_date, is_active) "
                    "VALUES(1, 100, CURRENT_TIMESTAMP, 1)"
                )
            bot.user_coins = {1: 100}
            with patch("main_bot.USERS_DB", db):
                reservation = bot.reserve_activation_cost(1, "+98912", 25)
                first = bot.finish_activation_reservation(
                    reservation["id"], success=False, error_text="failed"
                )
                second = bot.finish_activation_reservation(
                    reservation["id"], success=False, error_text="again"
                )
            self.assertEqual(first, 100)
            self.assertEqual(second, 100)
            with sqlite3.connect(db) as conn:
                refund_count = conn.execute(
                    "SELECT COUNT(*) FROM balance_transactions "
                    "WHERE transaction_type='activation_refund'"
                ).fetchone()[0]
            self.assertEqual(refund_count, 1)

    def test_duplicate_phone_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "users.db"
            bot = self.make_main_bot(db)
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "INSERT INTO users(user_id, phone, coins, join_date, is_active) "
                    "VALUES(1, '+98912', 10, CURRENT_TIMESTAMP, 1)"
                )
                conn.execute(
                    "INSERT INTO users(user_id, coins, join_date, is_active) "
                    "VALUES(2, 10, CURRENT_TIMESTAMP, 1)"
                )
            with patch("main_bot.USERS_DB", db):
                with self.assertRaises(ValueError):
                    bot.reserve_activation_cost(2, "+98912", 1)

    def test_expiration_is_iso_aware_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "users.db"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "CREATE TABLE users(phone TEXT, expiration_date TEXT, "
                    "is_active INTEGER, self_enabled INTEGER)"
                )
                conn.execute(
                    "INSERT INTO users VALUES(?, ?, 1, 1)",
                    ("+98912", (datetime.now() + timedelta(hours=1)).isoformat()),
                )
            account = TelegramAccount.__new__(TelegramAccount)
            account.phone = "+98912"
            with patch("self_bot.USERS_DB", db):
                self.assertTrue(account.is_self_valid())
                with sqlite3.connect(db) as conn:
                    conn.execute(
                        "UPDATE users SET expiration_date=?",
                        ((datetime.now() - timedelta(hours=1)).isoformat(),),
                    )
                self.assertFalse(account.is_self_valid())
                with sqlite3.connect(db) as conn:
                    conn.execute("UPDATE users SET expiration_date='broken'")
                self.assertFalse(account.is_self_valid())

    def test_cloud_backup_never_creates_zip_on_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            sessions = root / "sessions"
            data.mkdir(); sessions.mkdir()
            users = data / "users.db"
            with sqlite3.connect(users) as conn:
                conn.execute("CREATE TABLE users(user_id INTEGER PRIMARY KEY)")
                conn.execute("CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
                conn.execute("CREATE TABLE app_admins(user_id INTEGER PRIMARY KEY, added_by INTEGER, added_at TEXT)")
            store = AdminCenterStore(users, data, sessions)
            backup = store.create_backup(1)
            self.assertTrue(backup["content"].startswith(b"PK"))
            self.assertEqual(list(root.rglob("*.zip")), [])

    def test_anti_delete_path_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = anti_delete_directory(tmp, "+98912")
            self.assertFalse(path.exists())

    def test_due_message_is_claimed_once_and_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            schedule_id = create_scheduled_once(
                tmp, "+98912", target="me", message_text="hello",
                send_at="2000-01-01T00:00:00"
            )
            first = list_due_scheduled_once(
                tmp, "+98912", now_iso="2099-01-01T00:00:00"
            )
            second = list_due_scheduled_once(
                tmp, "+98912", now_iso="2099-01-01T00:00:00"
            )
            self.assertEqual([row["id"] for row in first], [schedule_id])
            self.assertEqual(second, [])
            row = list_scheduled_once(tmp, "+98912", limit=5)[0]
            self.assertEqual(row["status"], "uncertain")


class SendQueueShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_rejects_waiting_jobs(self):
        queue = SmartSendQueue(min_interval_seconds=0.05)
        queue.start()
        gate = asyncio.Event()

        async def blocked():
            await gate.wait()
            return "done"

        first = asyncio.create_task(queue.execute(blocked))
        second = asyncio.create_task(queue.execute(blocked))
        await asyncio.sleep(0.02)
        await queue.close()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertTrue(all(isinstance(item, Exception) for item in results))


if __name__ == "__main__":
    unittest.main()

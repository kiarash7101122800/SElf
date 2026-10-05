import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from admin_center import AdminCenterStore, CURRENT_RELEASE
from control_store import (
    add_enemy_hostile_replies,
    add_friend_affection_replies,
    list_enemy_hostile_replies,
    list_friend_affection_replies,
)
from session_vault import read_session_file, write_session_file


class V210AdminStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "data"
        self.sessions_dir = self.root / "sessions"
        self.data_dir.mkdir()
        self.sessions_dir.mkdir()
        self.users_db = self.data_dir / "users.db"
        with sqlite3.connect(self.users_db) as conn:
            conn.executescript(
                """
                CREATE TABLE users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    phone TEXT,
                    coins INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    expiration_date TEXT,
                    self_enabled INTEGER DEFAULT 0,
                    self_status TEXT,
                    session_file TEXT,
                    updated_at TEXT
                );
                CREATE TABLE app_admins (
                    user_id INTEGER PRIMARY KEY,
                    added_by INTEGER,
                    added_at TEXT
                );
                CREATE TABLE app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT
                );
                CREATE TABLE balance_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL,
                    transaction_type TEXT NOT NULL,
                    admin_id INTEGER,
                    note TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE payment_receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    amount_toman INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT
                );
                """
            )
            conn.execute(
                """INSERT INTO users
                   (user_id, phone, coins, is_active, self_enabled,
                    self_status, session_file, updated_at)
                   VALUES (100, '+989120000000', 500, 1, 1, 'running',
                           'session_100.txt', datetime('now'))"""
            )
        self.store = AdminCenterStore(
            self.users_db, self.data_dir, self.sessions_dir
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_login_requests_and_failures_are_rate_limited(self):
        for _ in range(3):
            allowed, wait = self.store.register_login_request(100)
            self.assertTrue(allowed)
            self.assertEqual(wait, 0)
        allowed, wait = self.store.register_login_request(100)
        self.assertFalse(allowed)
        self.assertGreater(wait, 0)

        for attempt in range(1, 6):
            can_retry, failures = self.store.record_login_failure(200)
            self.assertEqual(failures, attempt)
        self.assertFalse(can_retry)
        self.assertEqual(self.store.login_security_summary()["blocked"], 2)

    def test_auto_renewal_charges_wallet_and_extends_expiry(self):
        plan_id = self.store.create_plan("ماهانه ویژه", 30, 120, 0, 1)
        self.store.assign_plan(100, plan_id, 1, auto_renew=True)
        expired = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
        with self.store.connect() as conn:
            conn.execute(
                """UPDATE user_subscriptions SET expires_at = ?
                   WHERE user_id = 100 AND status = 'active'""",
                (expired,),
            )
            conn.execute(
                "UPDATE users SET expiration_date = ? WHERE user_id = 100",
                (expired,),
            )

        results = self.store.process_auto_renewals()

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["renewed"])
        with self.store.connect() as conn:
            user = conn.execute(
                "SELECT coins, expiration_date FROM users WHERE user_id = 100"
            ).fetchone()
            transaction = conn.execute(
                """SELECT transaction_type, amount
                   FROM balance_transactions ORDER BY id DESC LIMIT 1"""
            ).fetchone()
        self.assertEqual(user["coins"], 380)
        self.assertGreater(
            datetime.fromisoformat(user["expiration_date"]),
            datetime.utcnow(),
        )
        self.assertEqual(
            transaction["transaction_type"], "subscription_auto_renew"
        )
        self.assertEqual(transaction["amount"], -120)

    def test_expiry_notifications_are_unique_per_expiration(self):
        plan_id = self.store.create_plan("سه روزه", 3, 10, 0, 1)
        self.store.assign_plan(100, plan_id, 1)
        soon = (datetime.utcnow() + timedelta(days=2)).isoformat()
        with self.store.connect() as conn:
            conn.execute(
                """UPDATE user_subscriptions SET expires_at = ?
                   WHERE user_id = 100 AND status = 'active'""",
                (soon,),
            )
            conn.execute(
                "UPDATE users SET expiration_date = ? WHERE user_id = 100",
                (soon,),
            )
        notices = self.store.queue_due_expiry_notifications()
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["threshold_days"], 3)
        self.store.mark_expiry_notification(notices[0]["id"], sent=True)
        self.assertEqual(self.store.queue_due_expiry_notifications(), [])

    def test_release_tracking_and_financial_summary(self):
        self.store.record_self_release(
            100,
            to_version=CURRENT_RELEASE,
            code_hash="abc123",
            status="success",
            reason="test",
        )
        release = self.store.release_summary(CURRENT_RELEASE)
        self.assertEqual(release["current"], 1)
        self.assertEqual(release["outdated"], 0)

        with self.store.connect() as conn:
            conn.execute(
                """INSERT INTO payment_receipts
                   (amount_toman, status, created_at)
                   VALUES (250000, 'approved', ?)""",
                (datetime.utcnow().isoformat(),),
            )
        summary = self.store.financial_summary(30)
        self.assertEqual(summary["approved"], 1)
        self.assertEqual(summary["revenue"], 250000)


class V210BulkInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.phone = "+989121111111"

    def tearDown(self):
        self.temp.cleanup()

    def test_friend_and_enemy_replies_support_bulk_add(self):
        inserted, skipped = add_friend_affection_replies(
            self.temp.name,
            self.phone,
            ["سلام رفیق", "خوش اومدی", "سلام رفیق"],
        )
        self.assertEqual((inserted, skipped), (2, 0))
        inserted, skipped = add_friend_affection_replies(
            self.temp.name,
            self.phone,
            ["سلام رفیق", "خوش اومدی"],
        )
        self.assertEqual((inserted, skipped), (0, 2))
        self.assertEqual(
            len(list_friend_affection_replies(self.temp.name, self.phone)),
            2,
        )

        inserted, skipped = add_enemy_hostile_replies(
            self.temp.name,
            self.phone,
            ["پیام اول", "پیام دوم"],
        )
        self.assertEqual((inserted, skipped), (2, 0))
        self.assertEqual(
            len(list_enemy_hostile_replies(self.temp.name, self.phone)),
            2,
        )

    def test_panel_configuration_no_longer_parses_pipe_separated_input(self):
        project = Path(__file__).resolve().parents[1]
        for filename in ("admin_ui.py", "helper_bot.py"):
            source = (project / filename).read_text(encoding="utf-8")
            self.assertNotIn('split("|")', source)
            self.assertNotIn('split("|",', source)
        helper_source = (project / "helper_bot.py").read_text(encoding="utf-8")
        self.assertIn("مرحله ۱ از ۳ — کانال کامنت اول", helper_source)
        self.assertIn("هر متن را در یک خط جدا", helper_source)

    def test_sessions_are_encrypted_and_plaintext_is_migrated(self):
        data_dir = Path(self.temp.name) / "data"
        session_path = Path(self.temp.name) / "session.txt"
        secret = "1AZExampleTelegramStringSession"

        write_session_file(session_path, data_dir, secret)
        stored = session_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, stored)
        self.assertEqual(read_session_file(session_path, data_dir), secret)

        legacy_path = Path(self.temp.name) / "legacy.txt"
        legacy_path.write_text(secret, encoding="utf-8")
        self.assertEqual(read_session_file(legacy_path, data_dir), secret)
        self.assertNotIn(secret, legacy_path.read_text(encoding="utf-8"))

    def test_helper_power_control_is_visible_in_control_center(self):
        project = Path(__file__).resolve().parents[1]
        source = (project / "admin_ui.py").read_text(encoding="utf-8")
        self.assertIn("🔴 خاموش‌کردن هلپر", source)
        self.assertIn("🟢 روشن‌کردن هلپر", source)
        self.assertIn('"admin:helper:stop"', source)
        self.assertIn('"admin:helper:restart"', source)

    def test_flood_wait_does_not_trigger_fallback_send(self):
        project = Path(__file__).resolve().parents[1]
        feature_source = (project / "self_features.py").read_text(
            encoding="utf-8"
        )
        panel_source = (project / "self_bot.py").read_text(encoding="utf-8")
        self.assertIn("except MessageNotModifiedError:", feature_source)
        self.assertIn(
            "except FloodWaitError:\n"
            "            # A fallback SendMessageRequest",
            feature_source,
        )
        self.assertIn(
            "محدودیت تلگرام برای پنل هلپر",
            panel_source,
        )


if __name__ == "__main__":
    unittest.main()

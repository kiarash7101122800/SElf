import io
import sqlite3
import zipfile
import tempfile
import unittest
from pathlib import Path

from admin_center import AdminCenterStore


class AdminCenterTests(unittest.TestCase):
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
                   (user_id, username, first_name, phone, coins, is_active,
                    self_enabled, self_status, session_file, updated_at)
                   VALUES (100, 'testuser', 'Test', '+989121234567', 500, 1,
                           1, 'running', 'session.txt', datetime('now'))"""
            )
            conn.execute(
                """INSERT INTO app_admins(user_id, added_by, added_at)
                   VALUES (200, 1, datetime('now'))"""
            )
        self.store = AdminCenterStore(
            self.users_db, self.data_dir, self.sessions_dir
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_seeds_default_plans(self):
        names = [row["name"] for row in self.store.list_plans()]
        self.assertEqual(names, ["دائمی", "آزمایشی", "ماهانه"])

    def test_admin_roles_enforce_permissions(self):
        self.store.set_admin_role(200, "support", 1)
        self.assertTrue(self.store.can(200, 1, "support"))
        self.assertFalse(self.store.can(200, 1, "finance"))
        self.assertTrue(self.store.can(1, 1, "finance"))

    def test_balance_adjustment_is_atomic_and_audited(self):
        balance = self.store.adjust_balance(100, -125, 1, "test")
        self.assertEqual(balance, 375)
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM balance_transactions ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row["amount"], -125)
        self.assertEqual(row["balance_after"], 375)
        with self.assertRaises(ValueError):
            self.store.adjust_balance(100, -500, 1, "too much")

    def test_plan_assignment_updates_expiration_and_subscription(self):
        plan_id = self.store.create_plan("هفتگی", 7, 90, 0, 1)
        expires = self.store.assign_plan(100, plan_id, 1)
        self.assertIsNotNone(expires)
        subscription = self.store.active_subscription(100)
        self.assertEqual(subscription["plan_name"], "هفتگی")
        with self.store.connect() as conn:
            user = conn.execute(
                "SELECT expiration_date FROM users WHERE user_id = 100"
            ).fetchone()
        self.assertEqual(user["expiration_date"], expires)

    def test_feature_precedence_and_self_database_application(self):
        self_db = self.data_dir / "self_test.db"
        with sqlite3.connect(self_db) as conn:
            conn.execute(
                "CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT)"
            )
        self.store.set_feature_policy("global", "", "games", False, 1)
        self.store.set_feature_policy("user", 100, "games", True, 1)
        states = self.store.apply_features_to_self(100, self_db)
        self.assertTrue(states["games"])
        with sqlite3.connect(self_db) as conn:
            value = conn.execute(
                "SELECT value FROM settings WHERE key = 'games_enabled'"
            ).fetchone()[0]
        self.assertEqual(value, "on")

    def test_broadcast_lifecycle_and_cancellation(self):
        broadcast_id = self.store.create_broadcast(
            "active", "hello", "2000-01-01T00:00:00", 1
        )
        claimed = self.store.claim_due_broadcast()
        self.assertEqual(claimed["id"], broadcast_id)
        self.store.record_broadcast_delivery(broadcast_id, 100, True)
        self.store.finish_broadcast(broadcast_id, 1, 0, False)
        item = self.store.list_broadcasts(1)[0]
        self.assertEqual(item["status"], "completed")
        self.assertEqual(item["success_count"], 1)

    def test_support_ticket_reply_and_close(self):
        ticket_id = self.store.open_support_ticket(100, "help me")
        target = self.store.reply_ticket(ticket_id, 200, "done")
        self.assertEqual(target, 100)
        ticket, messages = self.store.ticket(ticket_id)
        self.assertEqual(ticket["status"], "answered")
        self.assertEqual(len(messages), 2)
        self.store.close_ticket(ticket_id, 200)
        ticket, _ = self.store.ticket(ticket_id)
        self.assertEqual(ticket["status"], "closed")

    def test_multiple_force_join_channels(self):
        first = self.store.upsert_force_join_channel(
            -1001, "one", "One", "https://t.me/one", 1
        )
        self.store.upsert_force_join_channel(
            -1002, "two", "Two", "https://t.me/two", 1
        )
        self.assertEqual(len(self.store.active_force_join_channels()), 2)
        self.store.toggle_force_join_channel(first, 1)
        self.assertEqual(len(self.store.active_force_join_channels()), 1)
        self.store.delete_force_join_channel(first, 1)
        self.assertEqual(len(self.store.list_force_join_channels()), 1)

    def test_backup_creation_contains_database_snapshot(self):
        before = set(self.root.rglob("*.zip"))
        backup = self.store.create_backup(1)
        self.assertGreater(backup["size_bytes"], 0)
        self.assertIsInstance(backup["content"], bytes)
        with zipfile.ZipFile(io.BytesIO(backup["content"])) as archive:
            self.assertIn("data/users.db", archive.namelist())
            self.assertIn("manifest.json", archive.namelist())
        self.assertEqual(before, set(self.root.rglob("*.zip")))


if __name__ == "__main__":
    unittest.main()

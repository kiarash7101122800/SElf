import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telethon import functions, types

from control_store import (
    create_scheduled_once,
    ensure_self_settings,
    get_self_settings,
    list_due_scheduled_once,
    list_private_allowlist,
    private_user_is_allowed,
    set_private_allowlist_user,
)
from helper_bot import HelperPanelBot
from self_features import FeatureEngine


OWNER_ID = 100
USER_ID = 200
CHAT_ID = -1001234567890


class FakeClient:
    def __init__(self):
        self.requests = []
        self.sent_messages = []
        self.sent_files = []
        self.blocked = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.contacts.BlockRequest):
            self.blocked.append(request.id)
            return True
        if isinstance(request, functions.messages.SendMediaRequest):
            message = SimpleNamespace(
                id=99,
                chat_id=CHAT_ID,
                media=types.MessageMediaDice(
                    value=4,
                    emoticon=request.media.emoticon,
                ),
                delete=AsyncMock(),
            )
            return SimpleNamespace(
                updates=[SimpleNamespace(message=message)]
            )
        return True

    async def get_input_entity(self, chat_id):
        return f"peer:{chat_id}"

    async def send_message(self, target, text, **kwargs):
        self.sent_messages.append((target, text, kwargs))
        return SimpleNamespace(id=len(self.sent_messages))

    async def send_file(self, target, file, **kwargs):
        self.sent_files.append((target, file, kwargs))
        return SimpleNamespace(id=len(self.sent_files))

    def is_connected(self):
        return True


class FakeAccount:
    def __init__(self, data_dir):
        self.phone = "+989120000000"
        self.owner_id = OWNER_ID
        self.account_manager_data_dir = Path(data_dir)
        self.users_db_path = Path(data_dir) / "users.db"
        self.client = FakeClient()
        self.is_running = True
        self.shutdown_requested = False
        self.last_activity = 0
        self._settings = get_self_settings(data_dir, self.phone)

    def get_data(self):
        return dict(self._settings)

    def put_data(self, values):
        self._settings = dict(values)

    @staticmethod
    def is_owner_outgoing_event(event):
        return bool(getattr(event, "out", False))


class FakeIncoming:
    def __init__(
        self,
        *,
        text="سلام",
        message_id=10,
        private=True,
        group=False,
    ):
        self.raw_text = text
        self.id = message_id
        self.chat_id = USER_ID if private else CHAT_ID
        self.sender_id = USER_ID
        self.is_private = private
        self.is_group = group
        self.deleted = False
        self.replies = []

    async def get_sender(self):
        return SimpleNamespace(
            id=USER_ID,
            first_name="کاربر",
            last_name="آزمایشی",
            username="tester",
            bot=False,
        )

    async def get_chat(self):
        return SimpleNamespace(
            id=self.chat_id,
            title="گروه تست" if self.is_group else "",
            first_name="کاربر",
            last_name="آزمایشی",
            username="tester",
        )

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def delete(self):
        self.deleted = True


class AdvancedSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temporary.name)
        self.account = FakeAccount(self.data_dir)
        self.engine = FeatureEngine(self.account)
        self.advanced = self.engine.advanced

    def tearDown(self):
        self.temporary.cleanup()

    async def test_private_lock_warns_then_blocks_unknown_user(self):
        self.engine.save_settings(
            {
                "private_lock_enabled": "on",
                "private_lock_warning_limit": "1",
                "private_lock_warning_text": "هشدار آزمایشی",
            }
        )
        first = FakeIncoming(message_id=1)
        second = FakeIncoming(message_id=2)

        self.assertTrue(await self.advanced.enforce_private_lock(first))
        self.assertTrue(first.deleted)
        self.assertEqual(first.replies[0][0], "هشدار آزمایشی")
        self.assertEqual(self.account.client.blocked, [])

        self.assertTrue(await self.advanced.enforce_private_lock(second))
        self.assertTrue(second.deleted)
        self.assertEqual(self.account.client.blocked, [USER_ID])

    async def test_allowlisted_user_bypasses_private_lock(self):
        self.engine.save_settings({"private_lock_enabled": "on"})
        set_private_allowlist_user(
            self.data_dir,
            self.account.phone,
            USER_ID,
            allowed=True,
            label="کاربر تست",
        )
        event = FakeIncoming()

        self.assertFalse(await self.advanced.enforce_private_lock(event))
        self.assertFalse(event.deleted)
        self.assertTrue(
            private_user_is_allowed(
                self.data_dir,
                self.account.phone,
                USER_ID,
            )
        )
        self.assertEqual(
            list_private_allowlist(
                self.data_dir,
                self.account.phone,
            )[0]["label"],
            "کاربر تست",
        )

    async def test_anti_edit_reports_before_and_after(self):
        self.engine.save_settings({"anti_edit_private": "on"})
        original = FakeIncoming(text="متن قبل", message_id=77)
        await self.advanced.remember_incoming_message(original)
        edited = FakeIncoming(text="متن بعد", message_id=77)

        await self.advanced.handle_message_edited(edited)

        self.assertEqual(len(self.account.client.sent_messages), 1)
        target, report, _ = self.account.client.sent_messages[0]
        self.assertEqual(target, "me")
        self.assertIn("متن قبل", report)
        self.assertIn("متن بعد", report)

    async def test_group_game_uses_direct_send_media_request(self):
        sent = await self.engine.send_official_game(CHAT_ID, "🎲")

        self.assertEqual(sent.chat_id, CHAT_ID)
        request = self.account.client.requests[0]
        self.assertIsInstance(request, functions.messages.SendMediaRequest)
        self.assertEqual(request.peer, f"peer:{CHAT_ID}")
        self.assertEqual(request.media.emoticon, "🎲")


class AdvancedStorageAndPanelTests(unittest.TestCase):
    def test_old_database_migrates_without_losing_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            db_path = data_dir / "bot_data_989120000001.db"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)"
                )
                connection.execute(
                    "INSERT INTO settings VALUES ('font', '9')"
                )

            ensure_self_settings(data_dir, "+989120000001")
            settings = get_self_settings(data_dir, "+989120000001")
            self.assertEqual(settings["font"], "9")
            self.assertEqual(settings["private_lock_enabled"], "off")
            schedule_id = create_scheduled_once(
                data_dir,
                "+989120000001",
                target="-1001",
                message_text="سلام",
                send_at="2026-07-29T18:30:00+04:00",
            )
            due = list_due_scheduled_once(
                data_dir,
                "+989120000001",
                now_iso="2026-07-29T18:31:00+04:00",
            )
            self.assertEqual(due[0]["id"], schedule_id)

    def test_reorganized_panel_has_compact_rows_and_bilingual_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            panel = HelperPanelBot.__new__(HelperPanelBot)
            panel.data_dir = Path(temporary)
            panel.users_db = panel.data_dir / "users.db"
            record = {
                "phone": "+989120000001",
                "self_pid": None,
                "is_active": 1,
                "updated_at": "2026-07-29",
            }

            text, keyboard = panel.build_page(OWNER_ID, record, "home")
            self.assertIn("پنل مدیریت سلف", text)
            labels = [
                button.text
                for row in keyboard.inline_keyboard
                for button in row
            ]
            self.assertIn("🔐 امنیت", labels)
            self.assertTrue(
                all(len(row) <= 2 for row in keyboard.inline_keyboard)
            )
            self.assertEqual(len(labels), 6)
            self.assertIn("⚙️ سایر تنظیمات", labels)

            from control_store import set_self_setting

            set_self_setting(
                panel.data_dir,
                record["phone"],
                "panel_language",
                "en",
            )
            text, keyboard = panel.build_page(OWNER_ID, record, "home")
            self.assertIn("Self-bot Control Panel", text)
            labels = [
                button.text
                for row in keyboard.inline_keyboard
                for button in row
            ]
            self.assertIn("🔐 Security", labels)


if __name__ == "__main__":
    unittest.main()

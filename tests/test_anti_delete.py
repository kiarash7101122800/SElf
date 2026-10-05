import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from control_store import (
    archive_message,
    count_archived_messages,
    ensure_self_settings,
    get_archived_messages,
    get_self_settings,
    remove_archived_message,
)
from self_features import FeatureEngine


class FakeClient:
    def __init__(self):
        self.sent_messages = []
        self.sent_files = []

    async def download_media(self, message, file):
        target = Path(file)
        target.write_bytes(b"ordinary-media")
        return str(target)

    async def send_message(self, target, text, **kwargs):
        self.sent_messages.append((target, text, kwargs))

    async def send_file(self, target, file, **kwargs):
        self.sent_files.append((target, str(file), kwargs))


class FakeAccount:
    def __init__(self, data_dir):
        self.phone = "+989120000000"
        self.owner_id = 9001
        self.account_manager_data_dir = Path(data_dir)
        self.users_db_path = Path(data_dir) / "users.db"
        self.client = FakeClient()
        self.is_running = True
        self.shutdown_requested = False
        self._settings = get_self_settings(data_dir, self.phone)

    def get_data(self):
        return dict(self._settings)

    def put_data(self, values):
        self._settings = dict(values)


class FakeIncomingEvent:
    def __init__(
        self,
        *,
        message_id=11,
        chat_id=22,
        text="سلام",
        media=None,
        photo=None,
        document=None,
        file_info=None,
    ):
        self.id = message_id
        self.chat_id = chat_id
        self.sender_id = 33
        self.raw_text = text
        self.is_private = True
        self.is_group = False
        self.is_channel = False
        self.message = SimpleNamespace(
            media=media,
            photo=photo,
            document=document,
            video_note=None,
            video=None,
            gif=None,
            voice=None,
            audio=None,
            sticker=None,
            poll=None,
            contact=None,
            venue=None,
            geo=None,
            web_preview=None,
            file=file_info,
        )

    async def get_sender(self):
        return SimpleNamespace(
            first_name="کاربر",
            last_name="آزمایشی",
            username="tester",
        )

    async def get_chat(self):
        return SimpleNamespace(
            first_name="گفتگوی",
            last_name="آزمایشی",
            username="tester",
        )


class AntiDeleteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name)
        self.account = FakeAccount(self.data_dir)
        self.engine = FeatureEngine(self.account)

    def tearDown(self):
        self.temp.cleanup()

    async def test_text_is_archived_then_sent_after_delete(self):
        event = FakeIncomingEvent()
        await self.engine.cache_incoming_for_anti_delete(event)
        self.assertEqual(
            count_archived_messages(self.data_dir, self.account.phone),
            1,
        )

        deleted = SimpleNamespace(chat_id=22, deleted_ids=[11])
        await self.engine.handle_deleted_messages(deleted)

        self.assertEqual(len(self.account.client.sent_messages), 1)
        self.assertIn(
            "سلام",
            self.account.client.sent_messages[0][1],
        )
        self.assertEqual(
            count_archived_messages(self.data_dir, self.account.phone),
            0,
        )

    async def test_ordinary_media_is_saved_and_removed_after_transfer(self):
        file_info = SimpleNamespace(
            ext=".mp4",
            name="clip.mp4",
            size=14,
        )
        event = FakeIncomingEvent(
            message_id=12,
            text="تبلیغ",
            document=object(),
            file_info=file_info,
        )
        await self.engine.cache_incoming_for_anti_delete(event)
        row = get_archived_messages(
            self.data_dir,
            self.account.phone,
            chat_id=22,
            message_id=12,
        )[0]
        cached_file = Path(row["media_path"])
        self.assertTrue(cached_file.is_file())

        await self.engine.handle_deleted_messages(
            SimpleNamespace(chat_id=22, deleted_ids=[12])
        )
        self.assertEqual(len(self.account.client.sent_files), 1)
        self.assertFalse(cached_file.exists())

    async def test_timed_media_is_not_added_to_ordinary_archive(self):
        event = FakeIncomingEvent(
            message_id=13,
            media=SimpleNamespace(ttl_seconds=10),
            photo=object(),
            file_info=SimpleNamespace(ext=".jpg", name="", size=100),
        )
        await self.engine.cache_incoming_for_anti_delete(event)
        self.assertEqual(
            count_archived_messages(self.data_dir, self.account.phone),
            0,
        )

    async def test_sensitive_telegram_message_is_not_archived(self):
        event = FakeIncomingEvent(message_id=14, text="Login code: 12345")
        event.sender_id = 777000
        await self.engine.cache_incoming_for_anti_delete(event)
        self.assertEqual(
            count_archived_messages(self.data_dir, self.account.phone),
            0,
        )

    async def test_self_moderation_delete_is_ignored(self):
        event = FakeIncomingEvent(message_id=15)
        await self.engine.cache_incoming_for_anti_delete(event)
        self.engine.mark_own_deletion(event)
        await self.engine.handle_deleted_messages(
            SimpleNamespace(chat_id=22, deleted_ids=[15])
        )
        self.assertEqual(len(self.account.client.sent_messages), 0)
        self.assertEqual(
            count_archived_messages(self.data_dir, self.account.phone),
            0,
        )


class MigrationTests(unittest.TestCase):
    def test_old_database_keeps_settings_and_receives_archive_schema(self):
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
            self.assertEqual(settings["anti_delete_enabled"], "on")
            archive_message(
                data_dir,
                "+989120000001",
                chat_id=1,
                message_id=2,
                message_text="ok",
            )
            self.assertTrue(
                remove_archived_message(
                    data_dir,
                    "+989120000001",
                    chat_id=1,
                    message_id=2,
                )
            )


if __name__ == "__main__":
    unittest.main()

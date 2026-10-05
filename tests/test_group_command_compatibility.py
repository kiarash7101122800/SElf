import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from self_features import FeatureEngine


class FakeAccount:
    def __init__(self, data_dir):
        self.client = SimpleNamespace()
        self.phone = "+989120000000"
        self.owner_id = 101
        self.account_manager_data_dir = data_dir
        self.users_db_path = Path(data_dir) / "users.db"
        self.last_activity = 0
        self._settings = {}

    def get_data(self):
        return dict(self._settings)

    def put_data(self, values):
        self._settings = dict(values)


class FakeGroupCommandEvent:
    def __init__(self, raw_text):
        self.raw_text = raw_text
        self.sender_id = -1001234567890
        self.chat_id = -1001234567890
        self.id = 55
        self.is_reply = True
        self.is_private = False
        self.is_group = True
        self.out = True
        self.via_bot_id = None
        self.edits = []
        self.replies = []
        self.message = SimpleNamespace(
            from_scheduled=False,
            reply_to_msg_id=50,
        )

    async def edit(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))


class GroupCommandCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.account = FakeAccount(self.temp_dir.name)
        self.engine = FeatureEngine(self.account)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_documented_media_commands_route_without_a_dot(self):
        cases = [
            (
                "دانلود",
                "download_replied_media",
                lambda event: (event,),
            ),
            (
                "مخفی",
                "archive_chat",
                lambda event: (event,),
            ),
            (
                "نمایش",
                "archive_chat",
                lambda event: (event,),
            ),
            (
                "لوگو",
                "watermark_replied_photo",
                lambda event: (event,),
            ),
            (
                "ترجمه en سلام",
                "translate_command",
                lambda event: (event, "en", "سلام"),
            ),
            (
                "ویس زن سلام",
                "tts_command",
                lambda event: (event, "زن", "سلام"),
            ),
            (
                "ویس ذخیره خنده",
                "save_voice",
                lambda event: (event, "خنده"),
            ),
            (
                "ویس سرچ خنده",
                "search_voice",
                lambda event: (event, "خنده"),
            ),
            (
                "ویس حذف 12",
                "delete_voice",
                lambda event: (event, 12),
            ),
            (
                "آهنگ test",
                "song_search",
                lambda event: (event, "test"),
            ),
            (
                "تایپ سلام",
                "typing_animation",
                lambda event: (event, "سلام"),
            ),
            (
                "شمارش 5",
                "countdown",
                lambda event: (event, 5),
            ),
            (
                "قیمت btc",
                "crypto_price",
                lambda event: (event, "btc"),
            ),
            (
                "ارز USD EUR",
                "currency_rate",
                lambda event: (event, "USD", "EUR"),
            ),
            (
                "اسکرین https://example.com",
                "web_screenshot",
                lambda event: (event, "https://example.com"),
            ),
        ]

        for raw, method_name, expected_args in cases:
            with self.subTest(raw=raw):
                event = FakeGroupCommandEvent(raw)
                mocked = AsyncMock()
                setattr(self.engine, method_name, mocked)

                handled = await self.engine.handle_command(event)

                self.assertTrue(handled)
                if raw == "مخفی":
                    mocked.assert_awaited_once_with(event, folder_id=1)
                elif raw == "نمایش":
                    mocked.assert_awaited_once_with(event, folder_id=0)
                else:
                    mocked.assert_awaited_once_with(*expected_args(event))

    async def test_documented_person_and_group_commands_route_without_a_dot(self):
        cases = [
            ("ری‌اکت ❤️", "manual_reaction", lambda event: (event, "❤️")),
            ("سکوت 10", "mute_target", lambda event: (event, 10)),
            ("رفع سکوت", "unmute_target", lambda event: (event,)),
            (
                "بلاک",
                "block_target",
                lambda event: (event,),
            ),
            (
                "آنبلاک",
                "block_target",
                lambda event: (event,),
            ),
            (
                "پروفایل افزودن 202",
                "track_profile",
                lambda event: (event, "202"),
            ),
            (
                "پروفایل حذف 202",
                "untrack_profile",
                lambda event: (event, "202"),
            ),
        ]

        for raw, method_name, expected_args in cases:
            with self.subTest(raw=raw):
                event = FakeGroupCommandEvent(raw)
                mocked = AsyncMock()
                setattr(self.engine, method_name, mocked)

                handled = await self.engine.handle_command(event)

                self.assertTrue(handled)
                if raw == "بلاک":
                    mocked.assert_awaited_once_with(event, blocked=True)
                elif raw == "آنبلاک":
                    mocked.assert_awaited_once_with(event, blocked=False)
                else:
                    mocked.assert_awaited_once_with(*expected_args(event))

    async def test_documented_setting_commands_work_without_a_dot(self):
        commands = [
            "ضدحذف روشن",
            "ضدحذف گروه روشن",
            "محبت دوست روشن",
            "حالت متن بولد",
            "امضا روشن",
            "متن امضا ساخته‌شده با پنل",
            "عضویت اجباری روشن",
            "سین گروه روشن",
            "ری‌اکت خودکار روشن ❤️",
            "روابط روشن",
            "قفل ویس روشن",
            "فیلتر روشن",
            "فیلتر افزودن تبلیغ|حذف",
            "متن لوگو GardTeam",
            "حساب 2+2*3",
            "پروفایل روشن",
            "کامنت اول روشن",
        ]

        for raw in commands:
            with self.subTest(raw=raw):
                event = FakeGroupCommandEvent(raw)
                handled = await self.engine.handle_command(event)
                self.assertTrue(handled)
                self.assertTrue(event.edits or event.replies)


if __name__ == "__main__":
    unittest.main()

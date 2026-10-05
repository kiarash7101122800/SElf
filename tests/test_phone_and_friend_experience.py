import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from control_store import (
    add_friend_affection_reply,
    delete_friend_affection_reply,
    list_friend_affection_replies,
    list_friends,
)
from helper_bot import HelperPanelBot
from main_bot import GET_CODE, GET_PHONE, TelegramAuthBot
from self_bot import TelegramAccount
from self_features import FRIEND_AFFECTION_REPLIES, FeatureEngine


class FakeSentMessage:
    def __init__(self, *, edit_error=False):
        self.edits = []
        self.edit_error = edit_error

    async def edit_text(self, text, **kwargs):
        if self.edit_error:
            raise RuntimeError("edit failed")
        self.edits.append((text, kwargs))


class FakePhoneMessage:
    def __init__(
        self,
        user_id,
        *,
        text=None,
        contact=None,
        fail_first_edit=False,
    ):
        self.text = text
        self.contact = contact
        self.from_user = SimpleNamespace(id=user_id)
        self.replies = []
        self.fail_first_edit = fail_first_edit
        self.deleted = False

    async def reply_text(self, text, **kwargs):
        sent = FakeSentMessage(
            edit_error=self.fail_first_edit and not self.replies
        )
        self.replies.append((text, kwargs, sent))
        return sent

    async def delete(self):
        self.deleted = True


class FakeFriendEvent:
    def __init__(
        self,
        *,
        raw_text,
        sender_id=202,
        message_id=10,
        is_private=True,
        is_group=False,
        outgoing=True,
    ):
        self.raw_text = raw_text
        self.sender_id = sender_id
        self.id = message_id
        self.chat_id = 303
        self.is_reply = True
        self.is_private = is_private
        self.is_group = is_group
        self.out = outgoing
        self.via_bot_id = None
        self.edits = []
        self.replies = []
        self.message = SimpleNamespace(from_scheduled=False)
        self.target = SimpleNamespace(
            sender_id=sender_id,
            get_sender=self.get_sender,
        )

    async def get_reply_message(self):
        return self.target

    async def get_sender(self):
        return SimpleNamespace(
            id=self.sender_id,
            first_name="دوست",
            last_name="کاربر",
            bot=False,
        )

    async def edit(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))


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


class PhoneExperienceTests(unittest.IsolatedAsyncioTestCase):
    def test_phone_keyboard_requests_the_users_contact(self):
        keyboard = TelegramAuthBot.create_phone_keyboard()
        contact_button = keyboard.keyboard[0][0]
        self.assertEqual(contact_button.text, "📱 ارسال شماره من")
        self.assertTrue(contact_button.request_contact)

    async def test_own_contact_starts_verification(self):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        bot.user_sessions = {}
        calls = []

        async def send_code(phone_number, user_id):
            calls.append((phone_number, user_id))
            return {
                "success": True,
                "phone_code_hash": "hash",
                "client": SimpleNamespace(),
            }

        bot.send_verification_code = send_code
        message = FakePhoneMessage(
            123,
            contact=SimpleNamespace(
                user_id=123,
                phone_number="+989121234567",
            ),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(user_data={})

        result = await bot.get_phone_number(update, context)

        self.assertEqual(result, GET_CODE)
        self.assertEqual(calls, [("+989121234567", 123)])
        self.assertEqual(
            bot.user_sessions[123]["phone_number"],
            "+989121234567",
        )
        self.assertIn("ورود کد تأیید", message.replies[1][0])

    async def test_code_prompt_is_sent_when_progress_edit_fails(self):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        bot.user_sessions = {}

        async def send_code(phone_number, user_id):
            return {
                "success": True,
                "phone_code_hash": "hash",
                "client": SimpleNamespace(),
            }

        bot.send_verification_code = send_code
        message = FakePhoneMessage(
            123,
            contact=SimpleNamespace(
                user_id=123,
                phone_number="+989121234567",
            ),
            fail_first_edit=True,
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(user_data={})

        result = await bot.get_phone_number(update, context)

        self.assertEqual(result, GET_CODE)
        self.assertIn("ورود کد تأیید", message.replies[1][0])
        self.assertIn(123, bot.user_sessions)

    def test_verification_code_accepts_persian_and_arabic_digits(self):
        self.assertEqual(
            TelegramAuthBot.normalize_verification_code("۱۲ ۳-۴۵"),
            "12345",
        )
        self.assertEqual(
            TelegramAuthBot.normalize_verification_code("١٢٣٤٥"),
            "12345",
        )
        self.assertEqual(
            TelegramAuthBot.normalize_verification_code("1234"),
            "",
        )

    async def test_verification_code_can_be_sent_as_text(self):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        bot.user_sessions = {
            123: {
                "entered_code": "",
                "phone_number": "+989121234567",
                "phone_code_hash": "hash",
                "client": SimpleNamespace(),
            }
        }
        bot.finish_verification_code = AsyncMock(return_value=GET_CODE)
        message = FakePhoneMessage(123, text="۱۲۳۴۵")
        update = SimpleNamespace(
            effective_message=message,
            effective_user=message.from_user,
        )
        context = SimpleNamespace(user_data={})

        result = await bot.verify_code_text(update, context)

        self.assertEqual(result, GET_CODE)
        self.assertTrue(message.deleted)
        self.assertEqual(bot.user_sessions[123]["entered_code"], "12345")
        bot.finish_verification_code.assert_awaited_once()

    async def test_another_users_contact_is_rejected(self):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        bot.user_sessions = {}
        message = FakePhoneMessage(
            123,
            contact=SimpleNamespace(
                user_id=999,
                phone_number="+989121234567",
            ),
        )
        update = SimpleNamespace(message=message)
        context = SimpleNamespace(user_data={})

        result = await bot.get_phone_number(update, context)

        self.assertEqual(result, GET_PHONE)
        self.assertFalse(bot.user_sessions)
        self.assertIn("فقط شماره متعلق", message.replies[0][0])


class FriendExperienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.account = FakeAccount(self.temp_dir.name)
        self.engine = FeatureEngine(self.account)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_tanzim_doost_uses_the_replied_user(self):
        event = FakeFriendEvent(raw_text="تنظیم دوست")

        handled = await self.engine.handle_command(event)

        self.assertTrue(handled)
        self.assertEqual(
            list_friends(
                self.temp_dir.name,
                self.account.phone,
                limit=10,
            ),
            [202],
        )
        self.assertIn("ریپلای می‌شود", event.edits[0][0])

    async def test_friend_gets_one_affectionate_reply_per_message(self):
        event = FakeFriendEvent(raw_text="سلام خوبی؟")

        first = await self.engine.reply_affectionately_to_friend(event)
        second = await self.engine.reply_affectionately_to_friend(event)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(event.replies), 1)
        self.assertIn(event.replies[0][0], FRIEND_AFFECTION_REPLIES)
        self.assertTrue(self.engine.friend_affection_was_sent(event))

    async def test_custom_friend_text_replaces_the_default_pool(self):
        reply_id = add_friend_affection_reply(
            self.temp_dir.name,
            self.account.phone,
            "دوستت دارم عزیز دلم ❤️",
        )
        event = FakeFriendEvent(raw_text="سلام")

        sent = await self.engine.reply_affectionately_to_friend(event)

        self.assertTrue(sent)
        self.assertEqual(event.replies[0][0], "دوستت دارم عزیز دلم ❤️")
        rows = list_friend_affection_replies(
            self.temp_dir.name,
            self.account.phone,
        )
        self.assertEqual(rows[0]["id"], reply_id)
        self.assertTrue(
            delete_friend_affection_reply(
                self.temp_dir.name,
                self.account.phone,
                reply_id,
            )
        )

    async def test_voice_command_works_without_a_dot(self):
        event = FakeFriendEvent(
            raw_text="ویس زن سلام عزیزم",
            is_private=False,
            is_group=True,
        )
        self.engine.tts_command = AsyncMock()

        handled = await self.engine.handle_command(event)

        self.assertTrue(handled)
        self.engine.tts_command.assert_awaited_once_with(
            event,
            "زن",
            "سلام عزیزم",
        )

    def test_persian_feature_commands_accept_dot_slash_or_plain_text(self):
        examples = {
            "ویس مرد سلام": ".ویس مرد سلام",
            "/ویس زن سلام": ".ویس زن سلام",
            "دانلود": ".دانلود",
            "ترجمه en": ".ترجمه en",
            "قفل ویس روشن": ".قفل ویس روشن",
            "فیلتر افزودن تبلیغ|حذف": ".فیلتر افزودن تبلیغ|حذف",
            "محبت دوست خاموش": ".محبت دوست خاموش",
            "قیمت btc": ".قیمت btc",
            "اسکرین https://example.com": ".اسکرین https://example.com",
            "حساب 2+2": ".حساب 2+2",
        }
        for raw, expected in examples.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    self.engine.normalize_command_text(raw),
                    expected,
                )
                self.assertTrue(self.engine.looks_like_command(raw))

    def test_send_as_group_command_is_accepted_but_scheduled_is_not(self):
        account = TelegramAccount.__new__(TelegramAccount)
        account.owner_id = 101
        group_event = FakeFriendEvent(
            raw_text="ویس زن سلام",
            sender_id=-1001234567890,
            is_private=False,
            is_group=True,
            outgoing=True,
        )

        self.assertTrue(account.is_owner_outgoing_event(group_event))

        group_event.message.from_scheduled = True
        self.assertFalse(account.is_owner_outgoing_event(group_event))


class FriendPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.phone = "+989120000000"
        self.panel = HelperPanelBot.__new__(HelperPanelBot)
        self.panel.data_dir = Path(self.temp_dir.name)
        self.panel.users_db = self.panel.data_dir / "users.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_friend_texts_are_visible_and_deletable_from_the_panel(self):
        reply_id = add_friend_affection_reply(
            self.temp_dir.name,
            self.phone,
            "قربونت برم عزیز دلم",
        )

        text, keyboard = self.panel.build_page(
            101,
            {"phone": self.phone, "self_pid": None},
            "friend_replies",
        )

        self.assertIn("قربونت برم عزیز دلم", text)
        callback_values = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn(
            f"hp:101:friendreply.delete.{reply_id}",
            callback_values,
        )


if __name__ == "__main__":
    unittest.main()

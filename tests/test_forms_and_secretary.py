import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from control_store import (
    create_form_template,
    get_form_session,
    get_form_submission_for_message,
    get_self_settings,
    list_form_templates,
    set_self_setting,
)
from helper_bot import HelperPanelBot
from self_bot import TelegramAccount


class FakeMessage:
    def __init__(self, message_id):
        self.id = message_id


class FakeClient:
    def __init__(self):
        self.next_message_id = 500
        self.sent = []
        self.edited = []

    async def send_message(self, target, text, **kwargs):
        self.next_message_id += 1
        message = FakeMessage(self.next_message_id)
        self.sent.append((target, text, kwargs, message))
        return message

    async def edit_message(self, target, message_id, text, **kwargs):
        self.edited.append((target, message_id, text, kwargs))
        return FakeMessage(message_id)


class FakeIncomingEvent:
    def __init__(self, text, *, sender_id=202, chat_id=202):
        self.raw_text = text
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.is_private = True
        self.replies = []
        self._next_message_id = 100

    async def reply(self, text, **kwargs):
        self._next_message_id += 1
        message = FakeMessage(self._next_message_id)
        self.replies.append((text, kwargs, message))
        return message


class FakeStatusEvent:
    def __init__(self, text, reply_message_id, *, chat_id=101):
        self.raw_text = text
        self.chat_id = chat_id
        self.is_reply = True
        self.reply_message_id = reply_message_id
        self.deleted = False

    async def get_reply_message(self):
        return FakeMessage(self.reply_message_id)

    async def delete(self):
        self.deleted = True


class FormStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.phone = "+989120000000"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_form_migration_creation_and_default_switch(self):
        form_id = create_form_template(
            self.temp_dir.name,
            self.phone,
            "سفارش کفش",
            "کفش",
            ["نام؟", "سایز؟"],
        )

        forms = list_form_templates(self.temp_dir.name, self.phone)
        settings = get_self_settings(self.temp_dir.name, self.phone)

        self.assertEqual(forms[0]["id"], form_id)
        self.assertEqual(forms[0]["field_count"], 2)
        self.assertEqual(settings["form_builder_enabled"], "off")

    def test_secretary_matching_accepts_keywords_and_variants(self):
        account = TelegramAccount.__new__(TelegramAccount)
        account.secretary_messages = {
            "قیمت/هزینه/قیمت چنده": "قیمت را می‌فرستم.",
            "ساعت کاری": "هر روز ۹ تا ۱۸",
        }

        self.assertEqual(
            account.find_secretary_response("سلام، هزینه این کار چقدره؟"),
            "قیمت را می‌فرستم.",
        )
        self.assertEqual(
            account.find_secretary_response("ساعت کاری؟"),
            "هر روز ۹ تا ۱۸",
        )


class FormConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.phone = "+989120000000"
        self.form_id = create_form_template(
            self.temp_dir.name,
            self.phone,
            "سفارش کفش",
            "کفش",
            ["نام و نام خانوادگی؟", "سایز کفش؟"],
        )
        set_self_setting(
            self.temp_dir.name,
            self.phone,
            "form_builder_enabled",
            "on",
        )
        self.account = TelegramAccount.__new__(TelegramAccount)
        self.account.phone = self.phone
        self.account.client = FakeClient()
        self.account.form_user_locks = {}
        self.account.get_data = lambda: get_self_settings(
            self.temp_dir.name,
            self.phone,
        )

        import self_bot

        self.original_database_dir = self_bot.DATABASE_DIR
        self_bot.DATABASE_DIR = Path(self.temp_dir.name)

    async def asyncTearDown(self):
        import self_bot

        self_bot.DATABASE_DIR = self.original_database_dir
        self.temp_dir.cleanup()

    async def test_form_runs_confirms_and_admin_can_change_status(self):
        start_event = FakeIncomingEvent("کفش")
        self.assertTrue(
            await self.account.process_form_message(start_event, "کفش")
        )
        self.assertIn("سؤال ۱ از 2", start_event.replies[0][0])

        answer_one = FakeIncomingEvent("علی رضایی")
        self.assertTrue(
            await self.account.process_form_message(
                answer_one,
                "علی رضایی",
            )
        )
        self.assertIn("سؤال 2 از 2", answer_one.replies[0][0])

        answer_two = FakeIncomingEvent("۴۲")
        self.assertTrue(
            await self.account.process_form_message(answer_two, "۴۲")
        )
        self.assertIn("آیا اطلاعات بالا را تأیید می‌کنید", answer_two.replies[0][0])
        session = get_form_session(
            self.temp_dir.name,
            self.phone,
            202,
        )
        self.assertEqual(session["stage"], "confirming")

        confirm = FakeIncomingEvent("تأیید")
        self.assertTrue(
            await self.account.process_form_message(confirm, "تأیید")
        )
        self.assertIn("وضعیت: ⏳ در حال پردازش", confirm.replies[0][0])
        admin_message = self.account.client.sent[0][3]
        submission = get_form_submission_for_message(
            self.temp_dir.name,
            self.phone,
            message_id=admin_message.id,
        )
        self.assertEqual(submission["status"], "processing")

        status_event = FakeStatusEvent("ارسال شده", admin_message.id)
        self.assertTrue(
            await self.account.handle_form_status_command(status_event)
        )
        self.assertTrue(status_event.deleted)
        self.assertEqual(len(self.account.client.edited), 2)
        self.assertTrue(
            any(
                "📦 ارسال شده" in edit[2]
                for edit in self.account.client.edited
            )
        )
        updated = get_form_submission_for_message(
            self.temp_dir.name,
            self.phone,
            message_id=admin_message.id,
        )
        self.assertEqual(updated["status"], "shipped")


class FormPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.phone = "+989120000000"
        self.panel = HelperPanelBot.__new__(HelperPanelBot)
        self.panel.data_dir = Path(self.temp_dir.name)
        self.panel.users_db = self.panel.data_dir / "users.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_forms_are_visible_toggleable_and_deletable(self):
        form_id = create_form_template(
            self.temp_dir.name,
            self.phone,
            "سفارش لباس",
            "لباس",
            ["مدل؟", "سایز؟"],
        )

        text, keyboard = self.panel.build_page(
            101,
            {"phone": self.phone, "self_pid": None},
            "forms",
        )

        self.assertIn("سفارش لباس", text)
        self.assertIn("شروع: «لباس»", text)
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn(f"hp:101:form.toggle.{form_id}", callbacks)
        self.assertIn(f"hp:101:form.delete.{form_id}", callbacks)


if __name__ == "__main__":
    unittest.main()

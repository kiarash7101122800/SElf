import unittest
from types import SimpleNamespace

from main_bot import TelegramAuthBot


class AdminTextContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_post_without_user_data_is_ignored(self):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        update = SimpleNamespace(
            effective_user=None,
            effective_message=SimpleNamespace(text="پیام کانال"),
        )
        context = SimpleNamespace(user_data=None)

        await bot.receive_admin_text(update, context)

    async def test_service_update_without_message_is_ignored(self):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            effective_message=None,
        )
        context = SimpleNamespace(user_data={})

        await bot.receive_admin_text(update, context)


if __name__ == "__main__":
    unittest.main()

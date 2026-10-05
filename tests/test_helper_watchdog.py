import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from main_bot import TelegramAuthBot


class HelperWatchdogTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        bot.helper_process = None
        bot.helper_watchdog_task = None
        bot.helper_operation_lock = asyncio.Lock()
        return bot

    async def test_disabled_helper_is_not_started(self):
        bot = self.make_bot()
        bot.start_helper_process = AsyncMock()

        with patch(
            "main_bot.get_helper_config",
            return_value={
                "enabled": False,
                "token": "token",
                "username": "helper",
            },
        ):
            await bot.reconcile_helper_process()

        bot.start_helper_process.assert_not_awaited()

    async def test_crashed_enabled_helper_is_restarted(self):
        bot = self.make_bot()
        bot.helper_is_running = lambda: False
        bot.start_helper_process = AsyncMock(
            return_value=(True, "آماده")
        )

        with patch(
            "main_bot.get_helper_config",
            return_value={
                "enabled": True,
                "token": "token",
                "username": "helper",
            },
        ):
            await bot.reconcile_helper_process()

        bot.start_helper_process.assert_awaited_once_with(
            wait_for_ready=True,
        )

    async def test_running_helper_is_not_duplicated(self):
        bot = self.make_bot()
        bot.helper_is_running = lambda: True
        bot.start_helper_process = AsyncMock()

        with patch(
            "main_bot.get_helper_config",
            return_value={
                "enabled": True,
                "token": "token",
                "username": "helper",
            },
        ):
            await bot.reconcile_helper_process()

        bot.start_helper_process.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

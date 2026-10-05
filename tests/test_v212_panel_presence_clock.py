import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from telethon import functions, types

from advanced_features import AdvancedFeatureEngine
from helper_bot import HelperPanelBot, fit_photo_caption, render_panel_html
from self_bot import TelegramAccount


class PanelExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.panel = HelperPanelBot.__new__(HelperPanelBot)
        self.panel.data_dir = Path(self.temporary.name)
        self.panel.users_db = self.panel.data_dir / "users.db"
        self.phone = "+989120000000"

    def tearDown(self):
        self.temporary.cleanup()

    def test_commands_render_as_real_copyable_code(self):
        rendered = render_panel_html("اجرا: `قفل پیوی روشن`")
        self.assertEqual(
            "اجرا: <code>قفل پیوی روشن</code>",
            rendered,
        )
        self.assertNotIn("`", rendered)
        self.assertLessEqual(len(fit_photo_caption("x" * 3000)), 1000)

    def test_home_contains_profile_identity_and_options_guide(self):
        text, keyboard = self.panel.build_page(
            123456,
            {
                "phone": self.phone,
                "self_pid": None,
                "panel_first_name": "آزمایشی",
                "panel_last_name": "کاربر",
                "panel_username": "selfmaker",
            },
            "home",
        )
        callbacks = {
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertIn("آیدی سلف‌ساز: `123456`", text)
        self.assertIn("@selfmaker", text)
        self.assertIn("hp:123456:option_guide", callbacks)

    def test_options_explain_warn_before_block(self):
        text, _ = self.panel.build_page(
            123456,
            {"phone": self.phone, "self_pid": None},
            "option_security",
        )
        self.assertIn("هشدار قبل بلاک", text)
        self.assertIn("پیش از بلاک‌کردن", text)


class PresenceDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_detection_reads_telegram_offline_status(self):
        class FakeClient:
            async def __call__(self, request):
                self.request = request
                return SimpleNamespace(
                    users=[
                        SimpleNamespace(
                            status=types.UserStatusOffline(
                                was_online=datetime.now(timezone.utc)
                            )
                        )
                    ]
                )

        account = TelegramAccount(
            "+989120000000",
            "session",
            SimpleNamespace(),
        )
        account.client = FakeClient()
        account.last_owner_activity = time.time() - 600
        account.observed_presence_online = None
        detected = await account.detect_presence_online(
            {
                "online_status": "off",
                "presence_auto_detect": "on",
            }
        )
        self.assertFalse(detected)

    async def test_always_online_takes_precedence(self):
        account = TelegramAccount(
            "+989120000000",
            "session",
            SimpleNamespace(),
        )
        self.assertTrue(
            await account.detect_presence_online(
                {
                    "online_status": "on",
                    "presence_auto_detect": "on",
                }
            )
        )


class ProfileClockTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_clock_updates_once_without_blocking(self):
        class FakeClient:
            def __init__(self):
                self.profile_updates = []

            async def __call__(self, request):
                if isinstance(request, functions.users.GetFullUserRequest):
                    return SimpleNamespace(
                        users=[SimpleNamespace(last_name="خانوادگی")],
                        full_user=SimpleNamespace(about="بیوی اصلی"),
                    )
                if isinstance(
                    request,
                    functions.account.UpdateProfileRequest,
                ):
                    self.profile_updates.append(request)
                    return True
                raise AssertionError(type(request))

        account = TelegramAccount(
            "+989120000000",
            "session",
            SimpleNamespace(),
        )
        account.client = FakeClient()
        account.send_queue = None
        account.get_data = lambda: {
            "timename": "on",
            "timebio": "on",
            "font": "5",
            "timename_font": "5",
            "timebio_font": "5",
            "timename_applied": "off",
            "timebio_applied": "off",
            "original_last_name": "",
            "original_bio": "",
        }
        account.last_time_update = 0

        saved = {}
        with patch(
            "self_bot.set_self_setting",
            side_effect=lambda _data, _phone, key, value: saved.__setitem__(
                key, value
            ),
        ):
            await account.force_time_update()

        self.assertEqual("خانوادگی", saved["original_last_name"])
        self.assertEqual("بیوی اصلی", saved["original_bio"])
        self.assertEqual(1, len(account.client.profile_updates))
        update = account.client.profile_updates[0]
        self.assertRegex(update.last_name, r"^\d{2}:\d{2}$")
        self.assertTrue(update.about.startswith("بیوی اصلی "))

    def test_analog_clock_draws_on_profile_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.jpg"
            target = Path(temporary) / "target.jpg"
            Image.new("RGB", (512, 512), "white").save(source)
            AdvancedFeatureEngine.draw_analog_clock(
                source,
                target,
                datetime(2026, 7, 30, 12, 30, 15),
            )
            self.assertTrue(target.is_file())
            with Image.open(target) as image:
                self.assertNotEqual(
                    image.getpixel((470, 470)),
                    (255, 255, 255),
                )


if __name__ == "__main__":
    unittest.main()

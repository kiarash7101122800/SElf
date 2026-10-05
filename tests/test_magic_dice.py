import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon import types

from self_features import FeatureEngine


class FakeSentDice:
    def __init__(self, value, *, message_id, chat_id):
        self.id = message_id
        self.chat_id = chat_id
        self.media = types.MessageMediaDice(value=value, emoticon="🎲")
        self.delete = AsyncMock()


class FakeAccount:
    def __init__(self, data_dir, sent_values=()):
        self.phone = "+989120000000"
        self.owner_id = 101
        self.account_manager_data_dir = data_dir
        self.users_db_path = Path(data_dir) / "users.db"
        self.last_activity = 0
        self._settings = {}
        self.sent_dice = [
            FakeSentDice(
                value,
                message_id=100 + index,
                chat_id=-1001234567890,
            )
            for index, value in enumerate(sent_values)
        ]
        self.client = SimpleNamespace(
            send_file=AsyncMock(side_effect=self.sent_dice),
            send_message=AsyncMock(),
        )

    def get_data(self):
        return dict(self._settings)

    def put_data(self, values):
        self._settings = dict(values)

    @staticmethod
    def is_owner_outgoing_event(event):
        return bool(event.out)


class FakeEvent:
    def __init__(
        self,
        raw_text="",
        *,
        value=None,
        chat_id=-1001234567890,
        message_id=55,
        reply_to=50,
    ):
        self.raw_text = raw_text
        self.chat_id = chat_id
        self.id = message_id
        self.sender_id = -1001234567890
        self.out = True
        self.is_reply = bool(reply_to)
        self.is_private = False
        self.is_group = True
        self.via_bot_id = None
        self.deleted = False
        self.edits = []
        self.replies = []
        media = (
            types.MessageMediaDice(value=value, emoticon="🎲")
            if value is not None
            else None
        )
        self.message = SimpleNamespace(
            from_scheduled=False,
            media=media,
            reply_to_msg_id=reply_to,
        )

    async def delete(self):
        self.deleted = True

    async def edit(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))


class MagicDiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_arm_command_is_silent_scoped_and_single_use(self):
        account = FakeAccount(self.temp_dir.name)
        engine = FeatureEngine(account)
        command = FakeEvent("تنظیم تاس ۱")

        handled = await engine.handle_command(command)

        self.assertTrue(handled)
        self.assertTrue(command.deleted)
        target, expires_at = engine.magic_dice_targets[command.chat_id]
        self.assertEqual(target, 1)
        self.assertGreater(expires_at, time.monotonic())
        account.client.send_file.assert_not_awaited()

    async def test_next_official_dice_keeps_only_the_selected_result(self):
        account = FakeAccount(self.temp_dir.name, sent_values=(4, 1))
        engine = FeatureEngine(account)
        event = FakeEvent(value=3)
        engine.magic_dice_targets[event.chat_id] = (
            1,
            time.monotonic() + 180,
        )

        with patch("self_features.asyncio.sleep", new=AsyncMock()):
            handled = await engine.handle_magic_dice_outgoing(event)

        self.assertTrue(handled)
        self.assertTrue(event.deleted)
        self.assertNotIn(event.chat_id, engine.magic_dice_targets)
        self.assertEqual(account.client.send_file.await_count, 2)
        first_call = account.client.send_file.await_args_list[0]
        self.assertEqual(first_call.args[0], event.chat_id)
        self.assertEqual(first_call.kwargs["reply_to"], 50)
        self.assertIsInstance(
            first_call.args[1],
            types.InputMediaDice,
        )
        account.sent_dice[0].delete.assert_awaited_once()
        account.sent_dice[1].delete.assert_not_awaited()

    async def test_matching_first_dice_is_left_untouched(self):
        account = FakeAccount(self.temp_dir.name)
        engine = FeatureEngine(account)
        event = FakeEvent(value=6)
        engine.magic_dice_targets[event.chat_id] = (
            6,
            time.monotonic() + 180,
        )

        handled = await engine.handle_magic_dice_outgoing(event)

        self.assertTrue(handled)
        self.assertFalse(event.deleted)
        account.client.send_file.assert_not_awaited()

    async def test_unarmed_dice_never_changes(self):
        account = FakeAccount(self.temp_dir.name)
        engine = FeatureEngine(account)
        event = FakeEvent(value=2)

        handled = await engine.handle_magic_dice_outgoing(event)

        self.assertFalse(handled)
        self.assertFalse(event.deleted)
        account.client.send_file.assert_not_awaited()

    async def test_direct_command_deletes_itself_and_rolls_target(self):
        account = FakeAccount(self.temp_dir.name, sent_values=(2, 5))
        engine = FeatureEngine(account)
        command = FakeEvent("تاس ۵")

        with patch("self_features.asyncio.sleep", new=AsyncMock()):
            handled = await engine.handle_command(command)

        self.assertTrue(handled)
        self.assertTrue(command.deleted)
        self.assertEqual(account.client.send_file.await_count, 2)
        account.sent_dice[0].delete.assert_awaited_once()
        account.sent_dice[1].delete.assert_not_awaited()
        account.client.send_message.assert_not_awaited()

    async def test_cancel_only_clears_the_current_chat(self):
        account = FakeAccount(self.temp_dir.name)
        engine = FeatureEngine(account)
        command = FakeEvent("لغو تاس")
        engine.magic_dice_targets[command.chat_id] = (
            1,
            time.monotonic() + 180,
        )
        engine.magic_dice_targets[-1009999999999] = (
            6,
            time.monotonic() + 180,
        )

        handled = await engine.handle_command(command)

        self.assertTrue(handled)
        self.assertTrue(command.deleted)
        self.assertNotIn(command.chat_id, engine.magic_dice_targets)
        self.assertIn(-1009999999999, engine.magic_dice_targets)


if __name__ == "__main__":
    unittest.main()

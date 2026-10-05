import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon import types

from self_features import FeatureEngine


CHAT_ID = -1001234567890
OWNER_ID = 101
OPPONENT_ID = 202


class FakeSentGame:
    def __init__(self, value, emoticon, *, message_id):
        self.id = message_id
        self.chat_id = CHAT_ID
        self.media = types.MessageMediaDice(
            value=value,
            emoticon=emoticon,
        )
        self.delete = AsyncMock()


class FakeReply:
    def __init__(self, sender_id=OPPONENT_ID, *, bot=False):
        self.sender_id = sender_id
        self._sender = SimpleNamespace(
            id=sender_id,
            first_name="حریف",
            last_name="",
            username="opponent",
            bot=bot,
        )

    async def get_sender(self):
        return self._sender


class FakeAccount:
    def __init__(self, data_dir, sent_games=()):
        self.phone = "+989120000000"
        self.owner_id = OWNER_ID
        self.account_manager_data_dir = data_dir
        self.users_db_path = Path(data_dir) / "users.db"
        self.last_activity = 0
        self._settings = {}
        self.sent_games = [
            FakeSentGame(value, emoticon, message_id=100 + index)
            for index, (value, emoticon) in enumerate(sent_games)
        ]
        self.client = SimpleNamespace(
            send_file=AsyncMock(side_effect=self.sent_games),
            send_message=AsyncMock(),
            get_me=AsyncMock(
                return_value=SimpleNamespace(
                    id=OWNER_ID,
                    first_name="صاحب",
                    last_name="سلف",
                    username="owner",
                )
            ),
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
        sender_id=OWNER_ID,
        out=True,
        value=None,
        emoticon="🎲",
        reply=None,
        message_id=55,
    ):
        self.raw_text = raw_text
        self.chat_id = CHAT_ID
        self.id = message_id
        self.sender_id = sender_id
        self.out = out
        self.is_reply = reply is not None
        self.is_private = False
        self.is_group = True
        self.via_bot_id = None
        self.deleted = False
        self.edits = []
        self.replies = []
        self._reply = reply
        media = (
            types.MessageMediaDice(value=value, emoticon=emoticon)
            if value is not None
            else None
        )
        self.message = SimpleNamespace(
            from_scheduled=False,
            media=media,
            reply_to_msg_id=50 if reply is not None else None,
        )

    async def delete(self):
        self.deleted = True

    async def edit(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def get_reply_message(self):
        return self._reply


class MagicCasinoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_jackpot_arm_is_silent_scoped_and_single_use(self):
        account = FakeAccount(self.temp_dir.name)
        engine = FeatureEngine(account)
        command = FakeEvent("تنظیم کازینو جکپات")

        handled = await engine.handle_command(command)

        self.assertTrue(handled)
        self.assertTrue(command.deleted)
        target, expires_at = engine.magic_casino_targets[CHAT_ID]
        self.assertEqual(target, 64)
        self.assertGreater(expires_at, time.monotonic())
        account.client.send_file.assert_not_awaited()

    async def test_next_slot_keeps_only_selected_official_result(self):
        account = FakeAccount(
            self.temp_dir.name,
            sent_games=((12, "🎰"), (64, "🎰")),
        )
        engine = FeatureEngine(account)
        event = FakeEvent(value=7, emoticon="🎰")
        engine.magic_casino_targets[CHAT_ID] = (
            64,
            time.monotonic() + 180,
        )

        with patch("self_features.asyncio.sleep", new=AsyncMock()):
            handled = await engine.handle_magic_game_outgoing(event)

        self.assertTrue(handled)
        self.assertTrue(event.deleted)
        self.assertNotIn(CHAT_ID, engine.magic_casino_targets)
        self.assertEqual(account.client.send_file.await_count, 2)
        first_media = account.client.send_file.await_args_list[0].args[1]
        self.assertIsInstance(first_media, types.InputMediaDice)
        self.assertEqual(first_media.emoticon, "🎰")
        account.sent_games[0].delete.assert_awaited_once()
        account.sent_games[1].delete.assert_not_awaited()

    async def test_casino_and_dice_arms_are_independent(self):
        account = FakeAccount(self.temp_dir.name)
        engine = FeatureEngine(account)
        engine.magic_dice_targets[CHAT_ID] = (
            2,
            time.monotonic() + 180,
        )
        command = FakeEvent("تنظیم کازینو ۴۴")

        handled = await engine.handle_command(command)

        self.assertTrue(handled)
        self.assertEqual(engine.magic_casino_targets[CHAT_ID][0], 44)
        self.assertEqual(engine.magic_dice_targets[CHAT_ID][0], 2)

    async def test_invalid_casino_result_never_sends(self):
        account = FakeAccount(self.temp_dir.name)
        engine = FeatureEngine(account)
        command = FakeEvent("کازینو ۶۵")

        handled = await engine.handle_command(command)

        self.assertTrue(handled)
        self.assertFalse(command.deleted)
        self.assertIn("۱ تا ۶۴", command.edits[0][0])
        account.client.send_file.assert_not_awaited()

    async def test_plain_casino_command_sends_one_official_slot(self):
        account = FakeAccount(
            self.temp_dir.name,
            sent_games=((23, "🎰"),),
        )
        engine = FeatureEngine(account)
        command = FakeEvent("کازینو")

        handled = await engine.handle_command(command)

        self.assertTrue(handled)
        self.assertTrue(command.deleted)
        account.client.send_file.assert_awaited_once()
        media = account.client.send_file.await_args.args[1]
        self.assertIsInstance(media, types.InputMediaDice)
        self.assertEqual(media.emoticon, "🎰")

    async def test_casual_entertainment_commands(self):
        account = FakeAccount(self.temp_dir.name)
        engine = FeatureEngine(account)

        number = FakeEvent("عدد تصادفی ۱۰ ۱۰")
        self.assertTrue(await engine.handle_command(number))
        self.assertIn("**10**", number.edits[0][0])

        pick = FakeEvent("انتخاب چای | قهوه")
        self.assertTrue(await engine.handle_command(pick))
        self.assertTrue(
            any(item in pick.edits[0][0] for item in ("چای", "قهوه"))
        )

        coin = FakeEvent("شیر یا خط")
        self.assertTrue(await engine.handle_command(coin))
        self.assertIn("شیر یا خط", coin.edits[0][0])

        rps = FakeEvent("سنگ کاغذ قیچی سنگ")
        self.assertTrue(await engine.handle_command(rps))
        self.assertIn("سنگ، کاغذ، قیچی", rps.edits[0][0])


class TicTacToeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def start_game(self):
        account = FakeAccount(self.temp_dir.name)
        engine = FeatureEngine(account)
        command = FakeEvent("دوز", reply=FakeReply())
        handled = await engine.handle_command(command)
        self.assertTrue(handled)
        return account, engine, command

    async def test_start_by_reply_creates_independent_text_game(self):
        account, engine, command = await self.start_game()

        self.assertTrue(command.deleted)
        game = engine.tic_tac_toe_games[CHAT_ID]
        self.assertEqual(game["players"], (OWNER_ID, OPPONENT_ID))
        self.assertEqual(game["turn"], OWNER_ID)
        self.assertEqual(game["board"], [""] * 9)
        text = account.client.send_message.await_args.args[1]
        self.assertIn("بازی دوز مستقل سلف", text)
        self.assertIn("به شرط‌بندی و موجودی متصل نیست", text)

    async def test_players_alternate_and_owner_can_win(self):
        account, engine, _ = await self.start_game()
        moves = (
            (OWNER_ID, 1, True),
            (OPPONENT_ID, 4, False),
            (OWNER_ID, 2, True),
            (OPPONENT_ID, 5, False),
            (OWNER_ID, 3, True),
        )

        for index, (player_id, position, out) in enumerate(moves, start=1):
            event = FakeEvent(
                f"دوز {position}",
                sender_id=player_id,
                out=out,
                message_id=60 + index,
            )
            if out:
                handled = await engine.handle_command(event)
            else:
                handled = await engine.handle_tic_tac_toe_incoming(event)
            self.assertTrue(handled)

        self.assertNotIn(CHAT_ID, engine.tic_tac_toe_games)
        final_text = account.client.send_message.await_args.args[1]
        self.assertIn("برنده شد", final_text)
        self.assertIn("صاحب سلف", final_text)

    async def test_wrong_turn_and_third_person_do_not_change_board(self):
        _, engine, _ = await self.start_game()
        game = engine.tic_tac_toe_games[CHAT_ID]

        wrong_turn = FakeEvent(
            "دوز ۱",
            sender_id=OPPONENT_ID,
            out=False,
        )
        handled = await engine.handle_tic_tac_toe_incoming(wrong_turn)
        self.assertTrue(handled)
        self.assertEqual(game["board"], [""] * 9)
        self.assertIn("هنوز نوبت", wrong_turn.replies[0][0])

        stranger = FakeEvent(
            "دوز ۱",
            sender_id=303,
            out=False,
        )
        handled = await engine.handle_tic_tac_toe_incoming(stranger)
        self.assertFalse(handled)
        self.assertEqual(game["board"], [""] * 9)

    async def test_cancel_only_removes_current_chat_game(self):
        account, engine, _ = await self.start_game()
        other_chat = -1009999999999
        engine.tic_tac_toe_games[other_chat] = dict(
            engine.tic_tac_toe_games[CHAT_ID]
        )
        command = FakeEvent("لغو دوز")

        handled = await engine.handle_command(command)

        self.assertTrue(handled)
        self.assertTrue(command.deleted)
        self.assertNotIn(CHAT_ID, engine.tic_tac_toe_games)
        self.assertIn(other_chat, engine.tic_tac_toe_games)
        self.assertIn(
            "به شرط‌بندی و موجودی متصل نبود",
            account.client.send_message.await_args.args[1],
        )


if __name__ == "__main__":
    unittest.main()

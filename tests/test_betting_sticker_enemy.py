import tempfile
import unittest
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from control_store import (
    add_enemy_hostile_reply,
    delete_enemy_hostile_reply,
    get_financial_config,
    list_enemies,
    list_enemy_hostile_replies,
)
from helper_bot import HelperPanelBot
from main_bot import TelegramAuthBot
from self_features import FeatureEngine


class FakeConversation:
    def __init__(self, quote_message):
        self.request = SimpleNamespace(id=32)
        self.send_message = AsyncMock(return_value=self.request)
        self.get_response = AsyncMock(return_value=quote_message)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeAccount:
    def __init__(self, data_dir):
        self.quotly = SimpleNamespace(id=1031952739, username="QuotLyBot")
        self.forwarded = SimpleNamespace(id=31)
        self.quote_media = object()
        self.quote_message = SimpleNamespace(
            id=33,
            sticker=object(),
            media=self.quote_media,
            document=None,
        )
        self.quote_conversation = FakeConversation(self.quote_message)
        self.client = SimpleNamespace(
            send_message=AsyncMock(),
            get_entity=AsyncMock(return_value=self.quotly),
            forward_messages=AsyncMock(return_value=self.forwarded),
            send_file=AsyncMock(),
            delete_messages=AsyncMock(),
            conversation=Mock(return_value=self.quote_conversation),
        )
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


class FakeEvent:
    def __init__(
        self,
        raw_text,
        *,
        sender_id=202,
        message_id=10,
        outgoing=True,
    ):
        self.raw_text = raw_text
        self.sender_id = sender_id
        self.id = message_id
        self.chat_id = -1001234567890
        self.is_reply = True
        self.is_private = False
        self.is_group = True
        self.out = outgoing
        self.via_bot_id = None
        self.message = SimpleNamespace(from_scheduled=False)
        self.target = SimpleNamespace(
            id=9,
            sender_id=sender_id,
            get_sender=self.get_sender,
        )
        self.edits = []
        self.replies = []
        self.deleted = False

    async def get_reply_message(self):
        return self.target

    async def get_sender(self):
        return SimpleNamespace(
            id=self.sender_id,
            first_name="کاربر",
            last_name="دشمن",
            bot=False,
        )

    async def edit(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def reply(self, text, **kwargs):
        self.replies.append((text, kwargs))

    async def delete(self):
        self.deleted = True


class StickerAndEnemyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.account = FakeAccount(self.temp_dir.name)
        self.engine = FeatureEngine(self.account)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_sticker_is_created_privately_and_reposted_without_bot_trace(self):
        event = FakeEvent("استیکر")

        handled = await self.engine.handle_command(event)

        self.assertTrue(handled)
        self.account.client.send_message.assert_not_awaited()
        self.account.client.get_entity.assert_awaited_once_with("@QuotLyBot")
        self.account.client.forward_messages.assert_awaited_once_with(
            self.account.quotly,
            event.target,
            from_peer=event.chat_id,
        )
        self.account.quote_conversation.send_message.assert_awaited_once_with(
            "/q",
            reply_to=self.account.forwarded.id,
        )
        self.account.client.send_file.assert_awaited_once_with(
            event.chat_id,
            self.account.quote_media,
            reply_to=9,
        )
        self.account.client.delete_messages.assert_awaited_once_with(
            self.account.quotly,
            [31, 32, 33],
            revoke=True,
        )
        self.assertTrue(event.deleted)

    async def test_tanzim_doshman_uses_the_replied_user(self):
        event = FakeEvent("تنظیم دشمن")

        handled = await self.engine.handle_command(event)

        self.assertTrue(handled)
        self.assertEqual(
            list_enemies(
                self.temp_dir.name,
                self.account.phone,
                limit=10,
            ),
            [202],
        )
        self.assertIn("دشمن ثبت شد", event.edits[0][0])

    async def test_enemy_uses_only_the_self_admins_custom_texts(self):
        reply_id = add_enemy_hostile_reply(
            self.temp_dir.name,
            self.account.phone,
            "فعلاً حوصله بحث ندارم.",
        )
        event = FakeEvent("سلام", outgoing=False)

        sent = await self.engine.reply_to_enemy(event)

        self.assertTrue(sent)
        self.assertEqual(event.replies[0][0], "فعلاً حوصله بحث ندارم.")
        rows = list_enemy_hostile_replies(
            self.temp_dir.name,
            self.account.phone,
        )
        self.assertEqual(rows[0]["id"], reply_id)
        self.assertTrue(
            delete_enemy_hostile_reply(
                self.temp_dir.name,
                self.account.phone,
                reply_id,
            )
        )

    async def test_enemy_without_custom_text_does_not_send_a_default_insult(self):
        event = FakeEvent("سلام", outgoing=False)

        sent = await self.engine.reply_to_enemy(event)

        self.assertFalse(sent)
        self.assertEqual(event.replies, [])


class EnemyPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.phone = "+989120000000"
        self.panel = HelperPanelBot.__new__(HelperPanelBot)
        self.panel.data_dir = Path(self.temp_dir.name)
        self.panel.users_db = self.panel.data_dir / "users.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_enemy_texts_are_visible_and_deletable_from_the_panel(self):
        reply_id = add_enemy_hostile_reply(
            self.temp_dir.name,
            self.phone,
            "پیامت را دیدم؛ ادامه نده.",
        )

        text, keyboard = self.panel.build_page(
            101,
            {"phone": self.phone, "self_pid": None},
            "enemy_replies",
        )

        self.assertIn("پیامت را دیدم؛ ادامه نده.", text)
        callback_values = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn(
            f"hp:101:enemyreply.delete.{reply_id}",
            callback_values,
        )


class BettingAndBalanceTests(unittest.TestCase):
    def test_default_betting_fee_preserves_existing_installations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = get_financial_config(Path(temp_dir) / "users.db")
        self.assertEqual(config["betting_fee_percent"], 0)

    def test_game_commission_is_deducted_from_the_complete_pot(self):
        prize, fee = TelegramAuthBot.calculate_game_payout(100, 10)
        self.assertEqual(prize, 180)
        self.assertEqual(fee, 20)

    def test_old_waiting_games_receive_a_zero_fee_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            users_db = Path(temp_dir) / "users.db"
            with sqlite3.connect(users_db) as connection:
                connection.execute(
                    """CREATE TABLE two_player_games (
                           game_id TEXT PRIMARY KEY,
                           creator_id INTEGER NOT NULL,
                           creator_name TEXT NOT NULL,
                           chat_id INTEGER NOT NULL,
                           message_id INTEGER,
                           diamond_amount INTEGER NOT NULL,
                           status TEXT NOT NULL DEFAULT 'waiting',
                           created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                       )"""
                )
                connection.execute(
                    """INSERT INTO two_player_games (
                           game_id, creator_id, creator_name, chat_id,
                           message_id, diamond_amount, status
                       ) VALUES ('old-game', 1, 'old', -100, 55, 10, 'waiting')"""
                )
            bot = TelegramAuthBot.__new__(TelegramAuthBot)
            with patch("main_bot.USERS_DB", users_db):
                bot.init_users_db()
            with sqlite3.connect(users_db) as connection:
                row = connection.execute(
                    """SELECT fee_percent FROM two_player_games
                       WHERE game_id = 'old-game'"""
                ).fetchone()
            self.assertEqual(row, (0,))

    def test_balance_response_has_a_glass_inline_balance_button(self):
        keyboard = TelegramAuthBot.create_balance_keyboard(101, 2500)
        button = keyboard.inline_keyboard[0][0]
        self.assertEqual(button.text, "💰 موجودی: 2,500 سکه")
        self.assertEqual(button.callback_data, "balance_view:101")
        self.assertEqual(button.api_kwargs.get("style"), "success")


if __name__ == "__main__":
    unittest.main()

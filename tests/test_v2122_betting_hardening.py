import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main_bot import TelegramAuthBot


class BettingHardening2122Tests(unittest.TestCase):
    def make_bot(self, db_path: Path):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        bot.user_coins = {}
        bot.invite_links = {}
        bot.user_referrals = {}
        bot.active_games = {}
        bot.game_operation_locks = {}
        bot.owner_id = 999
        bot.last_betting_maintenance_at = 0.0
        with patch("main_bot.USERS_DB", db_path):
            bot.init_users_db()
        return bot

    def test_referral_and_welcome_rewards_are_persistent_and_once_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            bot = self.make_bot(db)
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "INSERT INTO users(user_id, coins, join_date, is_active, welcome_gift_credited) "
                    "VALUES (1, 10, CURRENT_TIMESTAMP, 1, 1)"
                )
                conn.execute(
                    "INSERT INTO users(user_id, coins, join_date, is_active, welcome_gift_credited) "
                    "VALUES (2, 0, CURRENT_TIMESTAMP, 1, 0)"
                )
            bot.user_coins = {1: 10, 2: 0}
            with patch("main_bot.USERS_DB", db), patch(
                "main_bot.get_financial_config",
                return_value={"new_user_gift": 5, "referral_reward": 3},
            ):
                code = bot.get_or_create_invite_code(1)
                self.assertEqual(bot.register_pending_referral(2, code), 1)
                first = bot.credit_pending_onboarding_rewards(2)
                second = bot.credit_pending_onboarding_rewards(2)
                reloaded = self.make_bot(db)
                reloaded.load_referral_cache()

            self.assertEqual(first["gift"], 5)
            self.assertEqual(first["referral_reward"], 3)
            self.assertEqual(second["gift"], 0)
            self.assertEqual(second["referral_reward"], 0)
            with sqlite3.connect(db) as conn:
                balances = dict(conn.execute("SELECT user_id, coins FROM users WHERE user_id IN (1,2)"))
                referral = conn.execute(
                    "SELECT status, reward_amount FROM referrals WHERE referred_user_id = 2"
                ).fetchone()
                tx_count = conn.execute(
                    "SELECT COUNT(*) FROM balance_transactions "
                    "WHERE transaction_type IN ('new_user_gift','referral_reward')"
                ).fetchone()[0]
            self.assertEqual(balances, {1: 13, 2: 5})
            self.assertEqual(referral, ("credited", 3))
            self.assertEqual(tx_count, 2)
            self.assertEqual(reloaded.invite_links[code], 1)
            with patch("main_bot.USERS_DB", db):
                self.assertEqual(reloaded.referral_count(1), 1)

    def test_owner_wallet_is_not_overwritten_on_startup(self):
        import main_bot

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db = root / "data" / "users.db"
            data_dir = root / "data"
            sessions_dir = root / "sessions"
            data_dir.mkdir()
            sessions_dir.mkdir()
            seed = self.make_bot(db)
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "INSERT INTO users(user_id, coins, join_date, is_active) "
                    "VALUES(999, 12345, CURRENT_TIMESTAMP, 1)"
                )
            with patch("main_bot.USERS_DB", db), patch(
                "main_bot.DATA_DIR", data_dir
            ), patch("main_bot.SESSIONS_DIR", sessions_dir), patch.dict(
                "os.environ", {"OWNER_ID": "999"}
            ), patch.object(
                main_bot.TelegramAuthBot, "setup_handlers", lambda self: None
            ):
                live = main_bot.TelegramAuthBot("token", 1, "hash")
            self.assertEqual(live.user_coins[999], 12345)

    def test_rate_limit_is_database_persistent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            bot = self.make_bot(db)
            with patch("main_bot.USERS_DB", db), patch(
                "main_bot.BETTING_RATE_WINDOW_SECONDS", 60
            ):
                self.assertEqual(
                    bot.consume_betting_rate_limit(user_id=7, action="create", limit=2),
                    (True, 0),
                )
                self.assertEqual(
                    bot.consume_betting_rate_limit(user_id=7, action="create", limit=2),
                    (True, 0),
                )
                allowed, retry = bot.consume_betting_rate_limit(
                    user_id=7, action="create", limit=2
                )
            self.assertFalse(allowed)
            self.assertGreater(retry, 0)

    def test_group_allowlist_switches_to_restricted_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            bot = self.make_bot(db)
            with patch("main_bot.USERS_DB", db), patch(
                "main_bot.BETTING_ALLOWED_CHAT_IDS", set()
            ):
                self.assertTrue(bot.betting_chat_allowed(-100))
                with sqlite3.connect(db) as conn:
                    conn.execute(
                        "INSERT INTO betting_allowed_chats(chat_id, is_active) VALUES(-100, 1)"
                    )
                self.assertTrue(bot.betting_chat_allowed(-100))
                self.assertFalse(bot.betting_chat_allowed(-200))
                with sqlite3.connect(db) as conn:
                    conn.execute(
                        "UPDATE betting_allowed_chats SET is_active = 0 WHERE chat_id = -100"
                    )
                self.assertFalse(bot.betting_chat_allowed(-100))
                self.assertTrue(bot.betting_chat_allowed(-200))

    def test_cleanup_uses_status_specific_delivery_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            bot = self.make_bot(db)
            old = "2020-01-01 00:00:00"
            with sqlite3.connect(db) as conn:
                common = "game_id,creator_id,creator_name,chat_id,diamond_amount,status,created_at,updated_at"
                conn.execute(
                    f"INSERT INTO two_player_games({common},result_message_synced,closure_message_synced) "
                    "VALUES('settled-ok',1,'a',-1,10,'settled',?,?,1,0)",
                    (old, old),
                )
                conn.execute(
                    f"INSERT INTO two_player_games({common},result_message_synced,closure_message_synced) "
                    "VALUES('canceled-ok',1,'a',-1,10,'canceled',?,?,0,1)",
                    (old, old),
                )
                conn.execute(
                    f"INSERT INTO two_player_games({common},result_message_synced,closure_message_synced) "
                    "VALUES('settled-pending',1,'a',-1,10,'settled',?,?,0,1)",
                    (old, old),
                )
                conn.execute(
                    '''INSERT INTO system_balance_transactions(
                           account_key, amount, balance_after, transaction_type,
                           note, created_at
                       ) VALUES(
                           'betting_treasury', 1, 1, 'betting_fee_income',
                           'old', ?
                       )''',
                    (old,),
                )
            with patch("main_bot.USERS_DB", db), patch(
                "main_bot.BETTING_HISTORY_RETENTION_DAYS", 30
            ):
                bot.cleanup_betting_history()
            with sqlite3.connect(db) as conn:
                remaining = {
                    row[0] for row in conn.execute("SELECT game_id FROM two_player_games")
                }
            self.assertEqual(remaining, {"settled-pending"})
            with sqlite3.connect(db) as conn:
                treasury_tx = conn.execute(
                    "SELECT COUNT(*) FROM system_balance_transactions"
                ).fetchone()[0]
            self.assertEqual(treasury_tx, 0)


class BettingDelivery2122Tests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, db_path: Path):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        bot.user_coins = {}
        bot.invite_links = {}
        bot.user_referrals = {}
        bot.active_games = {}
        bot.game_operation_locks = {}
        bot.owner_id = 999
        bot.last_betting_maintenance_at = 0.0
        with patch("main_bot.USERS_DB", db_path):
            bot.init_users_db()
        return bot

    @staticmethod
    def insert_settled(db: Path, game_id: str):
        with sqlite3.connect(db) as conn:
            conn.execute(
                '''INSERT INTO two_player_games (
                       game_id, creator_id, creator_name, chat_id, message_id,
                       message_thread_id, diamond_amount, fee_percent, status,
                       participant_id, participant_name, winner_id, winner_name,
                       loser_id, loser_name, prize_amount, fee_amount,
                       creator_balance_after, participant_balance_after, settled_at,
                       result_delivery_state, result_message_synced
                   ) VALUES (?, 1, 'creator', -100, 50, 77, 10, 10, 'settled',
                             2, 'participant', 2, 'participant', 1, 'creator',
                             18, 2, 90, 108, CURRENT_TIMESTAMP, 'pending', 0)''',
                (game_id,),
            )

    async def test_permanent_edit_failure_sends_one_fallback_in_same_topic(self):
        from telegram.error import TelegramError

        class Sent:
            message_id = 99

        class FakeBot:
            def __init__(self):
                self.edits = 0
                self.sends = []

            async def edit_message_text(self, **kwargs):
                self.edits += 1
                raise TelegramError("Message to edit not found")

            async def send_message(self, **kwargs):
                self.sends.append(kwargs)
                return Sent()

        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            bot = self.make_bot(db)
            self.insert_settled(db, "delivery-ok")
            fake = FakeBot()
            with patch("main_bot.USERS_DB", db):
                self.assertTrue(await bot.deliver_game_result(fake, "delivery-ok"))
                self.assertTrue(await bot.deliver_game_result(fake, "delivery-ok"))
            self.assertEqual(fake.edits, 1)
            self.assertEqual(len(fake.sends), 1)
            self.assertEqual(fake.sends[0]["message_thread_id"], 77)
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "SELECT result_message_synced, result_message_id, result_fallback_attempted "
                    "FROM two_player_games WHERE game_id = 'delivery-ok'"
                ).fetchone()
            self.assertEqual(row, (1, 99, 1))

    async def test_ambiguous_fallback_is_not_automatically_sent_twice(self):
        from telegram.error import TelegramError

        class FakeBot:
            def __init__(self):
                self.edits = 0
                self.sends = 0

            async def edit_message_text(self, **kwargs):
                self.edits += 1
                raise TelegramError("Message to edit not found")

            async def send_message(self, **kwargs):
                self.sends += 1
                raise TelegramError("Timed out")

        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            bot = self.make_bot(db)
            self.insert_settled(db, "delivery-ambiguous")
            fake = FakeBot()
            with patch("main_bot.USERS_DB", db):
                self.assertFalse(await bot.deliver_game_result(fake, "delivery-ambiguous"))
                self.assertFalse(await bot.deliver_game_result(fake, "delivery-ambiguous"))
            self.assertEqual(fake.sends, 1)
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "SELECT result_delivery_state, result_fallback_attempted, result_message_synced "
                    "FROM two_player_games WHERE game_id = 'delivery-ambiguous'"
                ).fetchone()
            self.assertEqual(row, ("fallback_ambiguous", 1, 0))

    async def test_temporary_membership_error_is_non_definitive(self):
        from telegram.error import TelegramError

        class Store:
            @staticmethod
            def active_force_join_channels():
                return [{"chat_id": -100, "title": "required"}]

        class ContextBot:
            async def get_chat_member(self, **kwargs):
                raise TelegramError("temporary")

        class Context:
            bot = ContextBot()

        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        bot.owner_id = 999
        bot.admin_store = Store()
        bot.is_admin = lambda user_id: False
        with patch("main_bot.logging.exception"):
            status = await bot.betting_membership_status(Context(), 1)
        self.assertEqual(
            status,
            (
                False,
                "بررسی عضویت ممکن نشد؛ کمی بعد دوباره تلاش کنید.",
                False,
            ),
        )

    async def test_retry_after_releases_fallback_claim_for_safe_retry(self):
        from telegram.error import RetryAfter, TelegramError

        class FakeBot:
            async def edit_message_text(self, **kwargs):
                raise TelegramError("Message to edit not found")

            async def send_message(self, **kwargs):
                raise RetryAfter(4)

        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            bot = self.make_bot(db)
            self.insert_settled(db, "delivery-flood")
            with patch("main_bot.USERS_DB", db):
                self.assertFalse(
                    await bot.deliver_game_result(FakeBot(), "delivery-flood")
                )
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "SELECT result_delivery_state, result_fallback_attempted, "
                    "result_next_retry_at FROM two_player_games "
                    "WHERE game_id = 'delivery-flood'"
                ).fetchone()
            self.assertEqual(row[0], "pending")
            self.assertEqual(row[1], 0)
            self.assertTrue(row[2])

    async def test_stale_fallback_sending_is_marked_ambiguous_after_crash(self):
        class FakeBot:
            async def edit_message_text(self, **kwargs):
                raise AssertionError("stale ambiguous result must not auto-send")

        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            bot = self.make_bot(db)
            self.insert_settled(db, "delivery-crash")
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "UPDATE two_player_games SET "
                    "result_delivery_state = 'fallback_sending', "
                    "result_fallback_attempted = 1, "
                    "result_last_attempt_at = '2020-01-01 00:00:00' "
                    "WHERE game_id = 'delivery-crash'"
                )
            with patch("main_bot.USERS_DB", db):
                await bot.retry_unsynced_game_results(FakeBot())
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "SELECT result_delivery_state, result_next_retry_at "
                    "FROM two_player_games WHERE game_id = 'delivery-crash'"
                ).fetchone()
            self.assertEqual(row, ("fallback_ambiguous", None))

    async def test_transient_result_delivery_keeps_retrying_after_old_limit(self):
        from telegram.error import TelegramError

        class FakeBot:
            def __init__(self):
                self.edits = 0

            async def edit_message_text(self, **kwargs):
                self.edits += 1
                raise TelegramError("temporary network failure")

        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            bot = self.make_bot(db)
            self.insert_settled(db, "delivery-old")
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "UPDATE two_player_games SET result_retry_count = 999, "
                    "result_next_retry_at = CURRENT_TIMESTAMP "
                    "WHERE game_id = 'delivery-old'"
                )
            fake = FakeBot()
            with patch("main_bot.USERS_DB", db), patch(
                "main_bot.BETTING_CLEANUP_BATCH_SIZE", 10
            ):
                await bot.retry_unsynced_game_results(fake)
            self.assertEqual(fake.edits, 1)
            with sqlite3.connect(db) as conn:
                row = conn.execute(
                    "SELECT result_delivery_state, result_retry_count, "
                    "result_next_retry_at FROM two_player_games "
                    "WHERE game_id = 'delivery-old'"
                ).fetchone()
            self.assertEqual(row[0], "pending")
            self.assertEqual(row[1], 1000)
            self.assertTrue(row[2])


if __name__ == "__main__":
    unittest.main()

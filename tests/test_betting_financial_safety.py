import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main_bot import TelegramAuthBot


class BettingFinancialSafetyTests(unittest.TestCase):
    def make_bot(self, db_path: Path):
        bot = TelegramAuthBot.__new__(TelegramAuthBot)
        bot.user_coins = {}
        bot.active_games = {}
        bot.game_operation_locks = {}
        bot.owner_id = 999
        with patch("main_bot.USERS_DB", db_path):
            bot.init_users_db()
        return bot

    def test_small_fee_uses_floor_and_minimum_stake_prevents_unfair_fee(self):
        self.assertEqual(TelegramAuthBot.calculate_game_payout(4, 10), (8, 0))
        self.assertEqual(TelegramAuthBot.minimum_stake_for_fee(10), 5)
        self.assertEqual(TelegramAuthBot.calculate_game_payout(5, 10), (9, 1))
        self.assertEqual(TelegramAuthBot.calculate_game_payout(4, 0), (8, 0))

    def test_legacy_game_table_is_migrated_without_data_loss(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    '''CREATE TABLE two_player_games (
                           game_id TEXT PRIMARY KEY,
                           creator_id INTEGER NOT NULL,
                           creator_name TEXT NOT NULL,
                           chat_id INTEGER NOT NULL,
                           message_id INTEGER,
                           diamond_amount INTEGER NOT NULL,
                           status TEXT NOT NULL DEFAULT 'waiting',
                           created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                       )'''
                )
                conn.execute(
                    '''INSERT INTO two_player_games (
                           game_id, creator_id, creator_name, chat_id,
                           message_id, diamond_amount
                       ) VALUES ('legacy', 1, 'old', -100, 20, 5)'''
                )
            self.make_bot(db)
            with sqlite3.connect(db) as conn:
                columns = {
                    row[1]
                    for row in conn.execute(
                        "PRAGMA table_info(two_player_games)"
                    )
                }
                row = conn.execute(
                    '''SELECT fee_percent, expires_at
                       FROM two_player_games WHERE game_id = 'legacy' '''
                ).fetchone()
            self.assertIn("winner_id", columns)
            self.assertIn("result_message_synced", columns)
            self.assertIn("message_thread_id", columns)
            self.assertIn("closure_message_synced", columns)
            self.assertEqual(row[0], 0)
            self.assertTrue(row[1])

    def test_settlement_is_atomic_audited_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db = Path(temp_dir) / "users.db"
            bot = self.make_bot(db)
            with sqlite3.connect(db) as conn:
                for user_id, coins in ((1, 100), (2, 100), (999, 0)):
                    conn.execute(
                        '''INSERT INTO users (
                               user_id, coins, join_date, is_active
                           ) VALUES (?, ?, CURRENT_TIMESTAMP, 1)''',
                        (user_id, coins),
                    )
            bot.user_coins = {1: 100, 2: 100, 999: 0}
            with patch("main_bot.USERS_DB", db):
                reserved = bot._reserve_game_funds(
                    game_id="safe-game",
                    creator_id=1,
                    creator_name="creator",
                    chat_id=-100,
                    diamond_amount=10,
                    fee_percent=10,
                )
                self.assertTrue(reserved["ok"])
                with sqlite3.connect(db) as conn:
                    conn.execute(
                        '''UPDATE two_player_games SET message_id = 55
                           WHERE game_id = 'safe-game' '''
                    )
                first = bot._settle_waiting_game(
                    game_id="safe-game",
                    participant_id=2,
                    participant_name="participant",
                    winner_id=2,
                    winner_name="participant",
                    loser_id=1,
                    loser_name="creator",
                )
                second = bot._settle_waiting_game(
                    game_id="safe-game",
                    participant_id=2,
                    participant_name="participant",
                    winner_id=2,
                    winner_name="participant",
                    loser_id=1,
                    loser_name="creator",
                )
            self.assertTrue(first["ok"])
            self.assertEqual(second, {"ok": False, "reason": "unavailable"})
            with sqlite3.connect(db) as conn:
                balances = dict(
                    conn.execute(
                        '''SELECT user_id, coins FROM users
                           WHERE user_id IN (1, 2, 999)'''
                    )
                )
                treasury = conn.execute(
                    "SELECT balance FROM system_balances "
                    "WHERE account_key = 'betting_treasury'"
                ).fetchone()[0]
                game = conn.execute(
                    '''SELECT status, winner_id, prize_amount, fee_amount
                       FROM two_player_games WHERE game_id = 'safe-game' '''
                ).fetchone()
                transaction_count = conn.execute(
                    '''SELECT COUNT(*) FROM balance_transactions
                       WHERE note LIKE '%safe-game%' '''
                ).fetchone()[0]
            self.assertEqual(balances, {1: 90, 2: 108, 999: 0})
            self.assertEqual(treasury, 2)
            self.assertEqual(sum(balances.values()) + treasury, 200)
            self.assertEqual(game, ("settled", 2, 18, 2))
            self.assertEqual(transaction_count, 3)


if __name__ == "__main__":
    unittest.main()

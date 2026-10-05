import logging
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.error import RetryAfter, TelegramError
from telethon import TelegramClient
from telethon.sessions import StringSession
import asyncio
import time
import secrets
import os
import subprocess
import sys
import sqlite3
from db_utils import connect as db_connect
import re
import json
import hashlib
import psutil
import shutil
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv
from admin_center import AdminCenterStore, CURRENT_RELEASE
from admin_ui import AdminPanelMixin
from session_vault import read_session_file, write_session_file
from control_store import (
    add_admin,
    ensure_app_settings,
    get_admin_ids,
    get_brand_config,
    get_content_config,
    get_financial_config,
    get_force_join_config,
    get_helper_config,
    get_identity_config,
    remove_admin,
    self_database_path,
    set_app_settings,
)
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("BOT_DATA_DIR", BASE_DIR / "data"))
SESSIONS_DIR = Path(os.getenv("BOT_SESSIONS_DIR", BASE_DIR / "sessions"))
USERS_DB = DATA_DIR / "users.db"
SELF_BOT_SCRIPT = BASE_DIR / "self_bot.py"
HELPER_BOT_SCRIPT = BASE_DIR / "helper_bot.py"
HELPER_STATUS_FILE = DATA_DIR / "helper.status.json"
HELPER_START_TIMEOUT = float(os.getenv("HELPER_START_TIMEOUT", "20"))
HELPER_WATCHDOG_INTERVAL = max(
    10,
    int(os.getenv("HELPER_WATCHDOG_INTERVAL", "30")),
)
SELF_WATCHDOG_INTERVAL = max(
    5,
    int(os.getenv("SELF_WATCHDOG_INTERVAL", "30")),
)
SELF_RESTART_MAX_BACKOFF = max(
    SELF_WATCHDOG_INTERVAL,
    int(os.getenv("SELF_RESTART_MAX_BACKOFF", "300")),
)
SELF_RESTART_CONCURRENCY = max(
    1,
    int(os.getenv("SELF_RESTART_CONCURRENCY", "3")),
)
BETTING_GAME_TTL_MINUTES = max(
    1,
    int(os.getenv("BETTING_GAME_TTL_MINUTES", "15")),
)
BETTING_CLEANUP_INTERVAL = max(
    15,
    int(os.getenv("BETTING_CLEANUP_INTERVAL", "60")),
)
BETTING_MAX_STAKE = max(
    1,
    min(int(os.getenv("BETTING_MAX_STAKE", "1000000")), 10**15),
)
BETTING_MAX_OPEN_GAMES_PER_USER = max(
    1,
    int(os.getenv("BETTING_MAX_OPEN_GAMES_PER_USER", "3")),
)
BETTING_RATE_WINDOW_SECONDS = max(
    10,
    int(os.getenv("BETTING_RATE_WINDOW_SECONDS", "60")),
)
BETTING_CREATE_RATE_LIMIT = max(
    1,
    int(os.getenv("BETTING_CREATE_RATE_LIMIT", "3")),
)
BETTING_CANCEL_RATE_LIMIT = max(
    1,
    int(os.getenv("BETTING_CANCEL_RATE_LIMIT", "3")),
)
BETTING_JOIN_RATE_LIMIT = max(
    1,
    int(os.getenv("BETTING_JOIN_RATE_LIMIT", "8")),
)
BETTING_CLEANUP_BATCH_SIZE = max(
    1,
    min(int(os.getenv("BETTING_CLEANUP_BATCH_SIZE", "20")), 100),
)
BETTING_HISTORY_RETENTION_DAYS = max(
    30,
    int(os.getenv("BETTING_HISTORY_RETENTION_DAYS", "365")),
)
BETTING_TRANSACTION_RETENTION_DAYS = max(
    BETTING_HISTORY_RETENTION_DAYS,
    int(os.getenv("BETTING_TRANSACTION_RETENTION_DAYS", "730")),
)
# BETTING_RESULT_MAX_RETRIES is kept as a legacy environment alias.
BETTING_CLOSURE_MAX_RETRIES = max(
    3,
    int(
        os.getenv(
            "BETTING_CLOSURE_MAX_RETRIES",
            os.getenv("BETTING_RESULT_MAX_RETRIES", "20"),
        )
    ),
)
BETTING_ALLOWED_CHAT_IDS = {
    int(value.strip())
    for value in os.getenv("BETTING_ALLOWED_CHAT_IDS", "").split(",")
    if re.fullmatch(r"-?[0-9]+", value.strip())
}
try:
    BOT_TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Tehran"))
except Exception:
    BOT_TIMEZONE = ZoneInfo("UTC")


def current_self_code_hash() -> str:
    digest = hashlib.sha256()
    for source in (
        SELF_BOT_SCRIPT,
        BASE_DIR / "self_features.py",
        BASE_DIR / "advanced_features.py",
        BASE_DIR / "control_store.py",
        BASE_DIR / "session_vault.py",
    ):
        try:
            digest.update(source.name.encode("utf-8"))
            digest.update(source.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()


# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# httpx logs the complete Bot API request URL at INFO level. That URL contains
# the bot token, so keep transport logs at WARNING and above.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# حالت‌های مکالمه
(
    CHECK_MEMBERSHIP,
    ACTIVATION_PANEL,
    GET_PHONE,
    GET_CODE,
    GET_PASSWORD,
    COIN_PURCHASE,
    RECEIPT_UPLOAD,
) = range(7)

FINANCIAL_SETTING_FIELDS = {
    "coin_price_toman": {
        "label": "قیمت هر سکه",
        "prompt": "قیمت هر سکه را به تومان و فقط به‌صورت عدد بفرستید.",
    },
    "payment_card_number": {
        "label": "شماره کارت",
        "prompt": "شماره کارت ۱۶ رقمی را بفرستید.",
    },
    "payment_card_holder": {
        "label": "نام صاحب کارت",
        "prompt": "نام صاحب کارت را بفرستید.",
    },
    "payment_contact_username": {
        "label": "آیدی نمایشی پرداخت",
        "prompt": "نام کاربری نمایشی پرداخت را با یا بدون @ بفرستید.",
    },
    "activation_cost_coins": {
        "label": "هزینه فعال‌سازی",
        "prompt": "تعداد سکه لازم برای فعال‌سازی سلف را فقط به‌صورت عدد بفرستید.",
    },
    "daily_self_cost_coins": {
        "label": "هزینه روزانه سلف",
        "prompt": (
            "هزینه هر ۲۴ ساعت روشن‌بودن سلف را به سکه و فقط عددی "
            "بفرستید. عدد ۰ یعنی برداشت روزانه غیرفعال باشد."
        ),
    },
    "betting_fee_percent": {
        "label": "کارمزد شرط‌بندی",
        "prompt": (
            "درصد کارمزد از مجموع جایزه بازی دونفره را عددی بفرستید.\n"
            "مقدار مجاز ۰ تا ۵۰ است؛ عدد ۰ یعنی بدون کارمزد."
        ),
    },
    "new_user_gift_coins": {
        "label": "هدیه شروع",
        "prompt": "تعداد سکه هدیه شروع کاربران جدید را فقط به‌صورت عدد بفرستید.",
    },
    "referral_reward_coins": {
        "label": "پاداش دعوت",
        "prompt": "تعداد سکه پاداش هر دعوت موفق را فقط به‌صورت عدد بفرستید.",
    },
    "transfer_min_coins": {
        "label": "حداقل انتقال سکه",
        "prompt": "حداقل سکه مجاز در هر انتقال را فقط به‌صورت عدد بفرستید.",
    },
    "transfer_max_coins": {
        "label": "حداکثر هر انتقال",
        "prompt": "حداکثر سکه مجاز در هر انتقال را فقط به‌صورت عدد بفرستید.",
    },
    "transfer_daily_limit_coins": {
        "label": "سقف انتقال روزانه",
        "prompt": (
            "حداکثر مجموع انتقال هر کاربر در ۲۴ ساعت را فقط عددی "
            "بفرستید. عدد ۰ یعنی سقف روزانه نامحدود باشد."
        ),
    },
    "support_url": {
        "label": "لینک پشتیبانی",
        "prompt": (
            "لینک پشتیبانی را به‌شکل https://t.me/username یا @username "
            "بفرستید. برای پاک‌کردن، کلمه «حذف» را بفرستید."
        ),
    },
}

CONTENT_SETTING_FIELDS = {
    "support_url": {
        "label": "لینک پشتیبانی",
        "prompt": (
            "لینک پشتیبانی را به‌شکل https://t.me/username یا "
            "@username بفرستید. برای پاک‌کردن، کلمه «حذف» را بفرستید."
        ),
    },
    "support_text": {
        "label": "متن پشتیبانی",
        "prompt": "متن کامل بخش پشتیبانی را ارسال کنید.",
    },
    "rules_text": {
        "label": "متن قوانین",
        "prompt": "متن کامل قوانین ربات را ارسال کنید.",
    },
}

BRANDING_SETTING_FIELDS = {
    "bot_display_name": {
        "label": "نام ربات",
        "prompt": (
            "نام جدید ربات را ارسال کنید.\n"
            "این نام در منوی استارت ذخیره می‌شود و نام نمایشی رسمی ربات "
            "در تلگرام نیز به‌روزرسانی خواهد شد."
        ),
    },
}

IDENTITY_SETTING_FIELDS = {
    "receipt_admin_ids": {
        "label": "آیدی عددی مدیران رسید",
        "prompt": (
            "آیدی عددی ادمین‌های دریافت‌کننده رسید را با ویرگول بفرستید.\n"
            "نمونه: 123456789,987654321\n\n"
            "برای ارسال رسید به همه ادمین‌های پنل، کلمه «همه» را بفرستید."
        ),
    },
    "payment_contact_username": {
        "label": "آیدی نمایشی پرداخت",
        "prompt": "نام کاربری تلگرام را با یا بدون @ بفرستید.",
    },
    "self_admin_target": {
        "label": "آیدی مدیر سلف",
        "prompt": "آیدی مدیر سلف را به‌صورت @username یا آیدی عددی بفرستید.",
    },
    "self_group_target": {
        "label": "آیدی گروه گزارش",
        "prompt": "آیدی گروه گزارش را به‌صورت @username یا آیدی عددی بفرستید.",
    },
    "self_channel_target": {
        "label": "آیدی کانال سلف",
        "prompt": "آیدی کانال را به‌صورت @username یا آیدی عددی بفرستید.",
    },
    "brand_powered_by_username": {
        "label": "جایگزین Sourcekade",
        "prompt": (
            "متن یا آیدی جدیدی را بفرستید که در تمام بخش‌های "
            "Powered by به‌جای Sourcekade نمایش داده شود.\n"
            "نمونه: @GardTeam یا تیم گارد"
        ),
    },
    "brand_owner_username": {
        "label": "آیدی مالک نمایشی",
        "prompt": "آیدی مالک نمایشی را با یا بدون @ بفرستید.",
    },
    "brand_self_username": {
        "label": "آیدی ربات سلف",
        "prompt": "آیدی ربات سلف را با یا بدون @ بفرستید.",
    },
    "brand_group_username": {
        "label": "آیدی گروه نمایشی",
        "prompt": "آیدی گروه نمایشی را با یا بدون @ بفرستید.",
    },
}


def glass_button(text, *, style=None, **kwargs):
    """Create a modern Telegram inline button with an optional native color."""
    if style not in (None, "primary", "success", "danger"):
        raise ValueError(f"Unsupported Telegram button style: {style}")

    api_kwargs = dict(kwargs.pop("api_kwargs", {}) or {})
    if style:
        # python-telegram-bot 20+ forwards unknown Bot API fields from
        # api_kwargs, so the new native button styles work without changing
        # the rest of the bot or pinning the project to one exact PTB release.
        api_kwargs["style"] = style

    return InlineKeyboardButton(
        text=text,
        api_kwargs=api_kwargs or None,
        **kwargs,
    )


class TelegramAuthBot(AdminPanelMixin):
    def __init__(self, token, api_id, api_hash):
        self.token = token
        self.api_id = int(api_id)
        self.api_hash = str(api_hash)
        self.self_start_timeout = int(os.getenv("SELF_START_TIMEOUT", "45"))
        self.application = (
            Application.builder()
            .token(token)
            .post_init(self.post_init)
            .post_shutdown(self.post_shutdown)
            .build()
        )
        self.user_sessions = {}
        self.user_coins = {}
        self.active_selfbots = {}
        self.self_watchdog_task = None
        self.self_restart_tasks = {}
        self.self_operation_locks = {}
        self.self_billing_lock = asyncio.Lock()
        self.self_restart_semaphore = asyncio.Semaphore(
            SELF_RESTART_CONCURRENCY
        )
        self.helper_process = None
        self.helper_watchdog_task = None
        self.admin_broadcast_task = None
        self.helper_operation_lock = asyncio.Lock()
        self.invite_links = {}
        self.user_referrals = {}
        self.user_first_start = {}
        self.active_games = {}
        self.pending_coin_transfers = {}
        self.game_operation_locks = {}
        self.game_cleanup_task = None
        self.last_betting_maintenance_at = 0.0
        self.owner_id = int(os.getenv("OWNER_ID", "0"))
        if self.owner_id <= 0:
            raise RuntimeError("OWNER_ID باید یک آیدی عددی معتبر باشد.")
        
        # دیتابیس کاربران
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        for private_dir in (DATA_DIR, SESSIONS_DIR):
            try:
                private_dir.chmod(0o700)
            except OSError:
                pass
        self.init_users_db()
        ensure_app_settings(USERS_DB)
        self.users_db = USERS_DB
        self.data_dir = DATA_DIR
        self.sessions_dir = SESSIONS_DIR
        self.current_release = CURRENT_RELEASE
        self.self_code_hash = current_self_code_hash()
        self.admin_store = AdminCenterStore(
            USERS_DB,
            DATA_DIR,
            SESSIONS_DIR,
        )
        legacy_join = get_force_join_config(USERS_DB)
        if (
            legacy_join.get("configured")
            and not self.admin_store.list_force_join_channels()
        ):
            self.admin_store.upsert_force_join_channel(
                legacy_join["chat_id"],
                legacy_join.get("username", ""),
                legacy_join.get("title", "کانال عضویت اجباری"),
                legacy_join["join_url"],
                self.owner_id,
            )
        self.load_user_coins()
        self.recover_activation_reservations()
        self.load_referral_cache()
        self.load_active_games()
        self.setup_handlers()
    
    def init_users_db(self):
        """ایجاد دیتابیس کاربران"""
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("PRAGMA journal_mode = WAL")
            cursor = conn.cursor()
            cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                phone TEXT,
                coins INTEGER DEFAULT 0,
                invited_by INTEGER,
                join_date TEXT,
                is_active INTEGER DEFAULT 1,
                expiration_date TEXT,
                self_pid INTEGER,
                session_file TEXT,
                updated_at TEXT
            )''')

            cursor.execute("PRAGMA table_info(users)")
            columns = {row[1] for row in cursor.fetchall()}
            self_enabled_is_new = "self_enabled" not in columns
            welcome_gift_is_new = "welcome_gift_credited" not in columns
            migrations = {
                "username": "TEXT",
                "first_name": "TEXT",
                "last_name": "TEXT",
                "expiration_date": "TEXT",
                "self_pid": "INTEGER",
                "session_file": "TEXT",
                "updated_at": "TEXT",
                "self_enabled": "INTEGER NOT NULL DEFAULT 0",
                "self_status": "TEXT NOT NULL DEFAULT 'inactive'",
                "self_last_error": "TEXT",
                "self_last_started_at": "TEXT",
                "self_last_stopped_at": "TEXT",
                "self_restart_count": "INTEGER NOT NULL DEFAULT 0",
                "self_consecutive_failures": "INTEGER NOT NULL DEFAULT 0",
                "self_next_restart_at": "TEXT",
                "self_last_billed_at": "TEXT",
                "self_next_billing_at": "TEXT",
                "self_version": "TEXT",
                "self_previous_version": "TEXT",
                "self_code_hash": "TEXT",
                "self_last_updated_at": "TEXT",
                "welcome_gift_credited": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, column_type in migrations.items():
                if column not in columns:
                    cursor.execute(
                        f"ALTER TABLE users ADD COLUMN {column} {column_type}"
                    )
            if self_enabled_is_new:
                cursor.execute(
                    '''UPDATE users
                       SET self_enabled = 1,
                           self_status = 'offline'
                       WHERE phone IS NOT NULL
                         AND TRIM(phone) != ''
                         AND session_file IS NOT NULL
                         AND TRIM(session_file) != '' '''
                )
            if welcome_gift_is_new:
                # Existing accounts must not receive the new-user gift again.
                cursor.execute(
                    "UPDATE users SET welcome_gift_credited = 1"
                )

            # One Telegram phone may belong to only one customer.  Older
            # duplicate rows are disabled before the partial UNIQUE index.
            duplicates = cursor.execute(
                """SELECT phone FROM users
                   WHERE phone IS NOT NULL AND TRIM(phone) != ''
                   GROUP BY phone HAVING COUNT(*) > 1"""
            ).fetchall()
            for (duplicate_phone,) in duplicates:
                rows = cursor.execute(
                    """SELECT user_id FROM users WHERE phone = ?
                       ORDER BY self_enabled DESC,
                                COALESCE(updated_at, join_date, '') DESC,
                                user_id DESC""",
                    (duplicate_phone,),
                ).fetchall()
                keep_id = int(rows[0][0])
                for (duplicate_user_id,) in rows[1:]:
                    cursor.execute(
                        """UPDATE users SET phone = NULL, self_enabled = 0,
                               self_status = 'duplicate_phone', self_pid = NULL,
                               session_file = NULL, updated_at = datetime('now')
                           WHERE user_id = ?""",
                        (int(duplicate_user_id),),
                    )
            cursor.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique
                   ON users(phone)
                   WHERE phone IS NOT NULL AND TRIM(phone) != ''"""
            )
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS activation_reservations (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id INTEGER NOT NULL,
                       phone TEXT NOT NULL,
                       amount INTEGER NOT NULL CHECK(amount >= 0),
                       status TEXT NOT NULL DEFAULT 'pending',
                       balance_after INTEGER NOT NULL,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       completed_at TEXT,
                       error_text TEXT NOT NULL DEFAULT ''
                   )"""
            )
            cursor.execute(
                """CREATE INDEX IF NOT EXISTS idx_activation_reservations_pending
                   ON activation_reservations(status, created_at)"""
            )

            cursor.execute('''CREATE TABLE IF NOT EXISTS self_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                phone TEXT NOT NULL,
                command TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                completed_at TEXT
            )''')
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_self_commands_pending
                   ON self_commands(user_id, phone, status, id)'''
            )
            cursor.execute(
                '''CREATE TABLE IF NOT EXISTS two_player_games (
                       game_id TEXT PRIMARY KEY,
                       creator_id INTEGER NOT NULL,
                       creator_name TEXT NOT NULL,
                       chat_id INTEGER NOT NULL,
                       message_id INTEGER,
                       diamond_amount INTEGER NOT NULL CHECK(diamond_amount > 0),
                       fee_percent INTEGER NOT NULL DEFAULT 0,
                       status TEXT NOT NULL DEFAULT 'waiting',
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )'''
            )
            cursor.execute("PRAGMA table_info(two_player_games)")
            game_columns = {row[1] for row in cursor.fetchall()}
            game_migrations = {
                "fee_percent": "INTEGER NOT NULL DEFAULT 0",
                "participant_id": "INTEGER",
                "participant_name": "TEXT",
                "winner_id": "INTEGER",
                "winner_name": "TEXT",
                "loser_id": "INTEGER",
                "loser_name": "TEXT",
                "prize_amount": "INTEGER",
                "fee_amount": "INTEGER",
                "creator_balance_after": "INTEGER",
                "participant_balance_after": "INTEGER",
                "expires_at": "TEXT",
                "settled_at": "TEXT",
                "canceled_at": "TEXT",
                "expired_at": "TEXT",
                "cancel_reason": "TEXT",
                "result_message_synced": "INTEGER NOT NULL DEFAULT 0",
                "result_message_id": "INTEGER",
                "result_last_attempt_at": "TEXT",
                "result_delivery_error": "TEXT",
                "message_thread_id": "INTEGER",
                "result_delivery_state": "TEXT NOT NULL DEFAULT 'pending'",
                "result_fallback_attempted": "INTEGER NOT NULL DEFAULT 0",
                "result_retry_count": "INTEGER NOT NULL DEFAULT 0",
                "result_next_retry_at": "TEXT",
                "closure_message_synced": "INTEGER NOT NULL DEFAULT 0",
                "closure_retry_count": "INTEGER NOT NULL DEFAULT 0",
                "closure_next_retry_at": "TEXT",
                "closure_last_attempt_at": "TEXT",
                "closure_delivery_error": "TEXT",
                "updated_at": "TEXT",
            }
            for column, column_type in game_migrations.items():
                if column not in game_columns:
                    cursor.execute(
                        f"ALTER TABLE two_player_games "
                        f"ADD COLUMN {column} {column_type}"
                    )
            cursor.execute(
                '''UPDATE two_player_games
                   SET expires_at = datetime(created_at, ?),
                       updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)
                   WHERE status = 'waiting' AND expires_at IS NULL''',
                (f"+{BETTING_GAME_TTL_MINUTES} minutes",),
            )
            cursor.execute(
                '''UPDATE two_player_games
                   SET result_delivery_state = 'synced',
                       result_next_retry_at = NULL
                   WHERE status = 'settled' AND result_message_synced = 1'''
            )
            cursor.execute(
                '''UPDATE two_player_games
                   SET closure_message_synced = 1,
                       closure_next_retry_at = NULL
                   WHERE status IN ('canceled', 'expired', 'failed')
                     AND message_id IS NULL'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_two_player_games_status
                   ON two_player_games(status, created_at)'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_two_player_games_expiry
                   ON two_player_games(status, expires_at)'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_two_player_games_creator
                   ON two_player_games(creator_id, status, created_at DESC)'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_two_player_games_result_delivery
                   ON two_player_games(
                       status, result_message_synced, result_delivery_state,
                       result_next_retry_at
                   )'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_two_player_games_closure_delivery
                   ON two_player_games(
                       status, closure_message_synced, closure_next_retry_at
                   )'''
            )
            cursor.execute(
                '''CREATE TABLE IF NOT EXISTS balance_transactions (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id INTEGER NOT NULL,
                       amount INTEGER NOT NULL,
                       balance_after INTEGER NOT NULL,
                       transaction_type TEXT NOT NULL,
                       admin_id INTEGER,
                       note TEXT,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_balance_transactions_user
                   ON balance_transactions(user_id, created_at DESC)'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_balance_transactions_type_time
                   ON balance_transactions(transaction_type, created_at)'''
            )
            cursor.execute(
                '''CREATE TABLE IF NOT EXISTS system_balances (
                       account_key TEXT PRIMARY KEY,
                       balance INTEGER NOT NULL DEFAULT 0,
                       updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )'''
            )
            cursor.execute(
                '''INSERT OR IGNORE INTO system_balances(account_key, balance)
                   VALUES ('betting_treasury', 0)'''
            )
            cursor.execute(
                '''CREATE TABLE IF NOT EXISTS system_balance_transactions (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       account_key TEXT NOT NULL,
                       amount INTEGER NOT NULL,
                       balance_after INTEGER NOT NULL,
                       transaction_type TEXT NOT NULL,
                       note TEXT,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_system_balance_transactions
                   ON system_balance_transactions(account_key, created_at DESC)'''
            )
            cursor.execute(
                '''CREATE TABLE IF NOT EXISTS invite_codes (
                       code TEXT PRIMARY KEY,
                       owner_id INTEGER NOT NULL UNIQUE,
                       is_active INTEGER NOT NULL DEFAULT 1,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )'''
            )
            cursor.execute(
                '''CREATE TABLE IF NOT EXISTS referrals (
                       referred_user_id INTEGER PRIMARY KEY,
                       referrer_id INTEGER NOT NULL,
                       invite_code TEXT,
                       status TEXT NOT NULL DEFAULT 'pending',
                       reward_amount INTEGER NOT NULL DEFAULT 0,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       credited_at TEXT
                   )'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_referrals_referrer_status
                   ON referrals(referrer_id, status, created_at DESC)'''
            )
            cursor.execute(
                '''INSERT OR IGNORE INTO referrals (
                       referred_user_id, referrer_id, invite_code, status,
                       reward_amount, created_at, credited_at
                   )
                   SELECT user_id, invited_by, '', 'credited', 0,
                          COALESCE(join_date, CURRENT_TIMESTAMP),
                          COALESCE(join_date, CURRENT_TIMESTAMP)
                   FROM users WHERE invited_by IS NOT NULL'''
            )
            cursor.execute(
                '''CREATE TABLE IF NOT EXISTS betting_rate_limits (
                       user_id INTEGER NOT NULL,
                       action TEXT NOT NULL,
                       window_started_at INTEGER NOT NULL,
                       action_count INTEGER NOT NULL DEFAULT 0,
                       PRIMARY KEY(user_id, action)
                   )'''
            )
            cursor.execute(
                '''CREATE TABLE IF NOT EXISTS betting_allowed_chats (
                       chat_id INTEGER PRIMARY KEY,
                       title TEXT,
                       added_by INTEGER,
                       is_active INTEGER NOT NULL DEFAULT 1,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )'''
            )
            cursor.execute(
                '''CREATE TABLE IF NOT EXISTS payment_receipts (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id INTEGER NOT NULL,
                       coin_amount INTEGER NOT NULL CHECK(coin_amount > 0),
                       amount_toman INTEGER NOT NULL CHECK(amount_toman > 0),
                       coin_price_toman INTEGER NOT NULL
                           CHECK(coin_price_toman > 0),
                       telegram_file_id TEXT NOT NULL,
                       telegram_file_unique_id TEXT,
                       file_type TEXT NOT NULL,
                       status TEXT NOT NULL DEFAULT 'pending',
                       admin_id INTEGER,
                       admin_note TEXT,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       reviewed_at TEXT
                   )'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_payment_receipts_status
                   ON payment_receipts(status, created_at DESC, id DESC)'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_payment_receipts_user
                   ON payment_receipts(user_id, created_at DESC, id DESC)'''
            )
            cursor.execute(
                '''CREATE TABLE IF NOT EXISTS custom_start_buttons (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       label TEXT NOT NULL,
                       button_type TEXT NOT NULL
                           CHECK(button_type IN ('url', 'text')),
                       payload TEXT NOT NULL,
                       position INTEGER NOT NULL DEFAULT 0,
                       is_active INTEGER NOT NULL DEFAULT 1,
                       created_by INTEGER NOT NULL,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                   )'''
            )
            cursor.execute(
                '''CREATE INDEX IF NOT EXISTS idx_custom_start_buttons_order
                   ON custom_start_buttons(is_active, position, id)'''
            )
            cursor.execute(
                '''UPDATE self_commands
                   SET status = 'failed',
                       result = ?,
                       completed_at = CURRENT_TIMESTAMP
                   WHERE status IN ('pending', 'running')
                     AND created_at < datetime('now', '-10 minutes')''',
                (json.dumps(
                    {"ok": False, "message": "فرمان منقضی شد"},
                    ensure_ascii=False,
                ),),
            )

        # v2.12.3 no longer duplicates all Telegram sessions in one shared
        # accounts.db.  Per-user encrypted session files remain the only local
        # authentication store.  Remove the legacy aggregate database and its
        # SQLite sidecars after schema initialization.
        for legacy_name in ("accounts.db", "accounts.db-wal", "accounts.db-shm"):
            legacy_path = DATA_DIR / legacy_name
            try:
                legacy_path.unlink(missing_ok=True)
            except OSError:
                logging.warning("Could not remove legacy session store: %s", legacy_path)

        legacy_deleted_dir = DATA_DIR / "deleted_selfbots"
        if legacy_deleted_dir.is_dir():
            try:
                shutil.rmtree(legacy_deleted_dir)
            except OSError as exc:
                logging.warning(
                    "Could not remove legacy deleted-self archives %s: %s",
                    legacy_deleted_dir, exc,
                )

        try:
            USERS_DB.chmod(0o600)
        except OSError:
            pass

    def load_user_coins(self):
        """بازیابی موجودی‌های ذخیره‌شده پس از راه‌اندازی مجدد."""
        with db_connect(USERS_DB) as conn:
            rows = conn.execute("SELECT user_id, coins FROM users").fetchall()
        self.user_coins.clear()
        self.user_coins.update(
            {int(user_id): int(coins or 0) for user_id, coins in rows}
        )

    @staticmethod
    def _upsert_user_coins(conn, user_id: int, coins: int) -> None:
        conn.execute(
            '''INSERT INTO users (
                   user_id, coins, join_date, is_active, updated_at
               )
               VALUES (?, ?, datetime('now'), 1, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                   coins = excluded.coins,
                   updated_at = datetime('now')''',
            (int(user_id), max(0, int(coins))),
        )

    def persist_user_coins(self, *user_ids: int) -> None:
        """ذخیره موجودی حافظه در دیتابیس مرکزی بدون تغییر سایر مشخصات."""
        unique_ids = {int(user_id) for user_id in user_ids}
        if not unique_ids:
            return
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            for user_id in unique_ids:
                self._upsert_user_coins(
                    conn,
                    user_id,
                    self.user_coins.get(user_id, 0),
                )

    def phone_owner(self, phone: str) -> int | None:
        with db_connect(USERS_DB, timeout=10) as conn:
            row = conn.execute(
                """SELECT user_id FROM users
                   WHERE phone = ? LIMIT 1""",
                (str(phone),),
            ).fetchone()
        return int(row[0]) if row else None

    def reserve_activation_cost(
        self, user_id: int, phone: str, amount: int
    ) -> dict:
        user_id = int(user_id)
        amount = max(0, int(amount))
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            owner = conn.execute(
                "SELECT user_id FROM users WHERE phone = ? AND user_id != ?",
                (str(phone), user_id),
            ).fetchone()
            if owner:
                raise ValueError("این شماره قبلاً برای کاربر دیگری ثبت شده است.")
            row = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            balance = int(row[0] or 0) if row else 0
            if balance < amount:
                raise ValueError("موجودی برای فعال‌سازی کافی نیست.")
            new_balance = balance - amount
            self._upsert_user_coins(conn, user_id, new_balance)
            cursor = conn.execute(
                """INSERT INTO activation_reservations
                   (user_id, phone, amount, balance_after)
                   VALUES (?, ?, ?, ?)""",
                (user_id, str(phone), amount, new_balance),
            )
            reservation_id = int(cursor.lastrowid)
            conn.execute(
                """INSERT INTO balance_transactions
                   (user_id, amount, balance_after, transaction_type, note)
                   VALUES (?, ?, ?, 'activation_reserve', ?)""",
                (user_id, -amount, new_balance, f"رزرو فعال‌سازی #{reservation_id}"),
            )
        self.user_coins[user_id] = new_balance
        return {"id": reservation_id, "balance": new_balance, "amount": amount}

    def finish_activation_reservation(
        self, reservation_id: int, *, success: bool, error_text: str = ""
    ) -> int:
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT user_id, amount, status FROM activation_reservations
                   WHERE id = ?""",
                (int(reservation_id),),
            ).fetchone()
            if not row:
                raise LookupError("رزرو فعال‌سازی پیدا نشد.")
            user_id, amount, status = int(row[0]), int(row[1]), str(row[2])
            current_row = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            current_balance = int(current_row[0] or 0) if current_row else 0
            if status != "pending":
                self.user_coins[user_id] = current_balance
                return current_balance
            if success:
                conn.execute(
                    """UPDATE activation_reservations
                       SET status = 'committed', completed_at = CURRENT_TIMESTAMP,
                           error_text = '' WHERE id = ?""",
                    (int(reservation_id),),
                )
                new_balance = current_balance
            else:
                new_balance = current_balance + amount
                self._upsert_user_coins(conn, user_id, new_balance)
                conn.execute(
                    """INSERT INTO balance_transactions
                       (user_id, amount, balance_after, transaction_type, note)
                       VALUES (?, ?, ?, 'activation_refund', ?)""",
                    (user_id, amount, new_balance, f"برگشت رزرو فعال‌سازی #{reservation_id}"),
                )
                conn.execute(
                    """UPDATE activation_reservations
                       SET status = 'refunded', completed_at = CURRENT_TIMESTAMP,
                           error_text = ? WHERE id = ?""",
                    (str(error_text or "")[:500], int(reservation_id)),
                )
        self.user_coins[user_id] = new_balance
        return new_balance

    def recover_activation_reservations(self) -> None:
        """Resolve interrupted activation charges after a main-bot crash."""
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT ar.id, ar.user_id, ar.phone, u.self_pid,
                          u.self_status, u.session_file
                   FROM activation_reservations ar
                   LEFT JOIN users u ON u.user_id = ar.user_id
                   WHERE ar.status = 'pending'
                     AND ar.created_at <= datetime('now', '-2 minutes')"""
            ).fetchall()
        for row in rows:
            pid = int(row["self_pid"] or 0)
            alive = bool(pid and psutil.pid_exists(pid))
            if alive and str(row["session_file"] or ""):
                with db_connect(USERS_DB, timeout=10) as conn:
                    conn.execute(
                        """UPDATE users SET self_enabled = 1,
                               self_status = 'running', updated_at = datetime('now')
                           WHERE user_id = ?""",
                        (int(row["user_id"]),),
                    )
                self.finish_activation_reservation(int(row["id"]), success=True)
            else:
                self.finish_activation_reservation(
                    int(row["id"]), success=False,
                    error_text="بازیابی خودکار رزرو نیمه‌تمام",
                )

    def load_referral_cache(self) -> None:
        """بازیابی لینک‌ها و آمار دعوت از دیتابیس برای سازگاری رابط قدیمی."""
        self.invite_links.clear()
        self.user_referrals.clear()
        with db_connect(USERS_DB, timeout=10) as conn:
            for code, owner_id in conn.execute(
                "SELECT code, owner_id FROM invite_codes WHERE is_active = 1"
            ):
                self.invite_links[str(code)] = int(owner_id)
            for referrer_id, referred_user_id in conn.execute(
                '''SELECT referrer_id, referred_user_id FROM referrals
                   WHERE status = 'credited' '''
            ):
                self.user_referrals.setdefault(int(referrer_id), []).append(
                    int(referred_user_id)
                )

    def referral_count(self, user_id: int) -> int:
        with db_connect(USERS_DB, timeout=10) as conn:
            return int(
                conn.execute(
                    '''SELECT COUNT(*) FROM referrals
                       WHERE referrer_id = ? AND status = 'credited' ''',
                    (int(user_id),),
                ).fetchone()[0]
            )

    def get_or_create_invite_code(self, user_id: int) -> str:
        user_id = int(user_id)
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                '''SELECT code FROM invite_codes
                   WHERE owner_id = ? AND is_active = 1''',
                (user_id,),
            ).fetchone()
            if row:
                code = str(row[0])
            else:
                while True:
                    code = secrets.token_urlsafe(8)
                    try:
                        conn.execute(
                            "INSERT INTO invite_codes(code, owner_id) VALUES (?, ?)",
                            (code, user_id),
                        )
                        break
                    except sqlite3.IntegrityError:
                        continue
        self.invite_links[code] = user_id
        return code

    def register_pending_referral(
        self,
        referred_user_id: int,
        invite_code: str,
    ) -> int | None:
        invite_code = str(invite_code or "").strip()
        if not invite_code:
            return None
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            owner = conn.execute(
                '''SELECT owner_id FROM invite_codes
                   WHERE code = ? AND is_active = 1''',
                (invite_code,),
            ).fetchone()
            if owner is None:
                return None
            referrer_id = int(owner[0])
            referred_user_id = int(referred_user_id)
            if referrer_id == referred_user_id:
                return None
            conn.execute(
                '''INSERT OR IGNORE INTO referrals (
                       referred_user_id, referrer_id, invite_code, status
                   ) VALUES (?, ?, ?, 'pending')''',
                (referred_user_id, referrer_id, invite_code),
            )
            conn.execute(
                '''UPDATE users SET invited_by = COALESCE(invited_by, ?)
                   WHERE user_id = ?''',
                (referrer_id, referred_user_id),
            )
        return referrer_id

    def credit_pending_onboarding_rewards(self, user_id: int) -> dict:
        """واریز یک‌باره هدیه و دعوت، فقط پس از تأیید عضویت اجباری."""
        user_id = int(user_id)
        financial = get_financial_config(USERS_DB)
        result = {
            "gift": 0,
            "user_balance": int(self.user_coins.get(user_id, 0)),
            "referrer_id": None,
            "referral_reward": 0,
            "referrer_balance": None,
        }
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            user_row = conn.execute(
                '''SELECT coins, welcome_gift_credited FROM users
                   WHERE user_id = ?''',
                (user_id,),
            ).fetchone()
            if user_row is None:
                return result
            user_balance = int(user_row[0] or 0)
            gift_credited = bool(user_row[1])
            gift = 0 if self.is_owner(user_id) else max(
                0,
                int(financial["new_user_gift"]),
            )
            if not gift_credited:
                user_balance += gift
                self._upsert_user_coins(conn, user_id, user_balance)
                conn.execute(
                    '''UPDATE users SET welcome_gift_credited = 1
                       WHERE user_id = ?''',
                    (user_id,),
                )
                if gift > 0:
                    self._record_balance_transaction(
                        conn,
                        user_id=user_id,
                        amount=gift,
                        balance_after=user_balance,
                        transaction_type="new_user_gift",
                        note="هدیه شروع پس از تأیید عضویت اجباری",
                    )
                result["gift"] = gift

            referral = conn.execute(
                '''SELECT referrer_id FROM referrals
                   WHERE referred_user_id = ? AND status = 'pending' ''',
                (user_id,),
            ).fetchone()
            if referral is not None:
                referrer_id = int(referral[0])
                reward = max(0, int(financial["referral_reward"]))
                referrer_row = conn.execute(
                    "SELECT coins FROM users WHERE user_id = ?",
                    (referrer_id,),
                ).fetchone()
                if referrer_row is not None:
                    referrer_balance = int(referrer_row[0] or 0) + reward
                    self._upsert_user_coins(conn, referrer_id, referrer_balance)
                    if reward > 0:
                        self._record_balance_transaction(
                            conn,
                            user_id=referrer_id,
                            amount=reward,
                            balance_after=referrer_balance,
                            transaction_type="referral_reward",
                            note=f"پاداش دعوت کاربر {user_id}",
                        )
                    conn.execute(
                        '''UPDATE referrals
                           SET status = 'credited', reward_amount = ?,
                               credited_at = CURRENT_TIMESTAMP
                           WHERE referred_user_id = ? AND status = 'pending' ''',
                        (reward, user_id),
                    )
                    result.update(
                        referrer_id=referrer_id,
                        referral_reward=reward,
                        referrer_balance=referrer_balance,
                    )
        self.user_coins[user_id] = int(user_balance)
        result["user_balance"] = int(user_balance)
        if result["referrer_id"] is not None:
            referrer_id = int(result["referrer_id"])
            self.user_coins[referrer_id] = int(result["referrer_balance"])
            referrals = self.user_referrals.setdefault(referrer_id, [])
            if user_id not in referrals:
                referrals.append(user_id)
        return result

    async def notify_onboarding_rewards(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        rewards: dict,
    ) -> None:
        if int(rewards.get("gift", 0)) > 0:
            try:
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        "🎁 هدیه شروع پس از تأیید عضویت واریز شد.\n"
                        f"💰 مبلغ: {int(rewards['gift']):,} سکه\n"
                        f"💳 موجودی: {int(rewards['user_balance']):,} سکه"
                    ),
                )
            except TelegramError:
                logging.exception("Could not notify welcome gift")
        if rewards.get("referrer_id") is not None:
            try:
                await context.bot.send_message(
                    chat_id=int(rewards["referrer_id"]),
                    text=(
                        "🎉 دعوت شما پس از تأیید عضویت کاربر ثبت شد.\n"
                        f"💰 پاداش: {int(rewards['referral_reward']):,} سکه\n"
                        f"💳 موجودی: {int(rewards['referrer_balance']):,} سکه"
                    ),
                )
            except TelegramError:
                logging.exception("Could not notify referral reward")

    def register_user_profile(self, user) -> None:
        """Store display data without overwriting balance or activation data."""
        if user is None:
            return
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute(
                '''INSERT INTO users (
                       user_id, username, first_name, last_name, coins,
                       join_date, is_active, updated_at
                   )
                   VALUES (?, ?, ?, ?, 0, datetime('now'), 1, datetime('now'))
                   ON CONFLICT(user_id) DO UPDATE SET
                       username = excluded.username,
                       first_name = excluded.first_name,
                       last_name = excluded.last_name,
                       updated_at = datetime('now')''',
                (
                    int(user.id),
                    (user.username or "").strip(),
                    (user.first_name or "").strip(),
                    (user.last_name or "").strip(),
                ),
            )
        self.user_coins.setdefault(int(user.id), 0)

    def gift_user_coins(
        self,
        *,
        user_id: int,
        amount: int,
        admin_id: int,
    ) -> int:
        """Atomically add coins and record the admin action."""
        user_id = int(user_id)
        amount = int(amount)
        if amount <= 0:
            raise ValueError("تعداد سکه هدیه باید بیشتر از صفر باشد.")
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                raise LookupError("کاربر در ربات پیدا نشد.")
            new_balance = int(row[0] or 0) + amount
            self._upsert_user_coins(conn, user_id, new_balance)
            conn.execute(
                '''INSERT INTO balance_transactions (
                       user_id, amount, balance_after, transaction_type,
                       admin_id, note
                   )
                   VALUES (?, ?, ?, 'admin_gift', ?, ?)''',
                (
                    user_id,
                    amount,
                    new_balance,
                    int(admin_id),
                    "اهدای سکه از پنل ادمین",
                ),
            )
        self.user_coins[user_id] = new_balance
        return new_balance

    def transfer_user_coins(
        self,
        *,
        sender_id: int,
        target_id: int,
        amount: int,
    ) -> tuple[int, int, int]:
        """Atomically transfer coins while enforcing panel-defined limits."""
        sender_id = int(sender_id)
        target_id = int(target_id)
        amount = int(amount)
        if sender_id == target_id:
            raise ValueError("نمی‌توانید به خودتان سکه انتقال دهید.")

        config = get_financial_config(USERS_DB)
        minimum = int(config["transfer_min"])
        maximum = max(minimum, int(config["transfer_max"]))
        daily_limit = int(config["transfer_daily_limit"])
        if amount < minimum:
            raise ValueError(
                f"حداقل مقدار هر انتقال {minimum} سکه است."
            )
        if amount > maximum:
            raise ValueError(
                f"حداکثر مقدار هر انتقال {maximum} سکه است."
            )

        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            sender = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?",
                (sender_id,),
            ).fetchone()
            target = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?",
                (target_id,),
            ).fetchone()
            if sender is None:
                raise LookupError("حساب فرستنده در ربات پیدا نشد.")
            if target is None:
                raise LookupError(
                    "کاربر مقصد هنوز ربات را Start نکرده است."
                )
            sender_balance = int(sender[0] or 0)
            target_balance = int(target[0] or 0)
            if sender_balance < amount:
                raise ValueError(
                    f"موجودی کافی نیست؛ موجودی فعلی {sender_balance} سکه است."
                )

            used_today = int(
                conn.execute(
                    '''SELECT COALESCE(-SUM(amount), 0)
                       FROM balance_transactions
                       WHERE user_id = ?
                         AND transaction_type = 'user_transfer_out'
                         AND created_at >= datetime('now', '-1 day')''',
                    (sender_id,),
                ).fetchone()[0]
                or 0
            )
            if daily_limit > 0 and used_today + amount > daily_limit:
                remaining = max(0, daily_limit - used_today)
                raise ValueError(
                    f"سقف انتقال ۲۴ ساعته {daily_limit} سکه است؛ "
                    f"سهم باقی‌مانده شما {remaining} سکه است."
                )

            sender_after = sender_balance - amount
            target_after = target_balance + amount
            self._upsert_user_coins(conn, sender_id, sender_after)
            self._upsert_user_coins(conn, target_id, target_after)
            conn.execute(
                '''INSERT INTO balance_transactions (
                       user_id, amount, balance_after, transaction_type, note
                   )
                   VALUES (?, ?, ?, 'user_transfer_out', ?)''',
                (
                    sender_id,
                    -amount,
                    sender_after,
                    f"انتقال به کاربر {target_id}",
                ),
            )
            conn.execute(
                '''INSERT INTO balance_transactions (
                       user_id, amount, balance_after, transaction_type, note
                   )
                   VALUES (?, ?, ?, 'user_transfer_in', ?)''',
                (
                    target_id,
                    amount,
                    target_after,
                    f"دریافت از کاربر {sender_id}",
                ),
            )

        self.user_coins[sender_id] = sender_after
        self.user_coins[target_id] = target_after
        return sender_after, target_after, used_today + amount

    def create_payment_receipt(
        self,
        *,
        user_id: int,
        coin_amount: int,
        amount_toman: int,
        coin_price_toman: int,
        file_id: str,
        file_unique_id: str,
        file_type: str,
    ) -> int:
        """Store one pending receipt and reject accidental duplicate uploads."""
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            if file_unique_id:
                duplicate = conn.execute(
                    '''SELECT id, status
                       FROM payment_receipts
                       WHERE telegram_file_unique_id = ?
                       ORDER BY id DESC
                       LIMIT 1''',
                    (file_unique_id,),
                ).fetchone()
                if duplicate:
                    raise ValueError(
                        f"این رسید قبلاً با شماره #{int(duplicate[0])} "
                        "ثبت شده است."
                    )
            cursor = conn.execute(
                '''INSERT INTO payment_receipts (
                       user_id, coin_amount, amount_toman, coin_price_toman,
                       telegram_file_id, telegram_file_unique_id, file_type
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (
                    int(user_id),
                    int(coin_amount),
                    int(amount_toman),
                    int(coin_price_toman),
                    str(file_id),
                    str(file_unique_id or ""),
                    str(file_type),
                ),
            )
            return int(cursor.lastrowid)

    def get_payment_receipt(self, receipt_id: int):
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                '''SELECT r.*, u.username, u.first_name, u.last_name
                   FROM payment_receipts AS r
                   LEFT JOIN users AS u ON u.user_id = r.user_id
                   WHERE r.id = ?''',
                (int(receipt_id),),
            ).fetchone()

    def review_payment_receipt(
        self,
        *,
        receipt_id: int,
        admin_id: int,
        approve: bool,
    ) -> tuple[str, int | None, int]:
        """Approve/reject once; coin credit and status update share one lock."""
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            receipt = conn.execute(
                '''SELECT user_id, coin_amount, status
                   FROM payment_receipts
                   WHERE id = ?''',
                (int(receipt_id),),
            ).fetchone()
            if receipt is None:
                raise LookupError("رسید پیدا نشد.")
            user_id, coin_amount, current_status = receipt
            user_id = int(user_id)
            coin_amount = int(coin_amount)
            if current_status != "pending":
                return f"already:{current_status}", None, user_id

            new_status = "approved" if approve else "rejected"
            if not approve:
                conn.execute(
                    '''UPDATE payment_receipts
                       SET status = 'rejected', admin_id = ?,
                           reviewed_at = CURRENT_TIMESTAMP
                       WHERE id = ? AND status = 'pending' ''',
                    (int(admin_id), int(receipt_id)),
                )
                return new_status, None, user_id

            balance_row = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if balance_row is None:
                raise LookupError("کاربر رسید در دیتابیس پیدا نشد.")
            new_balance = int(balance_row[0] or 0) + coin_amount
            self._upsert_user_coins(conn, user_id, new_balance)
            conn.execute(
                '''UPDATE payment_receipts
                   SET status = 'approved', admin_id = ?,
                       reviewed_at = CURRENT_TIMESTAMP
                   WHERE id = ? AND status = 'pending' ''',
                (int(admin_id), int(receipt_id)),
            )
            conn.execute(
                '''INSERT INTO balance_transactions (
                       user_id, amount, balance_after, transaction_type,
                       admin_id, note
                   )
                   VALUES (?, ?, ?, 'receipt_purchase', ?, ?)''',
                (
                    user_id,
                    coin_amount,
                    new_balance,
                    int(admin_id),
                    f"تأیید رسید #{int(receipt_id)}",
                ),
            )
        self.user_coins[user_id] = new_balance
        return new_status, new_balance, user_id

    def receipt_admin_ids(self) -> list[int]:
        configured = get_identity_config(USERS_DB)["receipt_admin_ids"]
        admin_ids = get_admin_ids(USERS_DB, self.owner_id)
        selected = [item for item in configured if item in admin_ids]
        return selected or sorted(admin_ids)

    def list_custom_start_buttons(self, *, active_only: bool = False):
        where = "WHERE is_active = 1" if active_only else ""
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                f'''SELECT id, label, button_type, payload, position, is_active
                    FROM custom_start_buttons
                    {where}
                    ORDER BY position, id'''
            ).fetchall()

    def get_custom_start_button(self, button_id: int):
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                '''SELECT id, label, button_type, payload, position, is_active
                   FROM custom_start_buttons
                   WHERE id = ?''',
                (int(button_id),),
            ).fetchone()

    def create_custom_start_button(
        self,
        *,
        label: str,
        button_type: str,
        payload: str,
        created_by: int,
    ) -> int:
        if button_type not in {"url", "text"}:
            raise ValueError("نوع دکمه معتبر نیست.")
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            next_position = int(
                conn.execute(
                    "SELECT COALESCE(MAX(position), 0) + 1 "
                    "FROM custom_start_buttons"
                ).fetchone()[0]
            )
            cursor = conn.execute(
                '''INSERT INTO custom_start_buttons (
                       label, button_type, payload, position, created_by
                   )
                   VALUES (?, ?, ?, ?, ?)''',
                (
                    str(label),
                    str(button_type),
                    str(payload),
                    next_position,
                    int(created_by),
                ),
            )
            return int(cursor.lastrowid)

    def toggle_custom_start_button(self, button_id: int) -> bool:
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT is_active FROM custom_start_buttons WHERE id = ?",
                (int(button_id),),
            ).fetchone()
            if row is None:
                raise LookupError("دکمه پیدا نشد.")
            new_state = 0 if int(row[0]) else 1
            conn.execute(
                '''UPDATE custom_start_buttons
                   SET is_active = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?''',
                (new_state, int(button_id)),
            )
        return bool(new_state)

    def delete_custom_start_button(self, button_id: int) -> None:
        with db_connect(USERS_DB, timeout=10) as conn:
            cursor = conn.execute(
                "DELETE FROM custom_start_buttons WHERE id = ?",
                (int(button_id),),
            )
            if cursor.rowcount != 1:
                raise LookupError("دکمه پیدا نشد.")

    def pending_receipt_count(self) -> int:
        with db_connect(USERS_DB, timeout=10) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM payment_receipts WHERE status = 'pending'"
                ).fetchone()[0]
            )

    def user_receipt_history(self, user_id: int, limit: int = 8):
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                '''SELECT id, coin_amount, amount_toman, status, created_at
                   FROM payment_receipts
                   WHERE user_id = ?
                   ORDER BY id DESC
                   LIMIT ?''',
                (int(user_id), int(limit)),
            ).fetchall()

    def user_pending_receipt_count(self, user_id: int) -> int:
        with db_connect(USERS_DB, timeout=10) as conn:
            return int(
                conn.execute(
                    '''SELECT COUNT(*)
                       FROM payment_receipts
                       WHERE user_id = ? AND status = 'pending' ''',
                    (int(user_id),),
                ).fetchone()[0]
            )

    def load_active_games(self) -> None:
        # Restore waiting games and refund unpublished reservations safely.
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")

            orphaned = conn.execute(
                '''SELECT game_id, creator_id, diamond_amount
                   FROM two_player_games
                   WHERE status = 'waiting' AND message_id IS NULL'''
            ).fetchall()
            for game_id, creator_id, diamond_amount in orphaned:
                creator_id = int(creator_id)
                row = conn.execute(
                    "SELECT coins FROM users WHERE user_id = ?",
                    (creator_id,),
                ).fetchone()
                current_balance = int(row[0] or 0) if row else 0
                refunded_balance = current_balance + int(diamond_amount)
                self._upsert_user_coins(conn, creator_id, refunded_balance)
                self._record_balance_transaction(
                    conn,
                    user_id=creator_id,
                    amount=int(diamond_amount),
                    balance_after=refunded_balance,
                    transaction_type="betting_stake_refund",
                    note=f"بازگشت رزرو بازی منتشرنشده {game_id}",
                )
                conn.execute(
                    '''UPDATE two_player_games
                       SET status = 'failed',
                           canceled_at = CURRENT_TIMESTAMP,
                           cancel_reason = 'publish_interrupted',
                           creator_balance_after = ?,
                           closure_message_synced = 1,
                           closure_next_retry_at = NULL,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE game_id = ? AND status = 'waiting' ''',
                    (refunded_balance, game_id),
                )
                self.user_coins[creator_id] = refunded_balance

            rows = conn.execute(
                '''SELECT game_id, creator_id, creator_name, chat_id,
                          message_id, message_thread_id, diamond_amount,
                          fee_percent, expires_at
                   FROM two_player_games
                   WHERE status = 'waiting' AND message_id IS NOT NULL'''
            ).fetchall()

        self.active_games.update(
            {
                str(game_id): {
                    "creator_id": int(creator_id),
                    "creator_name": str(creator_name),
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                    "message_thread_id": (
                        int(message_thread_id)
                        if message_thread_id is not None
                        else None
                    ),
                    "diamond_amount": int(diamond_amount),
                    "fee_percent": int(fee_percent or 0),
                    "expires_at": str(expires_at or ""),
                }
                for (
                    game_id,
                    creator_id,
                    creator_name,
                    chat_id,
                    message_id,
                    message_thread_id,
                    diamond_amount,
                    fee_percent,
                    expires_at,
                ) in rows
            }
        )

    def save_activated_user(self, user_id, phone, process, session_file):
        """ثبت نتیجه فعال‌سازی موفق بدون حذف اطلاعات قبلی کاربر."""
        process_pid = int(getattr(process, "pid", process))
        daily_cost = get_financial_config(USERS_DB)["daily_self_cost"]
        next_billing_at = (
            (datetime.now() + timedelta(days=1)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if daily_cost > 0
            else None
        )
        with db_connect(USERS_DB) as conn:
            conn.execute(
                '''INSERT INTO users (
                       user_id, phone, coins, join_date, is_active,
                       self_pid, session_file, self_enabled, self_status,
                       self_last_error, self_last_started_at,
                       self_consecutive_failures, self_next_restart_at,
                       self_last_billed_at, self_next_billing_at, updated_at
                   )
                   VALUES (
                       ?, ?, ?, datetime('now'), 1, ?, ?, 1, 'running',
                       NULL, datetime('now'), 0, NULL, datetime('now'), ?,
                       datetime('now')
                   )
                   ON CONFLICT(user_id) DO UPDATE SET
                       phone = excluded.phone,
                       coins = excluded.coins,
                       is_active = 1,
                       self_pid = excluded.self_pid,
                       session_file = excluded.session_file,
                       self_enabled = 1,
                       self_status = 'running',
                       self_last_error = NULL,
                       self_last_started_at = datetime('now'),
                       self_consecutive_failures = 0,
                       self_next_restart_at = NULL,
                       self_last_billed_at = datetime('now'),
                       self_next_billing_at = excluded.self_next_billing_at,
                       updated_at = datetime('now')''',
                (
                    user_id,
                    phone,
                    self.user_coins.get(user_id, 0),
                    process_pid,
                    str(session_file),
                    next_billing_at,
                ),
            )
        self.admin_store.record_self_release(
            int(user_id),
            to_version=self.current_release,
            code_hash=self.self_code_hash,
            status="success",
            reason="activation",
        )

    @staticmethod
    def _parse_stored_datetime(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    def get_selfbot_record(self, user_id: int):
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                '''SELECT user_id, username, first_name, last_name, phone,
                          expiration_date, self_pid, session_file,
                          self_enabled, self_status, self_last_error,
                          self_last_started_at, self_last_stopped_at,
                          self_restart_count, self_consecutive_failures,
                          self_next_restart_at, self_last_billed_at,
                          self_next_billing_at, self_version,
                          self_previous_version, self_code_hash,
                          self_last_updated_at, updated_at
                   FROM users
                   WHERE user_id = ?''',
                (int(user_id),),
            ).fetchone()

    def registered_selfbot_rows(self):
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                '''SELECT user_id, username, first_name, last_name, phone,
                          expiration_date, self_pid, session_file,
                          self_enabled, self_status, self_last_error,
                          self_last_started_at, self_last_stopped_at,
                          self_restart_count, self_consecutive_failures,
                          self_next_restart_at, self_last_billed_at,
                          self_next_billing_at, self_version,
                          self_previous_version, self_code_hash,
                          self_last_updated_at, updated_at
                   FROM users
                   WHERE phone IS NOT NULL
                     AND TRIM(phone) != ''
                     AND session_file IS NOT NULL
                     AND TRIM(session_file) != ''
                   ORDER BY self_enabled DESC, updated_at DESC, user_id'''
            ).fetchall()

    @staticmethod
    def _self_runtime_fields() -> set[str]:
        return {
            "phone",
            "session_file",
            "self_pid",
            "self_enabled",
            "self_status",
            "self_last_error",
            "self_last_started_at",
            "self_last_stopped_at",
            "self_restart_count",
            "self_consecutive_failures",
            "self_next_restart_at",
            "self_last_billed_at",
            "self_next_billing_at",
            "self_version",
            "self_previous_version",
            "self_code_hash",
            "self_last_updated_at",
        }

    def update_selfbot_runtime(self, user_id: int, **values) -> None:
        values = {
            key: value
            for key, value in values.items()
            if key in self._self_runtime_fields()
        }
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute(
                f'''UPDATE users
                    SET {assignments}, updated_at = datetime('now')
                    WHERE user_id = ?''',
                (*values.values(), int(user_id)),
            )

    def selfbot_session_path(self, user_id: int, stored_path=None) -> Path:
        expected = SESSIONS_DIR / f"session_{int(user_id)}.txt"
        if stored_path:
            candidate = Path(str(stored_path))
            try:
                if (
                    candidate.name == expected.name
                    and candidate.resolve().parent == SESSIONS_DIR.resolve()
                    and candidate.is_file()
                ):
                    return candidate
            except OSError:
                pass
        return expected

    @staticmethod
    def read_runtime_status(status_file: Path) -> dict:
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def selfbot_pid_is_running(
        self,
        pid,
        *,
        session_file: Path | None = None,
    ) -> bool:
        try:
            pid = int(pid)
            if pid <= 0:
                return False
            process = psutil.Process(pid)
            command_parts = process.cmdline()
            command = " ".join(command_parts)
            if (
                not process.is_running()
                or process.status() == psutil.STATUS_ZOMBIE
                or SELF_BOT_SCRIPT.name not in command
            ):
                return False
            if session_file and session_file.name not in command:
                return False
            return True
        except (TypeError, ValueError, psutil.Error, OSError):
            return False

    def running_selfbot_pid(self, user_id: int, record=None):
        record = record or self.get_selfbot_record(user_id)
        session_file = self.selfbot_session_path(
            user_id,
            record["session_file"] if record else None,
        )
        info = self.active_selfbots.get(int(user_id), {})
        candidate_pids = [
            info.get("pid"),
            getattr(info.get("process"), "pid", None),
            record["self_pid"] if record else None,
        ]
        for pid in candidate_pids:
            if self.selfbot_pid_is_running(
                pid,
                session_file=session_file,
            ):
                return int(pid)
        return None

    def selfbot_is_expired(self, record) -> bool:
        expiration = self._parse_stored_datetime(
            record["expiration_date"] if record else None
        )
        if expiration is None:
            return False
        now = datetime.now(expiration.tzinfo) if expiration.tzinfo else datetime.now()
        return now >= expiration

    @staticmethod
    def permanent_selfbot_failure(detail: str) -> bool:
        normalized = str(detail or "").casefold()
        return any(
            marker in normalized
            for marker in (
                "auth_key_unregistered",
                "auth key is unregistered",
                "session_revoked",
                "session expired",
                "user_deactivated",
                "سشن تلگرام نامعتبر",
                "سشن نامعتبر",
                "فایل سشن پیدا نشد",
                "فایل سشن خالی",
            )
        )

    def adopt_running_selfbot(self, record, pid: int) -> None:
        user_id = int(record["user_id"])
        session_file = self.selfbot_session_path(
            user_id,
            record["session_file"],
        )
        self.active_selfbots[user_id] = {
            "process": None,
            "pid": int(pid),
            "phone": str(record["phone"]),
            "session_file": session_file,
            "status_file": SESSIONS_DIR / f"session_{user_id}.status.json",
        }
        if (
            record["self_status"] != "running"
            or int(record["self_pid"] or 0) != int(pid)
        ):
            self.update_selfbot_runtime(
                user_id,
                self_pid=int(pid),
                self_status="running",
                self_last_error=None,
                self_consecutive_failures=0,
                self_next_restart_at=None,
            )

    def self_operation_lock(self, user_id: int) -> asyncio.Lock:
        user_id = int(user_id)
        lock = self.self_operation_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self.self_operation_locks[user_id] = lock
        return lock

    async def terminate_selfbot_process(self, user_id: int) -> bool:
        record = self.get_selfbot_record(user_id)
        pid = self.running_selfbot_pid(user_id, record)
        if not pid:
            self.active_selfbots.pop(int(user_id), None)
            return False
        try:
            process = psutil.Process(pid)
            process.terminate()
            _, alive = await asyncio.to_thread(
                psutil.wait_procs,
                [process],
                timeout=5,
            )
            for remaining in alive:
                try:
                    remaining.kill()
                except psutil.Error:
                    pass
            if alive:
                await asyncio.to_thread(psutil.wait_procs, alive, timeout=3)
        except (psutil.Error, OSError):
            pass
        self.active_selfbots.pop(int(user_id), None)
        return True

    async def stop_selfbot(
        self,
        user_id: int,
        *,
        disable: bool,
        status: str = "stopped",
        detail: str | None = None,
    ) -> None:
        user_id = int(user_id)
        restart_task = self.self_restart_tasks.pop(user_id, None)
        current_task = asyncio.current_task()
        if (
            restart_task
            and restart_task is not current_task
            and not restart_task.done()
        ):
            restart_task.cancel()
        async with self.self_operation_lock(user_id):
            await self.terminate_selfbot_process(user_id)
            values = {
                "self_pid": None,
                "self_status": status,
                "self_last_error": detail,
                "self_last_stopped_at": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "self_next_restart_at": None,
            }
            if disable:
                values["self_enabled"] = 0
                values["self_consecutive_failures"] = 0
            self.update_selfbot_runtime(user_id, **values)

    async def delete_selfbot(self, user_id: int) -> None:
        """Stop one self-bot and permanently remove its operational files.

        User media is cloud-only.  Deleted sessions/settings are not archived
        on the host; an administrator must create a Telegram-cloud backup
        before deletion when recovery is required.
        """
        user_id = int(user_id)
        record = self.get_selfbot_record(user_id)
        if record is None or not record["phone"] or not record["session_file"]:
            raise LookupError("سلف پیدا نشد.")

        phone = str(record["phone"])
        session_file = self.selfbot_session_path(
            user_id,
            record["session_file"],
        )
        database_file = self_database_path(DATA_DIR, phone)
        await self.stop_selfbot(
            user_id,
            disable=True,
            status="deleting",
            detail=None,
        )

        paths = [
            session_file,
            database_file,
            Path(f"{database_file}-wal"),
            Path(f"{database_file}-shm"),
        ]
        for source in paths:
            try:
                source.unlink(missing_ok=True)
            except OSError as exc:
                logging.warning("Could not delete self-bot file %s: %s", source, exc)

        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE users
                   SET phone = NULL,
                       expiration_date = NULL,
                       self_pid = NULL,
                       session_file = NULL,
                       self_enabled = 0,
                       self_status = 'deleted',
                       self_last_error = NULL,
                       self_last_stopped_at = datetime('now'),
                       self_consecutive_failures = 0,
                       updated_at = datetime('now')
                   WHERE user_id = ?""",
                (user_id,),
            )
        self.processes.pop(user_id, None)
        self.process_meta.pop(user_id, None)


    async def launch_saved_selfbot(
        self,
        user_id: int,
        *,
        reason: str,
        enable_watchdog: bool = True,
    ) -> tuple[bool, str]:
        user_id = int(user_id)
        async with self.self_operation_lock(user_id):
            record = self.get_selfbot_record(user_id)
            if record is None:
                return False, "کاربر پیدا نشد."
            if not record["phone"]:
                return False, "شماره سلف ثبت نشده است."
            if self.selfbot_is_expired(record):
                await self.terminate_selfbot_process(user_id)
                self.update_selfbot_runtime(
                    user_id,
                    self_pid=None,
                    self_enabled=0,
                    self_status="expired",
                    self_last_error="اعتبار سلف منقضی شده است.",
                    self_last_stopped_at=datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    self_next_restart_at=None,
                )
                return False, "اعتبار سلف منقضی شده است."

            session_file = self.selfbot_session_path(
                user_id,
                record["session_file"],
            )
            if not session_file.is_file():
                detail = f"فایل سشن پیدا نشد: {session_file}"
                self.record_selfbot_failure(
                    user_id,
                    detail,
                    permanent=True,
                )
                return False, detail
            try:
                if not read_session_file(
                    session_file,
                    DATA_DIR,
                    migrate_plaintext=True,
                ):
                    raise ValueError("فایل سشن خالی است.")
            except (OSError, ValueError) as exc:
                detail = f"خواندن فایل سشن ناموفق بود: {exc}"
                self.record_selfbot_failure(
                    user_id,
                    detail,
                    permanent=True,
                )
                return False, detail

            running_pid = self.running_selfbot_pid(user_id, record)
            if running_pid:
                self.adopt_running_selfbot(record, running_pid)
                return True, "سلف از قبل در حال اجرا است."

            status_file = SESSIONS_DIR / f"session_{user_id}.status.json"
            status_file.unlink(missing_ok=True)
            child_env = os.environ.copy()
            child_env["TELEGRAM_API_ID"] = str(self.api_id)
            child_env["TELEGRAM_API_HASH"] = self.api_hash
            child_env["BOT_DATA_DIR"] = str(DATA_DIR)
            try:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(SELF_BOT_SCRIPT),
                        "--phone",
                        str(record["phone"]),
                        "--session-file",
                        str(session_file),
                        "--status-file",
                        str(status_file),
                    ],
                    cwd=str(BASE_DIR),
                    env=child_env,
                    start_new_session=True,
                )
            except Exception as exc:
                detail = f"اجرای فرایند سلف ناموفق بود: {exc}"
                self.record_selfbot_failure(
                    user_id,
                    detail,
                    permanent=False,
                )
                return False, detail

            self.active_selfbots[user_id] = {
                "process": process,
                "pid": process.pid,
                "phone": str(record["phone"]),
                "session_file": session_file,
                "status_file": status_file,
            }
            self.update_selfbot_runtime(
                user_id,
                self_pid=process.pid,
                self_enabled=1 if enable_watchdog else 0,
                self_status="starting",
                self_last_error=None,
                session_file=str(session_file),
                self_next_restart_at=None,
            )

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.self_start_timeout
            detail = "پاسخ آماده‌بودن سلف دریافت نشد."
            while loop.time() < deadline:
                runtime_status = self.read_runtime_status(status_file)
                if runtime_status.get("detail"):
                    detail = str(runtime_status["detail"])
                if runtime_status.get("status") == "ready":
                    previous_restarts = int(
                        record["self_restart_count"] or 0
                    )
                    restart_count = (
                        previous_restarts + 1
                        if reason != "activation"
                        else previous_restarts
                    )
                    self.update_selfbot_runtime(
                        user_id,
                        self_pid=process.pid,
                        self_enabled=1 if enable_watchdog else 0,
                        self_status="running",
                        self_last_error=None,
                        self_last_started_at=datetime.now().strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        self_restart_count=restart_count,
                        self_consecutive_failures=0,
                        self_next_restart_at=None,
                    )
                    if (
                        str(record["self_version"] or "")
                        != self.current_release
                        or "update" in reason
                    ):
                        self.admin_store.record_self_release(
                            user_id,
                            to_version=self.current_release,
                            code_hash=self.self_code_hash,
                            status="success",
                            reason=reason,
                        )
                    return True, "سلف آماده و در حال اجرا است."
                if runtime_status.get("status") == "failed":
                    break
                if process.poll() is not None:
                    detail = (
                        runtime_status.get("detail")
                        or f"فرایند سلف با کد {process.returncode} متوقف شد."
                    )
                    break
                await asyncio.sleep(0.25)

            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    process.kill()
            self.active_selfbots.pop(user_id, None)
            permanent = self.permanent_selfbot_failure(detail)
            self.record_selfbot_failure(
                user_id,
                detail,
                permanent=permanent,
            )
            if "update" in reason:
                self.admin_store.record_self_release(
                    user_id,
                    to_version=self.current_release,
                    code_hash=self.self_code_hash,
                    status="failed",
                    reason=reason,
                    detail=detail,
                )
            return False, detail

    async def watchdog_restart_selfbot(self, user_id: int) -> None:
        try:
            async with self.self_restart_semaphore:
                success, detail = await self.launch_saved_selfbot(
                    user_id,
                    reason="watchdog",
                )
                if success:
                    record = self.get_selfbot_record(user_id)
                    restart_count = int(
                        record["self_restart_count"] or 0
                    ) if record else 0
                    self.admin_store.queue_admin_notification(
                        "self_recovered",
                        "بازیابی خودکار سلف",
                        (
                            f"سلف کاربر {user_id} دوباره آنلاین شد.\n"
                            f"تعداد بازیابی: {restart_count}\n"
                            f"نتیجه: {detail}"
                        ),
                        user_id=user_id,
                        fingerprint=(
                            f"self-recovered:{user_id}:{restart_count}"
                        ),
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception(
                "Unexpected watchdog restart error for selfbot %s",
                user_id,
            )
        finally:
            current = self.self_restart_tasks.get(int(user_id))
            if current is asyncio.current_task():
                self.self_restart_tasks.pop(int(user_id), None)

    def sync_daily_billing_schedule(self, daily_cost: int) -> None:
        """Initialize or clear the next charge without surprising old users."""
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            if int(daily_cost) <= 0:
                conn.execute(
                    '''UPDATE users
                       SET self_next_billing_at = NULL
                       WHERE self_next_billing_at IS NOT NULL'''
                )
                return
            next_charge = (datetime.now() + timedelta(days=1)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            conn.execute(
                '''UPDATE users
                   SET self_next_billing_at = ?
                   WHERE self_enabled = 1
                     AND phone IS NOT NULL
                     AND TRIM(phone) != ''
                     AND session_file IS NOT NULL
                     AND TRIM(session_file) != ''
                     AND self_next_billing_at IS NULL''',
                (next_charge,),
            )

    def charge_daily_self_fee(
        self,
        user_id: int,
        daily_cost: int,
    ) -> tuple[bool, int]:
        """Atomically deduct one daily fee and return success/new balance."""
        user_id = int(user_id)
        daily_cost = int(daily_cost)
        if daily_cost <= 0:
            return True, self.user_coins.get(user_id, 0)
        next_charge = (datetime.now() + timedelta(days=1)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                '''SELECT coins, self_enabled
                   FROM users
                   WHERE user_id = ?''',
                (user_id,),
            ).fetchone()
            if row is None:
                raise LookupError("کاربر سلف پیدا نشد.")
            balance = int(row[0] or 0)
            if not int(row[1] or 0):
                return True, balance
            if balance < daily_cost:
                conn.execute(
                    '''UPDATE users
                       SET self_enabled = 0,
                           self_status = 'insufficient_balance',
                           self_last_error = ?,
                           self_next_billing_at = NULL,
                           updated_at = datetime('now')
                       WHERE user_id = ?''',
                    (
                        f"موجودی برای هزینه روزانه {daily_cost} سکه کافی نبود.",
                        user_id,
                    ),
                )
                self.user_coins[user_id] = balance
                return False, balance

            new_balance = balance - daily_cost
            self._upsert_user_coins(conn, user_id, new_balance)
            conn.execute(
                '''UPDATE users
                   SET self_last_billed_at = datetime('now'),
                       self_next_billing_at = ?,
                       updated_at = datetime('now')
                   WHERE user_id = ?''',
                (next_charge, user_id),
            )
            conn.execute(
                '''INSERT INTO balance_transactions (
                       user_id, amount, balance_after, transaction_type, note
                   )
                   VALUES (?, ?, ?, 'daily_self_fee', ?)''',
                (
                    user_id,
                    -daily_cost,
                    new_balance,
                    "برداشت خودکار هزینه روزانه سلف",
                ),
            )
        self.user_coins[user_id] = new_balance
        return True, new_balance

    async def process_daily_self_billing(self) -> None:
        daily_cost = get_financial_config(USERS_DB)["daily_self_cost"]
        if daily_cost <= 0:
            return
        async with self.self_billing_lock:
            now = datetime.now()
            with db_connect(USERS_DB, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    '''SELECT user_id, self_next_billing_at
                       FROM users
                       WHERE self_enabled = 1
                         AND phone IS NOT NULL
                         AND TRIM(phone) != ''
                         AND session_file IS NOT NULL
                         AND TRIM(session_file) != '' '''
                ).fetchall()

            for row in rows:
                user_id = int(row["user_id"])
                due_at = self._parse_stored_datetime(
                    row["self_next_billing_at"]
                )
                if due_at is None:
                    self.update_selfbot_runtime(
                        user_id,
                        self_next_billing_at=(
                            now + timedelta(days=1)
                        ).strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    continue
                comparable_now = (
                    datetime.now(due_at.tzinfo)
                    if due_at.tzinfo
                    else now
                )
                if comparable_now < due_at:
                    continue
                charged, balance = self.charge_daily_self_fee(
                    user_id,
                    daily_cost,
                )
                if charged:
                    try:
                        await self.application.bot.send_message(
                            chat_id=user_id,
                            text=(
                                f"📅 هزینه روزانه سلف ({daily_cost} سکه) "
                                "کسر شد.\n"
                                f"💰 موجودی جدید: {balance} سکه"
                            ),
                        )
                    except TelegramError:
                        pass
                    continue

                await self.stop_selfbot(
                    user_id,
                    disable=True,
                    status="insufficient_balance",
                    detail=(
                        f"موجودی برای هزینه روزانه {daily_cost} سکه "
                        "کافی نبود."
                    ),
                )
                try:
                    await self.application.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "⛔ سلف به‌دلیل کمبود موجودی خاموش شد.\n"
                            f"هزینه روزانه: {daily_cost} سکه\n"
                            f"موجودی فعلی: {balance} سکه\n\n"
                            "پس از افزایش موجودی، سلف را دوباره فعال کنید."
                        ),
                    )
                except TelegramError:
                    pass

    async def process_subscription_lifecycle(self) -> None:
        renewal_results = self.admin_store.process_auto_renewals()
        for item in renewal_results:
            user_id = int(item["user_id"])
            if item["renewed"]:
                self.user_coins[user_id] = int(item["balance"])
                message = (
                    "✅ اشتراک سلف شما خودکار تمدید شد.\n"
                    f"پلن: {item['plan_name']}\n"
                    f"هزینه: {item['price']} سکه\n"
                    f"موجودی: {item['balance']} سکه\n"
                    f"انقضای جدید: {item['expires_at']}"
                )
            else:
                message = (
                    "⚠️ تمدید خودکار اشتراک انجام نشد.\n"
                    f"هزینه پلن: {item['price']} سکه\n"
                    f"موجودی: {item['balance']} سکه\n\n"
                    "تمدید خودکار خاموش شد؛ لطفاً موجودی را افزایش دهید."
                )
            try:
                await self.application.bot.send_message(user_id, message)
            except TelegramError:
                pass

        notices = self.admin_store.queue_due_expiry_notifications()
        for notice in notices:
            threshold = int(notice["threshold_days"])
            if threshold == 0:
                message = (
                    "⛔ اعتبار اشتراک سلف شما منقضی شده است.\n"
                    "برای فعال‌سازی دوباره، اشتراک را تمدید کنید."
                )
            else:
                auto_renew = bool(notice["auto_renew"])
                message = (
                    f"⏳ {threshold} روز یا کمتر تا پایان اشتراک سلف شما "
                    "باقی مانده است.\n"
                    f"پلن: {notice['plan_name'] or 'ثبت نشده'}\n"
                    f"انقضا: {notice['expires_at']}\n"
                    f"تمدید خودکار: {'فعال' if auto_renew else 'خاموش'}"
                )
            try:
                await self.application.bot.send_message(
                    int(notice["user_id"]), message
                )
                sent = True
            except TelegramError:
                sent = False
            self.admin_store.mark_expiry_notification(
                int(notice["id"]), sent=sent
            )

    async def dispatch_admin_notifications(self) -> None:
        notifications = self.admin_store.pending_admin_notifications()
        if not notifications:
            return
        admin_ids = [
            admin_id
            for admin_id in get_admin_ids(self.users_db, self.owner_id)
            if self.cc_allowed(admin_id, "reports")
        ]
        for notification in notifications:
            delivered = False
            message = (
                f"⚠️ {notification['title']}\n\n"
                f"{notification['body']}"
            )
            for admin_id in admin_ids:
                try:
                    await self.application.bot.send_message(admin_id, message)
                    delivered = True
                except TelegramError:
                    continue
            if delivered:
                self.admin_store.mark_admin_notification_sent(
                    int(notification["id"])
                )

    async def reconcile_selfbots(self) -> None:
        for record in self.registered_selfbot_rows():
            user_id = int(record["user_id"])
            running_pid = self.running_selfbot_pid(user_id, record)
            if running_pid:
                self.adopt_running_selfbot(record, running_pid)
                continue

            self.active_selfbots.pop(user_id, None)
            if not int(record["self_enabled"] or 0):
                if record["self_pid"]:
                    self.update_selfbot_runtime(user_id, self_pid=None)
                continue
            if self.selfbot_is_expired(record):
                await self.stop_selfbot(
                    user_id,
                    disable=True,
                    status="expired",
                    detail="اعتبار سلف منقضی شده است.",
                )
                continue
            next_restart = self._parse_stored_datetime(
                record["self_next_restart_at"]
            )
            if next_restart:
                now = (
                    datetime.now(next_restart.tzinfo)
                    if next_restart.tzinfo
                    else datetime.now()
                )
                if now < next_restart:
                    continue
            existing = self.self_restart_tasks.get(user_id)
            if existing and not existing.done():
                continue
            if str(record["self_status"] or "") == "running":
                self.admin_store.queue_admin_notification(
                    "self_stopped",
                    "توقف ناگهانی سلف",
                    (
                        f"اجرای سلف کاربر {user_id} متوقف شد و وارد صف "
                        "بازیابی خودکار شد."
                    ),
                    user_id=user_id,
                    fingerprint=(
                        f"self-stopped:{user_id}:"
                        f"{record['self_restart_count'] or 0}:"
                        f"{record['updated_at'] or ''}"
                    ),
                )
            self.update_selfbot_runtime(
                user_id,
                self_pid=None,
                self_status="restarting",
            )
            self.self_restart_tasks[user_id] = asyncio.create_task(
                self.watchdog_restart_selfbot(user_id),
                name=f"selfbot-restart-{user_id}",
            )

    async def selfbot_watchdog_loop(self) -> None:
        while True:
            try:
                await self.process_subscription_lifecycle()
                await self.process_daily_self_billing()
                await self.reconcile_selfbots()
                await self.dispatch_admin_notifications()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Selfbot watchdog iteration failed")
            await asyncio.sleep(SELF_WATCHDOG_INTERVAL)
    
    def setup_handlers(self):
        # ابتدا هندلرهای معمولی
        self.application.add_handler(CommandHandler("link", self.create_invite_link))
        self.application.add_handler(CommandHandler("balance", self.show_balance))
        self.application.add_handler(CommandHandler("transfer", self.transfer_coins))
        self.application.add_handler(CommandHandler("admin", self.open_admin_panel))

        # دریافت توکن هلپر در گروه زودتر پردازش می‌شود تا وارد جریان
        # شماره، کد ورود یا رمز دومرحله‌ای ربات سازنده نشود.
        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.receive_admin_text,
            ),
            group=-1,
        )
        
        # دستورات مالک
        self.application.add_handler(CommandHandler("kasr", self.kasr_coins))
        self.application.add_handler(CommandHandler("id", self.get_user_id))
        self.application.add_handler(CommandHandler("addcoins", self.add_coins))
        self.application.add_handler(
            CommandHandler("gamehistory", self.game_history_command)
        )
        self.application.add_handler(
            CommandHandler("gameretry", self.game_retry_command)
        )
        self.application.add_handler(CommandHandler("treasury", self.treasury_command))
        self.application.add_handler(CommandHandler("betallow", self.bet_allow_command))
        self.application.add_handler(CommandHandler("betdeny", self.bet_deny_command))
        self.application.add_handler(CommandHandler("betgroups", self.bet_groups_command))
        
        # هندلر برای پیام‌های متنی
        self.application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^انتقال\s+\d+$'), self.transfer_coins_farsi))
        self.application.add_handler(MessageHandler(filters.TEXT & filters.Regex(r'^موجودی$'), self.show_balance_farsi))
        self.application.add_handler(
            MessageHandler(
                filters.ChatType.GROUPS
                & filters.TEXT
                & filters.Regex(r'^[۰-۹٠-٩0-9]{1,20}$'),
                self.receive_transfer_target,
            )
        )
        self.application.add_handler(
            MessageHandler(
                filters.ChatType.GROUPS
                & filters.TEXT
                & filters.Regex(r'^(?:لغو انتقال|انتقال لغو)$'),
                self.cancel_pending_transfer,
            )
        )
        self.application.add_handler(
            MessageHandler(
                filters.ChatType.GROUPS
                & filters.TEXT
                & filters.Regex(r'^\s*بازی(?:\s+.*)?$'),
                self.create_game,
            )
        )
        
        # هندلر بازی دونفره
        self.application.add_handler(
            CallbackQueryHandler(self.join_game, pattern=r'^join_game:')
        )
        self.application.add_handler(
            CallbackQueryHandler(self.cancel_game, pattern=r'^cancel_game:')
        )
        self.application.add_handler(
            CallbackQueryHandler(
                self.balance_button,
                pattern=r'^balance_view:',
            )
        )
        self.application.add_handler(
            CallbackQueryHandler(
                self.game_result_button,
                pattern=r'^game_result:',
            )
        )
        self.application.add_handler(
            CallbackQueryHandler(
                self.handle_custom_start_button,
                pattern=r'^start_button:',
            )
        )
        self.application.add_handler(
            CallbackQueryHandler(self.handle_admin_panel, pattern=r'^admin:')
        )
        
        # در آخر Conversation Handler
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', self.start)],
            states={
                CHECK_MEMBERSHIP: [
                    CallbackQueryHandler(self.check_membership, pattern='^(check|join)$')
                ],
                ACTIVATION_PANEL: [
                    CallbackQueryHandler(
                        self.activation_panel,
                        pattern=(
                            '^(activate|support|rules|wallet|wallet_history|'
                            'support_ticket|buy_coins|back|stats|invite|'
                            'panel_help)$'
                        ),
                    )
                ],
                GET_PHONE: [
                    MessageHandler(
                        (filters.CONTACT | filters.TEXT) & ~filters.COMMAND,
                        self.get_phone_number,
                    )
                ],
                GET_CODE: [
                    CallbackQueryHandler(
                        self.verify_code,
                        pattern=(
                            r"^(?:[0-9]|display|delete|submit|login_cancel)$"
                        ),
                    ),
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.verify_code_text,
                    ),
                ],
                GET_PASSWORD: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.verify_password,
                    )
                ],
                COIN_PURCHASE: [
                    CallbackQueryHandler(self.coin_purchase, pattern='^.*$')
                ],
                RECEIPT_UPLOAD: [
                    MessageHandler(
                        filters.PHOTO | filters.Document.ALL,
                        self.receive_payment_receipt,
                    ),
                    CallbackQueryHandler(
                        self.cancel_receipt_upload,
                        pattern=r'^receipt_cancel$',
                    ),
                ]
            },
            fallbacks=[
                CommandHandler('start', self.start),
                CommandHandler('cancel', self.cancel),
            ],
            allow_reentry=True,
            per_message=False,
        )
        
        self.application.add_handler(conv_handler)
        self.application.add_error_handler(self.handle_application_error)

    async def handle_application_error(
        self,
        update: object,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Log handler failures without exposing the complete Telegram update."""
        error = context.error
        if error is None:
            logging.error("Telegram update handler failed without an exception.")
            return

        logging.error(
            "Telegram update handler failed: %s",
            type(error).__name__,
            exc_info=(type(error), error, error.__traceback__),
        )
        self.admin_store.event(
            "ERROR",
            "main-bot-handler",
            type(error).__name__,
            getattr(getattr(update, "effective_user", None), "id", None),
        )
    
    def is_owner(self, user_id: int) -> bool:
        return user_id == self.owner_id

    def is_admin(self, user_id: int) -> bool:
        return int(user_id) in get_admin_ids(USERS_DB, self.owner_id)

    @staticmethod
    def clear_admin_input(context: ContextTypes.DEFAULT_TYPE) -> None:
        for key in (
            "awaiting_helper_token",
            "awaiting_admin_setting",
            "awaiting_gift_user_id",
            "awaiting_gift_amount",
            "gift_target_user_id",
            "awaiting_new_admin_id",
            "awaiting_start_button_label",
            "awaiting_start_button_payload",
            "start_button_draft",
            "awaiting_cc_action",
        ):
            context.user_data.pop(key, None)

    @staticmethod
    def format_card_number(card_number: str) -> str:
        digits = re.sub(r"\D", "", card_number or "")
        return " ".join(
            digits[index:index + 4]
            for index in range(0, len(digits), 4)
        )

    @staticmethod
    def normalize_financial_setting(key: str, value: str) -> str:
        raw = (value or "").strip()
        if key == "payment_card_number":
            digits = re.sub(r"\D", "", raw)
            if len(digits) != 16:
                raise ValueError("شماره کارت باید دقیقاً ۱۶ رقم باشد.")
            return digits
        if key == "payment_card_holder":
            if not 2 <= len(raw) <= 100:
                raise ValueError("نام صاحب کارت باید بین ۲ تا ۱۰۰ نویسه باشد.")
            return raw
        if key == "payment_contact_username":
            if raw.lower() in {"حذف", "خالی", "none", "off"}:
                return ""
            username = re.sub(
                r"^https?://(?:www\.)?t\.me/",
                "",
                raw,
                flags=re.IGNORECASE,
            ).strip("/").lstrip("@")
            if not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{3,31}",
                username,
            ):
                raise ValueError("نام کاربری تلگرام معتبر نیست.")
            return username
        if key == "support_url":
            if raw.lower() in {"حذف", "خالی", "none", "off"}:
                return ""
            username = re.sub(
                r"^https?://(?:www\.)?t\.me/",
                "",
                raw,
                flags=re.IGNORECASE,
            ).strip("/").lstrip("@")
            if not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{3,31}",
                username,
            ):
                raise ValueError("لینک یا نام کاربری پشتیبانی معتبر نیست.")
            return f"https://t.me/{username}"
        if key == "betting_fee_percent":
            normalized = raw.translate(
                str.maketrans(
                    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                    "01234567890123456789",
                )
            ).replace("%", "").strip()
            if not normalized.isdigit():
                raise ValueError("درصد کارمزد باید فقط عدد باشد.")
            number = int(normalized)
            if not 0 <= number <= 50:
                raise ValueError("درصد کارمزد باید بین ۰ تا ۵۰ باشد.")
            return str(number)
        if key in {
            "coin_price_toman",
            "activation_cost_coins",
            "daily_self_cost_coins",
            "new_user_gift_coins",
            "referral_reward_coins",
            "transfer_min_coins",
            "transfer_max_coins",
            "transfer_daily_limit_coins",
        }:
            normalized = raw.translate(
                str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
            ).replace(",", "").replace("٬", "")
            if not normalized.isdigit():
                raise ValueError("مقدار باید فقط عدد باشد.")
            number = int(normalized)
            minimum = (
                1
                if key in {
                    "coin_price_toman",
                    "transfer_min_coins",
                    "transfer_max_coins",
                }
                else 0
            )
            if not minimum <= number <= 1_000_000_000:
                raise ValueError(
                    f"مقدار باید بین {minimum:,} تا 1,000,000,000 باشد."
                )
            return str(number)
        raise ValueError("تنظیم ناشناخته است.")

    @staticmethod
    def normalize_content_setting(key: str, value: str) -> str:
        raw = (value or "").strip()
        if key == "support_url":
            if raw.lower() in {"حذف", "خالی", "none", "off"}:
                return ""
            username = re.sub(
                r"^https?://(?:www\.)?t\.me/",
                "",
                raw,
                flags=re.IGNORECASE,
            ).strip("/").lstrip("@")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", username):
                raise ValueError("لینک یا نام کاربری پشتیبانی معتبر نیست.")
            return f"https://t.me/{username}"
        if key in {"support_text", "rules_text"}:
            if not 2 <= len(raw) <= 3500:
                raise ValueError("متن باید بین ۲ تا ۳۵۰۰ نویسه باشد.")
            return raw
        raise ValueError("تنظیم ناشناخته است.")

    @staticmethod
    def normalize_branding_setting(key: str, value: str) -> str:
        raw = " ".join((value or "").strip().split())
        if key != "bot_display_name":
            raise ValueError("تنظیم ناشناخته است.")
        if not 1 <= len(raw) <= 64:
            raise ValueError("نام ربات باید بین ۱ تا ۶۴ نویسه باشد.")
        return raw

    @staticmethod
    def normalize_start_button_label(value: str) -> str:
        label = " ".join((value or "").strip().split())
        if not 1 <= len(label) <= 64:
            raise ValueError("نام دکمه باید بین ۱ تا ۶۴ نویسه باشد.")
        return label

    @staticmethod
    def normalize_start_button_payload(button_type: str, value: str) -> str:
        raw = (value or "").strip()
        if button_type == "text":
            if not 1 <= len(raw) <= 3500:
                raise ValueError("متن پاسخ باید بین ۱ تا ۳۵۰۰ نویسه باشد.")
            return raw
        if button_type == "url":
            if raw.startswith("@"):
                username = raw.lstrip("@")
                if not re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_]{3,31}",
                    username,
                ):
                    raise ValueError("آیدی گروه یا کانال معتبر نیست.")
                return f"https://t.me/{username}"
            if not re.fullmatch(r"https?://\S{3,2040}", raw):
                raise ValueError(
                    "لینک باید با http:// یا https:// شروع شود؛ "
                    "آیدی @username نیز پذیرفته می‌شود."
                )
            return raw
        raise ValueError("نوع دکمه معتبر نیست.")

    @staticmethod
    def normalize_telegram_target(value: str) -> str:
        raw = (value or "").strip()
        raw = re.sub(
            r"^https?://(?:www\.)?t\.me/",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip("/")
        normalized_digits = raw.translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        )
        if re.fullmatch(r"-?\d{5,20}", normalized_digits):
            return normalized_digits
        username = raw.lstrip("@")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", username):
            raise ValueError(
                "آیدی باید به‌صورت @username یا آیدی عددی معتبر باشد."
            )
        return f"@{username}"

    def normalize_identity_setting(self, key: str, value: str) -> str:
        raw = (value or "").strip()
        if key == "receipt_admin_ids":
            if raw in {"همه", "all", "ALL", "*"}:
                return ""
            normalized = raw.translate(
                str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
            )
            parts = [
                item
                for item in re.split(r"[\s,،;]+", normalized)
                if item
            ]
            if not parts or any(not item.isdigit() for item in parts):
                raise ValueError(
                    "آیدی‌ها باید عددی و با ویرگول از هم جدا شده باشند."
                )
            ids = list(dict.fromkeys(int(item) for item in parts))
            registered_admins = get_admin_ids(USERS_DB, self.owner_id)
            invalid = [item for item in ids if item not in registered_admins]
            if invalid:
                raise ValueError(
                    "این آیدی‌ها در بخش مدیریت ادمین‌ها ثبت نشده‌اند: "
                    + ", ".join(str(item) for item in invalid)
                )
            return ",".join(str(item) for item in ids)
        if key in {
            "self_admin_target",
            "self_group_target",
            "self_channel_target",
        }:
            if raw.lower() in {"حذف", "خالی", "none", "off"}:
                return ""
            return self.normalize_telegram_target(raw)
        if key == "brand_powered_by_username":
            if raw.lower() in {"حذف", "خالی", "none", "off"}:
                return ""
            normalized = re.sub(
                r"^https?://(?:www\.)?t\.me/",
                "@",
                raw,
                flags=re.IGNORECASE,
            ).strip("/")
            if len(normalized) > 64 or any(
                ord(char) < 32 for char in normalized
            ):
                raise ValueError(
                    "متن جایگزین باید حداکثر ۶۴ نویسه باشد."
                )
            if normalized.startswith("@") and not re.fullmatch(
                r"@[A-Za-z][A-Za-z0-9_]{3,31}",
                normalized,
            ):
                raise ValueError("آیدی تلگرام واردشده معتبر نیست.")
            return normalized
        if key in {
            "payment_contact_username",
            "brand_owner_username",
            "brand_self_username",
            "brand_group_username",
        }:
            if raw.lower() in {"حذف", "خالی", "none", "off"}:
                return ""
            username = re.sub(
                r"^https?://(?:www\.)?t\.me/",
                "",
                raw,
                flags=re.IGNORECASE,
            ).strip("/").lstrip("@")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", username):
                raise ValueError("نام کاربری تلگرام معتبر نیست.")
            return username
        raise ValueError("تنظیم ناشناخته است.")

    @staticmethod
    def normalize_force_join_target(value: str) -> str:
        target = (value or "").strip()
        target = re.sub(
            r"^https?://(?:www\.)?t\.me/",
            "",
            target,
            flags=re.IGNORECASE,
        ).strip("/")
        if target.startswith("+") or target.lower().startswith("joinchat/"):
            raise ValueError(
                "برای کانال خصوصی، آیدی عددی کانال را ارسال کنید."
            )
        if re.fullmatch(r"-100\d{5,}", target):
            return target
        target = target.lstrip("@")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,30}[A-Za-z0-9]", target):
            raise ValueError(
                "نام کاربری کانال معتبر نیست؛ نمونه صحیح: @channel_username"
            )
        return f"@{target}"

    @staticmethod
    def clear_force_join_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
        for key in (
            "awaiting_force_join_channel",
            "awaiting_force_join_link",
            "force_join_draft",
        ):
            context.user_data.pop(key, None)

    def helper_is_running(self) -> bool:
        if self.helper_process and self.helper_process.poll() is None:
            return True

        config = get_helper_config(USERS_DB)
        pid = config.get("pid")
        if not pid:
            return False

        try:
            process = psutil.Process(pid)
            command = " ".join(process.cmdline())
            return (
                process.is_running()
                and process.status() != psutil.STATUS_ZOMBIE
                and HELPER_BOT_SCRIPT.name in command
            )
        except (psutil.Error, OSError):
            return False

    def helper_is_ready(self) -> bool:
        config = get_helper_config(USERS_DB)
        try:
            runtime_status = json.loads(
                HELPER_STATUS_FILE.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            runtime_status = {}
        return bool(
            config.get("enabled")
            and config.get("token")
            and config.get("username")
            and self.helper_is_running()
            and runtime_status.get("status") == "ready"
        )

    def create_admin_keyboard(self):
        # v2.9: the main panel is intentionally compact.  Existing callbacks
        # remain valid and are reached from their relevant control-center page.
        return self.control_center_keyboard()

    def _create_admin_keyboard_legacy(self):
        config = get_helper_config(USERS_DB)
        running = self.helper_is_running()
        pending_receipts = self.pending_receipt_count()
        selfbot_rows = self.registered_selfbot_rows()
        running_selfbots = sum(
            self.running_selfbot_pid(int(row["user_id"]), row) is not None
            for row in selfbot_rows
        )
        restart_label = (
            "♻️ راه‌اندازی مجدد هلپر"
            if config.get("token")
            else "▶️ راه‌اندازی هلپر"
        )
        rows = [
            [
                glass_button(
                    f"🧾 رسیدهای در انتظار ({pending_receipts})",
                    callback_data="admin:receipts:page:0",
                    style="success" if pending_receipts else "primary",
                )
            ],
            [
                glass_button(
                    "💳 تنظیمات سکه و پرداخت",
                    callback_data="admin:finance",
                    style="primary",
                )
            ],
            [
                glass_button(
                    "👥 موجودی کاربران",
                    callback_data="admin:users:page:0",
                    style="primary",
                ),
                glass_button(
                    "👮 مدیریت ادمین‌ها",
                    callback_data="admin:admins",
                    style="primary",
                ),
            ],
            [
                glass_button(
                    f"🖥 مدیریت سلف‌ها ({running_selfbots}/{len(selfbot_rows)})",
                    callback_data="admin:selfs:page:0",
                    style="success" if running_selfbots else "primary",
                )
            ],
            [
                glass_button(
                    "📢 تنظیم جوین اجباری",
                    callback_data="admin:join",
                    style="primary",
                ),
                glass_button(
                    "📝 پشتیبانی و قوانین",
                    callback_data="admin:content",
                    style="primary",
                ),
            ],
            [
                glass_button(
                    "🆔 تنظیم آیدی‌ها",
                    callback_data="admin:identities",
                    style="primary",
                ),
                glass_button(
                    "🤖 نام و منوی استارت",
                    callback_data="admin:startmenu",
                    style="primary",
                ),
            ],
            [
                glass_button(
                    "✏️ جایگزین Sourcekade",
                    callback_data=(
                        "admin:identities:set:brand_powered_by_username"
                    ),
                    style="success",
                )
            ],
            [
                glass_button(
                    "🔑 ثبت یا تغییر توکن هلپر",
                    callback_data="admin:helper:set",
                    style="primary",
                )
            ],
            [
                glass_button(
                    restart_label,
                    callback_data="admin:helper:restart",
                    style="success",
                )
            ],
        ]
        if running or config.get("enabled"):
            rows.append(
                [
                    glass_button(
                        "⏹ توقف هلپر",
                        callback_data="admin:helper:stop",
                        style="danger",
                    )
                ]
            )
        rows.append(
            [
                glass_button(
                    "🔄 بروزرسانی وضعیت",
                    callback_data="admin:home",
                    style="primary",
                )
            ]
        )
        return InlineKeyboardMarkup(rows)

    def create_admin_cancel_keyboard(self, action: str = "helper:cancel"):
        return InlineKeyboardMarkup(
            [[
                glass_button(
                    "❌ لغو",
                    callback_data=f"admin:{action}",
                    style="danger",
                )
            ]]
        )

    def create_force_join_admin_keyboard(self):
        config = get_force_join_config(USERS_DB)
        toggle_label = (
            "⛔ غیرفعال‌کردن جوین اجباری"
            if config["enabled"]
            else "✅ فعال‌کردن جوین اجباری"
        )
        toggle_style = "danger" if config["enabled"] else "success"
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        toggle_label,
                        callback_data="admin:join:toggle",
                        style=toggle_style,
                    )
                ],
                [
                    glass_button(
                        "✏️ ثبت یا تغییر کانال",
                        callback_data="admin:join:set",
                        style="primary",
                    )
                ],
                [
                    glass_button(
                        "🔙 بازگشت",
                        callback_data="admin:home",
                        style="primary",
                    )
                ],
            ]
        )

    @staticmethod
    def create_force_join_confirm_keyboard():
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        "✅ ذخیره و فعال‌سازی",
                        callback_data="admin:join:confirm",
                        style="success",
                    )
                ],
                [
                    glass_button(
                        "❌ لغو",
                        callback_data="admin:join:cancel",
                        style="danger",
                    )
                ],
            ]
        )

    def create_financial_settings_keyboard(self):
        rows = [
            [
                glass_button(
                    "💰 قیمت هر سکه",
                    callback_data="admin:finance:set:coin_price_toman",
                    style="primary",
                ),
                glass_button(
                    "💳 شماره کارت",
                    callback_data="admin:finance:set:payment_card_number",
                    style="primary",
                ),
            ],
            [
                glass_button(
                    "👤 نام صاحب کارت",
                    callback_data="admin:finance:set:payment_card_holder",
                    style="primary",
                ),
                glass_button(
                    "📨 آیدی نمایشی پرداخت",
                    callback_data="admin:finance:set:payment_contact_username",
                    style="primary",
                ),
            ],
            [
                glass_button(
                    "⚡ هزینه فعال‌سازی",
                    callback_data="admin:finance:set:activation_cost_coins",
                    style="primary",
                ),
                glass_button(
                    "📅 هزینه روزانه سلف",
                    callback_data="admin:finance:set:daily_self_cost_coins",
                    style="success",
                ),
            ],
            [
                glass_button(
                    "🎁 هدیه شروع",
                    callback_data="admin:finance:set:new_user_gift_coins",
                    style="primary",
                ),
                glass_button(
                    "🎫 پاداش دعوت",
                    callback_data="admin:finance:set:referral_reward_coins",
                    style="primary",
                ),
            ],
            [
                glass_button(
                    "🎲 کارمزد شرط‌بندی",
                    callback_data="admin:finance:set:betting_fee_percent",
                    style="success",
                )
            ],
            [
                glass_button(
                    "↘️ حداقل انتقال",
                    callback_data="admin:finance:set:transfer_min_coins",
                    style="primary",
                ),
                glass_button(
                    "↗️ حداکثر هر انتقال",
                    callback_data="admin:finance:set:transfer_max_coins",
                    style="primary",
                ),
            ],
            [
                glass_button(
                    "🗓 سقف انتقال روزانه",
                    callback_data=(
                        "admin:finance:set:transfer_daily_limit_coins"
                    ),
                    style="primary",
                ),
                glass_button(
                    "🛟 لینک پشتیبانی",
                    callback_data="admin:finance:set:support_url",
                    style="primary",
                ),
            ],
            [
                glass_button(
                    "🔙 بازگشت",
                    callback_data="admin:home",
                    style="primary",
                )
            ],
        ]
        return InlineKeyboardMarkup(rows)

    def financial_settings_text(self, notice: str | None = None) -> str:
        config = get_financial_config(USERS_DB)
        prefix = f"{notice}\n\n" if notice else ""
        card_number = (
            self.format_card_number(config["card_number"])
            if config["card_number"]
            else "ثبت نشده"
        )
        card_holder = config["card_holder"] or "ثبت نشده"
        contact = (
            f"@{config['payment_contact']}"
            if config["payment_contact"]
            else "ثبت نشده"
        )
        return (
            f"{prefix}"
            "💳 تنظیمات سکه و پرداخت\n\n"
            f"├ قیمت هر سکه: {config['coin_price']:,} تومان\n"
            f"├ شماره کارت: {card_number}\n"
            f"├ صاحب کارت: {card_holder}\n"
            f"├ آیدی نمایشی پرداخت: {contact}\n"
            f"├ هزینه فعال‌سازی: {config['activation_cost']} سکه\n"
            f"├ هزینه هر ۲۴ ساعت سلف: {config['daily_self_cost']} سکه\n"
            f"├ کارمزد شرط‌بندی: {config['betting_fee_percent']}٪ از جایزه\n"
            f"├ هدیه شروع: {config['new_user_gift']} سکه\n"
            f"├ پاداش دعوت: {config['referral_reward']} سکه\n"
            f"├ انتقال مجاز: {config['transfer_min']} تا "
            f"{config['transfer_max']} سکه در هر بار\n"
            f"├ سقف انتقال ۲۴ ساعته: "
            f"{config['transfer_daily_limit'] or 'نامحدود'} سکه\n"
            f"└ پشتیبانی: {config['support_url'] or 'ثبت نشده'}\n\n"
            "هر تغییر بلافاصله ذخیره می‌شود و پس از ری‌استارت باقی می‌ماند."
        )

    def create_content_settings_keyboard(self):
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        "🛟 لینک پشتیبانی",
                        callback_data="admin:content:set:support_url",
                        style="primary",
                    ),
                    glass_button(
                        "💬 متن پشتیبانی",
                        callback_data="admin:content:set:support_text",
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "📜 متن قوانین",
                        callback_data="admin:content:set:rules_text",
                        style="primary",
                    )
                ],
                [
                    glass_button(
                        "🔙 بازگشت",
                        callback_data="admin:home",
                        style="primary",
                    )
                ],
            ]
        )

    def content_settings_text(self, notice: str | None = None) -> str:
        config = get_content_config(USERS_DB)
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}"
            "📝 تنظیم پشتیبانی و قوانین\n\n"
            f"├ لینک پشتیبانی: {config['support_url'] or 'ثبت نشده'}\n"
            f"├ متن پشتیبانی: {len(config['support_text'])} نویسه\n"
            f"└ متن قوانین: {len(config['rules_text'])} نویسه\n\n"
            "متن‌ها و لینک بلافاصله در منوی کاربران بروزرسانی می‌شوند."
        )

    def start_menu_settings_text(self, notice: str | None = None) -> str:
        brand = get_brand_config(USERS_DB)
        buttons = self.list_custom_start_buttons()
        active_count = sum(bool(button["is_active"]) for button in buttons)
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}"
            "🤖 نام ربات و منوی استارت\n\n"
            f"├ نام ربات: {brand['bot_display_name']}\n"
            f"├ دکمه‌های فعال: {active_count:,}\n"
            f"└ کل دکمه‌ها: {len(buttons):,}\n\n"
            "دکمه لینک کاربر را به گروه، کانال یا سایت می‌برد. "
            "دکمه متنی، پاسخ تنظیم‌شده را داخل همان منو نمایش می‌دهد."
        )

    def create_start_menu_settings_keyboard(self):
        rows = [
            [
                glass_button(
                    "✏️ تنظیم نام ربات",
                    callback_data="admin:startmenu:name",
                    style="primary",
                )
            ],
            [
                glass_button(
                    "🔗 افزودن دکمه لینک",
                    callback_data="admin:startmenu:add:url",
                    style="success",
                ),
                glass_button(
                    "💬 افزودن دکمه متنی",
                    callback_data="admin:startmenu:add:text",
                    style="success",
                ),
            ],
        ]
        for button in self.list_custom_start_buttons()[:20]:
            icon = "🔗" if button["button_type"] == "url" else "💬"
            state = "🟢" if button["is_active"] else "⚪️"
            label = str(button["label"]).replace("\n", " ")[:32]
            rows.append(
                [
                    glass_button(
                        f"{state} {icon} {label}",
                        callback_data=f"admin:startmenu:view:{int(button['id'])}",
                        style="primary",
                    )
                ]
            )
        rows.append(
            [
                glass_button(
                    "🔙 بازگشت",
                    callback_data="admin:home",
                    style="primary",
                )
            ]
        )
        return InlineKeyboardMarkup(rows)

    def start_menu_button_detail_text(
        self,
        button,
        notice: str | None = None,
    ) -> str:
        prefix = f"{notice}\n\n" if notice else ""
        button_type = "لینک" if button["button_type"] == "url" else "متنی"
        status = "فعال" if button["is_active"] else "غیرفعال"
        payload = str(button["payload"])
        if len(payload) > 1200:
            payload = payload[:1200] + "…"
        return (
            f"{prefix}"
            f"🧩 دکمه «{button['label']}»\n\n"
            f"├ نوع: {button_type}\n"
            f"├ وضعیت: {status}\n"
            f"└ ترتیب: {int(button['position'])}\n\n"
            f"محتوا:\n{payload}"
        )

    @staticmethod
    def create_start_menu_button_detail_keyboard(button):
        toggle_label = (
            "⛔ غیرفعال‌کردن"
            if button["is_active"]
            else "✅ فعال‌کردن"
        )
        toggle_style = "danger" if button["is_active"] else "success"
        button_id = int(button["id"])
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        toggle_label,
                        callback_data=f"admin:startmenu:toggle:{button_id}",
                        style=toggle_style,
                    )
                ],
                [
                    glass_button(
                        "🗑 حذف دکمه",
                        callback_data=f"admin:startmenu:delete:{button_id}",
                        style="danger",
                    )
                ],
                [
                    glass_button(
                        "🔙 فهرست دکمه‌ها",
                        callback_data="admin:startmenu",
                        style="primary",
                    )
                ],
            ]
        )

    @staticmethod
    def create_start_menu_button_delete_keyboard(button_id: int):
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        "✅ بله، حذف شود",
                        callback_data=(
                            f"admin:startmenu:delete-confirm:{int(button_id)}"
                        ),
                        style="danger",
                    )
                ],
                [
                    glass_button(
                        "❌ لغو",
                        callback_data=f"admin:startmenu:view:{int(button_id)}",
                        style="primary",
                    )
                ],
            ]
        )

    def create_identity_settings_keyboard(self):
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        "🧾 مدیران رسید",
                        callback_data="admin:identities:set:receipt_admin_ids",
                        style="primary",
                    ),
                    glass_button(
                        "💳 آیدی پرداخت",
                        callback_data=(
                            "admin:identities:set:payment_contact_username"
                        ),
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "👤 مدیر سلف",
                        callback_data="admin:identities:set:self_admin_target",
                        style="primary",
                    ),
                    glass_button(
                        "👥 گروه گزارش",
                        callback_data="admin:identities:set:self_group_target",
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "📣 کانال سلف",
                        callback_data="admin:identities:set:self_channel_target",
                        style="primary",
                    ),
                    glass_button(
                        "✏️ جایگزین Sourcekade",
                        callback_data=(
                            "admin:identities:set:brand_powered_by_username"
                        ),
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "👑 آیدی مالک",
                        callback_data=(
                            "admin:identities:set:brand_owner_username"
                        ),
                        style="primary",
                    ),
                    glass_button(
                        "🤖 آیدی سلف",
                        callback_data=(
                            "admin:identities:set:brand_self_username"
                        ),
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "🔥 آیدی گروه نمایشی",
                        callback_data=(
                            "admin:identities:set:brand_group_username"
                        ),
                        style="primary",
                    )
                ],
                [
                    glass_button(
                        "🔙 بازگشت",
                        callback_data="admin:home",
                        style="primary",
                    )
                ],
            ]
        )

    @staticmethod
    def _display_username(value: str) -> str:
        return f"@{value}" if value else "ثبت نشده"

    @staticmethod
    def _display_powered_by(value: str) -> str:
        if not value:
            return "ثبت نشده"
        if value.startswith("@") or " " in value:
            return value
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", value):
            return f"@{value}"
        return value

    def identity_settings_text(self, notice: str | None = None) -> str:
        config = get_identity_config(USERS_DB)
        receipt_targets = (
            ", ".join(str(item) for item in config["receipt_admin_ids"])
            if config["receipt_admin_ids"]
            else "همه ادمین‌های پنل"
        )
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}"
            "🆔 تنظیم آیدی‌های تلگرام\n\n"
            f"├ مدیران دریافت رسید: {receipt_targets}\n"
            f"├ آیدی نمایشی پرداخت: "
            f"{self._display_username(config['payment_contact'])}\n"
            f"├ مدیر سلف: {config['self_admin_target'] or 'ثبت نشده'}\n"
            f"├ گروه گزارش: {config['self_group_target'] or 'ثبت نشده'}\n"
            f"├ کانال سلف: {config['self_channel_target'] or 'ثبت نشده'}\n"
            f"├ جایگزین Sourcekade: "
            f"{self._display_powered_by(config['brand_powered_by'])}\n"
            f"├ مالک نمایشی: {self._display_username(config['brand_owner'])}\n"
            f"├ ربات سلف: {self._display_username(config['brand_self'])}\n"
            f"└ گروه نمایشی: {self._display_username(config['brand_group'])}\n\n"
            "برای مقصد رسید، فقط ادمین‌های ثبت‌شده در پنل پذیرفته می‌شوند."
        )

    @staticmethod
    def receipt_status_label(status: str) -> str:
        return {
            "pending": "🟡 در انتظار",
            "approved": "✅ تأییدشده",
            "rejected": "❌ ردشده",
        }.get(status, status)

    def receipt_caption(
        self,
        receipt,
        notice: str | None = None,
    ) -> str:
        username = (
            f"@{receipt['username']}" if receipt["username"] else "ندارد"
        )
        full_name = " ".join(
            part
            for part in (receipt["first_name"], receipt["last_name"])
            if part
        ) or "ثبت نشده"
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}"
            f"🧾 رسید خرید #{int(receipt['id'])}\n\n"
            f"├ کاربر: {full_name}\n"
            f"├ نام کاربری: {username}\n"
            f"├ آیدی عددی: {int(receipt['user_id'])}\n"
            f"├ تعداد: {int(receipt['coin_amount']):,} سکه\n"
            f"├ مبلغ: {int(receipt['amount_toman']):,} تومان\n"
            f"├ قیمت واحد: {int(receipt['coin_price_toman']):,} تومان\n"
            f"├ وضعیت: {self.receipt_status_label(receipt['status'])}\n"
            f"└ زمان ثبت: {receipt['created_at']}"
        )

    @staticmethod
    def create_receipt_review_keyboard(receipt_id: int):
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        "✅ تأیید و واریز سکه",
                        callback_data=f"admin:receipt:approve:{int(receipt_id)}",
                        style="success",
                    )
                ],
                [
                    glass_button(
                        "❌ رد رسید",
                        callback_data=f"admin:receipt:reject:{int(receipt_id)}",
                        style="danger",
                    )
                ],
                [
                    glass_button(
                        "🔙 رسیدهای در انتظار",
                        callback_data="admin:receipts:page:0",
                        style="primary",
                    )
                ],
            ]
        )

    def pending_receipt_page(self, page: int, page_size: int = 8):
        page = max(0, int(page))
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM payment_receipts WHERE status = 'pending'"
                ).fetchone()[0]
            )
            last_page = max(0, (total - 1) // page_size)
            page = min(page, last_page)
            rows = conn.execute(
                '''SELECT r.id, r.user_id, r.coin_amount, r.amount_toman,
                          u.first_name, u.username
                   FROM payment_receipts AS r
                   LEFT JOIN users AS u ON u.user_id = r.user_id
                   WHERE r.status = 'pending'
                   ORDER BY r.id DESC
                   LIMIT ? OFFSET ?''',
                (int(page_size), int(page * page_size)),
            ).fetchall()
        return page, last_page, total, rows

    def create_pending_receipts_keyboard(self, page: int):
        page, last_page, _, receipts = self.pending_receipt_page(page)
        rows = []
        for receipt in receipts:
            display = (
                receipt["first_name"]
                or (
                    f"@{receipt['username']}"
                    if receipt["username"]
                    else str(receipt["user_id"])
                )
            )
            rows.append(
                [
                    glass_button(
                        (
                            f"🧾 #{int(receipt['id'])} | {str(display)[:14]} | "
                            f"{int(receipt['coin_amount']):,} سکه"
                        ),
                        callback_data=f"admin:receipt:view:{int(receipt['id'])}",
                        style="primary",
                    )
                ]
            )
        navigation = []
        if page > 0:
            navigation.append(
                glass_button(
                    "⬅️ قبلی",
                    callback_data=f"admin:receipts:page:{page - 1}",
                    style="primary",
                )
            )
        if page < last_page:
            navigation.append(
                glass_button(
                    "بعدی ➡️",
                    callback_data=f"admin:receipts:page:{page + 1}",
                    style="primary",
                )
            )
        if navigation:
            rows.append(navigation)
        rows.append(
            [
                glass_button(
                    "🔙 بازگشت",
                    callback_data="admin:home",
                    style="primary",
                )
            ]
        )
        return InlineKeyboardMarkup(rows)

    def pending_receipts_text(
        self,
        page: int,
        notice: str | None = None,
    ) -> str:
        page, last_page, total, _ = self.pending_receipt_page(page)
        prefix = f"{notice}\n\n" if notice else ""
        empty = "\n\nرسید در انتظاری وجود ندارد." if not total else ""
        return (
            f"{prefix}"
            "🧾 رسیدهای در انتظار\n\n"
            f"├ تعداد: {total:,}\n"
            f"└ صفحه: {page + 1} از {last_page + 1}"
            f"{empty}"
        )

    async def send_receipt_media(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        chat_id: int,
        receipt,
        notice: str | None = None,
    ) -> None:
        caption = self.receipt_caption(receipt, notice)
        keyboard = (
            self.create_receipt_review_keyboard(int(receipt["id"]))
            if receipt["status"] == "pending"
            else None
        )
        if receipt["file_type"] == "photo":
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=receipt["telegram_file_id"],
                caption=caption,
                reply_markup=keyboard,
            )
        else:
            await context.bot.send_document(
                chat_id=chat_id,
                document=receipt["telegram_file_id"],
                caption=caption,
                reply_markup=keyboard,
            )

    async def edit_receipt_review_message(
        self,
        query,
        receipt,
        notice: str,
    ) -> None:
        caption = self.receipt_caption(receipt, notice)
        if query.message and (query.message.photo or query.message.document):
            await query.edit_message_caption(caption=caption, reply_markup=None)
        else:
            await query.edit_message_text(text=caption, reply_markup=None)

    def user_balance_page(self, page: int, page_size: int = 8):
        page = max(0, int(page))
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            total_users = int(
                conn.execute(
                    "SELECT COUNT(*) FROM users WHERE user_id != ?",
                    (self.owner_id,),
                ).fetchone()[0]
            )
            total_coins = int(
                conn.execute(
                    "SELECT COALESCE(SUM(coins), 0) FROM users WHERE user_id != ?",
                    (self.owner_id,),
                ).fetchone()[0]
            )
            last_page = max(0, (total_users - 1) // page_size)
            page = min(page, last_page)
            rows = conn.execute(
                '''SELECT user_id, username, first_name, coins, phone, is_active
                   FROM users
                   WHERE user_id != ?
                   ORDER BY coins DESC, updated_at DESC, user_id
                   LIMIT ? OFFSET ?''',
                (self.owner_id, page_size, page * page_size),
            ).fetchall()
        return page, last_page, total_users, total_coins, rows

    def create_user_balances_keyboard(self, page: int):
        page, last_page, _, _, users = self.user_balance_page(page)
        rows = []
        for user in users:
            display_name = (
                user["first_name"]
                or (f"@{user['username']}" if user["username"] else "")
                or str(user["user_id"])
            )
            display_name = str(display_name).replace("\n", " ")[:18]
            rows.append(
                [
                    glass_button(
                        f"👤 {display_name} | {int(user['coins'] or 0):,} سکه",
                        callback_data=f"admin:user:{int(user['user_id'])}",
                        style="primary",
                    )
                ]
            )
        navigation = []
        if page > 0:
            navigation.append(
                glass_button(
                    "⬅️ قبلی",
                    callback_data=f"admin:users:page:{page - 1}",
                    style="primary",
                )
            )
        if page < last_page:
            navigation.append(
                glass_button(
                    "بعدی ➡️",
                    callback_data=f"admin:users:page:{page + 1}",
                    style="primary",
                )
            )
        if navigation:
            rows.append(navigation)
        rows.extend(
            [
                [
                    glass_button(
                        "🔎 جست‌وجوی کاربر",
                        callback_data="admin:cc:input:search",
                        style="primary",
                    )
                ],
                [
                    glass_button(
                        "🎁 اهدای سکه با آیدی",
                        callback_data="admin:users:gift",
                        style="success",
                    )
                ],
                [
                    glass_button(
                        "🔙 بازگشت",
                        callback_data="admin:home",
                        style="primary",
                    )
                ],
            ]
        )
        return InlineKeyboardMarkup(rows)

    def user_balances_text(
        self,
        page: int,
        notice: str | None = None,
    ) -> str:
        page, last_page, total_users, total_coins, _ = self.user_balance_page(page)
        financial = get_financial_config(USERS_DB)
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}"
            "👥 موجودی کاربران\n\n"
            f"├ تعداد کاربران: {total_users:,}\n"
            f"├ مجموع سکه‌ها: {total_coins:,}\n"
            f"├ ارزش کل: {total_coins * financial['coin_price']:,} تومان\n"
            f"└ صفحه: {page + 1} از {last_page + 1}\n\n"
            "برای مشاهده جزئیات و اهدای سکه، کاربر را انتخاب کنید."
        )

    def get_user_record(self, user_id: int):
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                '''SELECT user_id, username, first_name, last_name, phone,
                          coins, is_active, join_date, updated_at,
                          expiration_date
                   FROM users
                   WHERE user_id = ?''',
                (int(user_id),),
            ).fetchone()

    def user_detail_text(
        self,
        user_id: int,
        notice: str | None = None,
    ) -> str:
        user = self.get_user_record(user_id)
        if user is None:
            return "❌ کاربر پیدا نشد."
        price = get_financial_config(USERS_DB)["coin_price"]
        username = f"@{user['username']}" if user["username"] else "ندارد"
        full_name = " ".join(
            part for part in (user["first_name"], user["last_name"]) if part
        ) or "ثبت نشده"
        prefix = f"{notice}\n\n" if notice else ""
        self_record = self.get_selfbot_record(user_id)
        self_status = self.selfbot_status_info(self_record)[1]
        subscription = self.admin_store.active_subscription(user_id)
        subscription_label = (
            str(subscription["plan_name"]) if subscription else "بدون پلن"
        )
        expiration = (
            user["expiration_date"]
            if user["expiration_date"]
            else ("دائمی" if subscription else "ثبت نشده")
        )
        account_state = "فعال" if user["is_active"] else "مسدود"
        return (
            f"{prefix}"
            "👤 اطلاعات و موجودی کاربر\n\n"
            f"├ نام: {full_name}\n"
            f"├ نام کاربری: {username}\n"
            f"├ آیدی عددی: {int(user['user_id'])}\n"
            f"├ تلفن سلف: {user['phone'] or 'ثبت نشده'}\n"
            f"├ موجودی: {int(user['coins'] or 0):,} سکه\n"
            f"├ ارزش موجودی: {int(user['coins'] or 0) * price:,} تومان\n"
            f"├ حساب: {account_state}\n"
            f"├ اشتراک: {subscription_label}\n"
            f"├ انقضا: {expiration}\n"
            f"└ وضعیت سلف: {self_status}"
        )

    def create_user_detail_keyboard(self, user_id: int):
        user = self.get_user_record(user_id)
        is_active = bool(user and user["is_active"])
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        "🎁 اهدای سکه",
                        callback_data=f"admin:user:gift:{int(user_id)}",
                        style="success",
                    )
                ],
                [
                    glass_button(
                        "➕/➖ اصلاح موجودی",
                        callback_data=f"admin:cc:user:balance:{int(user_id)}",
                        style="primary",
                    ),
                    glass_button(
                        "💎 تخصیص اشتراک",
                        callback_data=f"admin:cc:user:plan:{int(user_id)}",
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "⛔ مسدودکردن" if is_active else "✅ آزادکردن",
                        callback_data=f"admin:cc:user:toggle:{int(user_id)}",
                        style="danger" if is_active else "success",
                    ),
                    glass_button(
                        "🤖 مشاهده سلف",
                        callback_data=f"admin:self:view:{int(user_id)}",
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "🔙 فهرست کاربران",
                        callback_data="admin:users:page:0",
                        style="primary",
                    )
                ],
            ]
        )

    def selfbot_status_info(self, record) -> tuple[str, str, int | None]:
        if record is None:
            return "missing", "⚪️ ثبت نشده", None
        pid = self.running_selfbot_pid(int(record["user_id"]), record)
        if pid:
            return "running", "🟢 در حال اجرا", pid
        if self.selfbot_is_expired(record):
            return "expired", "⏳ منقضی", None
        status = str(record["self_status"] or "").strip().lower()
        if status == "invalid_session":
            return "invalid_session", "🔐 سشن نامعتبر", None
        if status == "insufficient_balance":
            return "insufficient_balance", "💸 کمبود موجودی", None
        if status in {"starting", "restarting"}:
            return status, "🟡 در حال راه‌اندازی", None
        if int(record["self_enabled"] or 0):
            if status in {"error", "activation_failed"}:
                return "error", "🔴 خطای اجرا", None
            return "offline", "🟠 آفلاین؛ در صف بازیابی", None
        return "stopped", "⚪️ متوقف", None

    def selfbot_page(self, page: int, page_size: int = 6):
        rows = self.registered_selfbot_rows()
        total = len(rows)
        running = sum(
            self.running_selfbot_pid(int(row["user_id"]), row) is not None
            for row in rows
        )
        enabled = sum(int(row["self_enabled"] or 0) for row in rows)
        error_count = sum(
            self.selfbot_status_info(row)[0]
            in {"error", "invalid_session", "expired", "insufficient_balance"}
            for row in rows
        )
        last_page = max(0, (total - 1) // page_size)
        page = min(max(0, int(page)), last_page)
        selected = rows[page * page_size:(page + 1) * page_size]
        return (
            page,
            last_page,
            total,
            running,
            enabled,
            error_count,
            selected,
        )

    def selfbots_panel_text(
        self,
        page: int,
        notice: str | None = None,
    ) -> str:
        (
            page,
            last_page,
            total,
            running,
            enabled,
            error_count,
            _,
        ) = self.selfbot_page(page)
        watchdog_state = (
            "🟢 فعال"
            if self.self_watchdog_task and not self.self_watchdog_task.done()
            else "🔴 متوقف"
        )
        release = self.admin_store.release_summary(self.current_release)
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}"
            "🖥 مدیریت سلف‌ها\n\n"
            f"├ کل سلف‌های ثبت‌شده: {total:,}\n"
            f"├ در حال اجرا: {running:,}\n"
            f"├ روشن در Watchdog: {enabled:,}\n"
            f"├ نیازمند بررسی: {error_count:,}\n"
            f"├ وضعیت Watchdog: {watchdog_state}\n"
            f"├ بررسی خودکار: هر {SELF_WATCHDOG_INTERVAL} ثانیه\n"
            f"├ نسخه جاری: {self.current_release}\n"
            f"├ نیازمند آپدیت: {release['outdated']:,}\n"
            f"└ صفحه: {page + 1} از {last_page + 1}\n\n"
            "سلف موردنظر را برای مشاهده جزئیات، روشن‌کردن، توقف یا "
            "راه‌اندازی مجدد انتخاب کنید."
        )

    def create_selfbots_keyboard(self, page: int):
        (
            page,
            last_page,
            _,
            _,
            _,
            _,
            selfbots,
        ) = self.selfbot_page(page)
        rows = []
        status_icons = {
            "running": "🟢",
            "starting": "🟡",
            "restarting": "🟡",
            "offline": "🟠",
            "error": "🔴",
            "invalid_session": "🔐",
            "expired": "⏳",
            "insufficient_balance": "💸",
            "stopped": "⚪️",
        }
        for record in selfbots:
            status_key, _, _ = self.selfbot_status_info(record)
            display_name = (
                record["first_name"]
                or (
                    f"@{record['username']}"
                    if record["username"]
                    else ""
                )
                or str(record["user_id"])
            )
            display_name = str(display_name).replace("\n", " ")[:16]
            phone = str(record["phone"] or "")[-7:]
            rows.append(
                [
                    glass_button(
                        f"{status_icons.get(status_key, '⚪️')} "
                        f"{display_name} | {phone}",
                        callback_data=(
                            f"admin:self:view:{int(record['user_id'])}"
                        ),
                        style="primary",
                    )
                ]
            )
        navigation = []
        if page > 0:
            navigation.append(
                glass_button(
                    "⬅️ قبلی",
                    callback_data=f"admin:selfs:page:{page - 1}",
                    style="primary",
                )
            )
        if page < last_page:
            navigation.append(
                glass_button(
                    "بعدی ➡️",
                    callback_data=f"admin:selfs:page:{page + 1}",
                    style="primary",
                )
            )
        if navigation:
            rows.append(navigation)
        rows.extend(
            [
                [
                    glass_button(
                        "🔄 بررسی و بازیابی الآن",
                        callback_data=f"admin:selfs:refresh:{page}",
                        style="success",
                    )
                ],
                [
                    glass_button(
                        "♻️ آپدیت همه سلف‌های روشن",
                        callback_data=f"admin:selfs:update-all:{page}",
                        style="success",
                    )
                ],
                [
                    glass_button(
                        "🔙 بازگشت",
                        callback_data="admin:home",
                        style="primary",
                    )
                ],
            ]
        )
        return InlineKeyboardMarkup(rows)

    def selfbot_detail_text(
        self,
        user_id: int,
        notice: str | None = None,
    ) -> str:
        record = self.get_selfbot_record(user_id)
        if record is None or not record["phone"] or not record["session_file"]:
            return "❌ سلف ثبت‌شده‌ای برای این کاربر پیدا نشد."
        _, status_label, pid = self.selfbot_status_info(record)
        full_name = " ".join(
            item
            for item in (record["first_name"], record["last_name"])
            if item
        ) or "ثبت نشده"
        username = (
            f"@{record['username']}"
            if record["username"]
            else "ندارد"
        )
        desired = (
            "روشن؛ بازیابی خودکار"
            if int(record["self_enabled"] or 0)
            else "خاموش"
        )
        error = str(record["self_last_error"] or "").strip()
        if len(error) > 350:
            error = error[:347] + "..."
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}"
            "🖥 جزئیات سلف\n\n"
            f"├ مالک: {full_name}\n"
            f"├ نام کاربری: {username}\n"
            f"├ آیدی کاربر: {int(record['user_id'])}\n"
            f"├ شماره: {record['phone']}\n"
            f"├ وضعیت اجرا: {status_label}\n"
            f"├ حالت مدیریتی: {desired}\n"
            f"├ PID: {pid or 'ندارد'}\n"
            f"├ انقضا: {record['expiration_date'] or 'بدون انقضا'}\n"
            f"├ نسخه سلف: {record['self_version'] or 'قدیمی/نامشخص'}\n"
            f"├ نسخه قبلی: {record['self_previous_version'] or 'ندارد'}\n"
            f"├ آخرین آپدیت: "
            f"{record['self_last_updated_at'] or 'ثبت نشده'}\n"
            f"├ آخرین هزینه روزانه: "
            f"{record['self_last_billed_at'] or 'ثبت نشده'}\n"
            f"├ برداشت بعدی: "
            f"{record['self_next_billing_at'] or 'غیرفعال/ثبت نشده'}\n"
            f"├ آخرین اجرا: {record['self_last_started_at'] or 'ثبت نشده'}\n"
            f"├ تعداد بازیابی: {int(record['self_restart_count'] or 0):,}\n"
            f"└ خطای آخر: {error or 'ندارد'}"
        )

    def create_selfbot_detail_keyboard(self, user_id: int):
        record = self.get_selfbot_record(user_id)
        if record is None:
            return InlineKeyboardMarkup(
                [[
                    glass_button(
                        "🔙 فهرست سلف‌ها",
                        callback_data="admin:selfs:page:0",
                        style="primary",
                    )
                ]]
            )
        status_key, _, _ = self.selfbot_status_info(record)
        rows = []
        if status_key == "running":
            rows.append(
                [
                    glass_button(
                        "♻️ راه‌اندازی مجدد",
                        callback_data=f"admin:self:restart:{int(user_id)}",
                        style="success",
                    ),
                    glass_button(
                        "⏹ خاموش‌کردن",
                        callback_data=f"admin:self:stop:{int(user_id)}",
                        style="danger",
                    ),
                ]
            )
        elif status_key != "expired":
            rows.append(
                [
                    glass_button(
                        "▶️ روشن‌کردن",
                        callback_data=f"admin:self:start:{int(user_id)}",
                        style="success",
                    )
                ]
            )
            if int(record["self_enabled"] or 0):
                rows.append(
                    [
                        glass_button(
                            "⏹ لغو بازیابی خودکار",
                            callback_data=f"admin:self:stop:{int(user_id)}",
                            style="danger",
                        )
                    ]
                )
        rows.extend(
            [
                [
                    glass_button(
                        "⬆️ آپدیت سلف",
                        callback_data=f"admin:self:update:{int(user_id)}",
                        style="success",
                    ),
                    glass_button(
                        "🗑 حذف سلف",
                        callback_data=f"admin:self:delete:{int(user_id)}",
                        style="danger",
                    ),
                ],
                [
                    glass_button(
                        "🔄 بروزرسانی",
                        callback_data=f"admin:self:view:{int(user_id)}",
                        style="primary",
                    )
                ],
                [
                    glass_button(
                        "🔙 فهرست سلف‌ها",
                        callback_data="admin:selfs:page:0",
                        style="primary",
                    )
                ],
            ]
        )
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def create_selfbot_delete_keyboard(user_id: int):
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        "✅ بله، حذف و بایگانی شود",
                        callback_data=(
                            f"admin:self:delete-confirm:{int(user_id)}"
                        ),
                        style="danger",
                    )
                ],
                [
                    glass_button(
                        "❌ لغو",
                        callback_data=f"admin:self:view:{int(user_id)}",
                        style="primary",
                    )
                ],
            ]
        )

    def create_admins_keyboard(self, viewer_id: int):
        rows = []
        if self.is_owner(viewer_id):
            rows.append(
                [
                    glass_button(
                        "➕ افزودن ادمین",
                        callback_data="admin:admins:add",
                        style="success",
                    )
                ]
            )
            for admin_id in sorted(get_admin_ids(USERS_DB, self.owner_id)):
                if admin_id == self.owner_id:
                    continue
                rows.append(
                    [
                        glass_button(
                            f"❌ حذف ادمین {admin_id}",
                            callback_data=f"admin:admins:remove:{admin_id}",
                            style="danger",
                        )
                    ]
                )
        rows.append(
            [
                glass_button(
                    "🔙 بازگشت",
                    callback_data="admin:home",
                    style="primary",
                )
            ]
        )
        return InlineKeyboardMarkup(rows)

    def admins_panel_text(
        self,
        viewer_id: int,
        notice: str | None = None,
    ) -> str:
        admin_ids = sorted(get_admin_ids(USERS_DB, self.owner_id))
        lines = []
        for admin_id in admin_ids:
            user = self.get_user_record(admin_id)
            name = ""
            if user:
                name = (
                    f"@{user['username']}"
                    if user["username"]
                    else (user["first_name"] or "")
                )
            role = "مالک اصلی" if admin_id == self.owner_id else "ادمین"
            suffix = f" | {name}" if name else ""
            lines.append(f"• {admin_id} | {role}{suffix}")
        prefix = f"{notice}\n\n" if notice else ""
        permission_note = (
            "افزودن و حذف ادمین فقط توسط مالک اصلی انجام می‌شود."
            if not self.is_owner(viewer_id)
            else "ادمین‌های افزوده‌شده به پنل و امکانات مدیریتی دسترسی دارند."
        )
        return (
            f"{prefix}"
            "👮 مدیریت ادمین‌ها\n\n"
            + ("\n".join(lines) if lines else "ادمینی ثبت نشده است.")
            + f"\n\n{permission_note}\n"
            "همه ادمین‌ها از بررسی جوین اجباری معاف هستند."
        )

    def force_join_panel_text(self, notice: str | None = None) -> str:
        config = get_force_join_config(USERS_DB)
        status = "🟢 فعال" if config["enabled"] else "⚪️ غیرفعال"
        channel = (
            f"@{config['username']}"
            if config["username"]
            else config["chat_id"] or "ثبت نشده"
        )
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}"
            "📢 تنظیمات جوین اجباری\n\n"
            f"├ وضعیت: {status}\n"
            f"├ عنوان: {config['title']}\n"
            f"├ کانال: {channel}\n"
            f"└ لینک عضویت: {config['join_url'] or 'ثبت نشده'}\n\n"
            "برای بررسی عضویت کاربران، ربات اصلی باید در کانال ادمین باشد."
        )

    def admin_panel_text(self, notice: str | None = None) -> str:
        return self.control_center_text(notice)

    def _admin_panel_text_legacy(self, notice: str | None = None) -> str:
        config = get_helper_config(USERS_DB)
        force_join = get_force_join_config(USERS_DB)
        financial = get_financial_config(USERS_DB)
        brand = get_brand_config(USERS_DB)
        custom_button_count = len(
            self.list_custom_start_buttons(active_only=True)
        )
        admin_count = len(get_admin_ids(USERS_DB, self.owner_id))
        pending_receipts = self.pending_receipt_count()
        selfbot_rows = self.registered_selfbot_rows()
        running_selfbots = sum(
            self.running_selfbot_pid(int(row["user_id"]), row) is not None
            for row in selfbot_rows
        )
        desired_selfbots = sum(
            int(row["self_enabled"] or 0) for row in selfbot_rows
        )
        running = self.helper_is_running()
        ready = self.helper_is_ready()
        if ready:
            status = "🟢 فعال و در حال اجرا"
        elif running and config.get("enabled"):
            status = "🟡 در حال راه‌اندازی"
        elif running:
            status = "🟡 اجرا شده ولی غیرفعال"
        elif config.get("enabled"):
            status = "🔴 فعال است ولی اجرا نشده"
        else:
            status = "⚪️ متوقف"

        username = (
            f"@{config['username']}"
            if config.get("username")
            else "ثبت نشده"
        )
        token_status = "ثبت شده ✅" if config.get("token") else "ثبت نشده ❌"
        force_join_status = (
            f"فعال روی {force_join['title']}"
            if force_join["enabled"]
            else "غیرفعال"
        )
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}"
            "👑 پنل مدیریت اصلی\n\n"
            "📢 جوین اجباری\n"
            f"└ وضعیت: {force_join_status}\n\n"
            "💳 سکه و پرداخت\n"
            f"├ قیمت هر سکه: {financial['coin_price']:,} تومان\n"
            f"├ هزینه فعال‌سازی: {financial['activation_cost']} سکه\n"
            f"├ هزینه روزانه سلف: {financial['daily_self_cost']} سکه\n"
            f"├ کارمزد شرط‌بندی: {financial['betting_fee_percent']}٪\n"
            f"├ رسید در انتظار: {pending_receipts:,}\n"
            f"└ تعداد ادمین‌ها: {admin_count}\n\n"
            "🤖 منوی استارت\n"
            f"├ نام ربات: {brand['bot_display_name']}\n"
            f"└ دکمه سفارشی فعال: {custom_button_count:,}\n\n"
            "🖥 سلف‌ها\n"
            f"├ در حال اجرا: {running_selfbots:,} از {len(selfbot_rows):,}\n"
            f"├ بازیابی خودکار: {desired_selfbots:,}\n"
            f"└ Watchdog: هر {SELF_WATCHDOG_INTERVAL} ثانیه\n\n"
            "🤖 تنظیمات بات هلپر پنل سلف\n"
            f"├ وضعیت: {status}\n"
            f"├ نام کاربری: {username}\n"
            f"├ توکن: {token_status}\n"
            "└ حالت Inline: هنگام ثبت توکن بررسی می‌شود\n\n"
            "با تغییر هلپر، همه سلف‌های فعال و سلف‌های جدید در فراخوانی "
            "بعدی «پنل» از تنظیم جدید استفاده می‌کنند."
        )

    async def open_admin_panel(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        user_id = update.effective_user.id
        if not self.is_admin(user_id):
            await update.effective_message.reply_text(
                "❌ شما به پنل مدیریت اصلی دسترسی ندارید."
            )
            return

        if update.effective_chat.type != "private":
            await update.effective_message.reply_text(
                "🔐 تنظیم بات هلپر فقط در گفت‌وگوی خصوصی ربات انجام می‌شود."
            )
            return

        self.clear_admin_input(context)
        self.clear_force_join_draft(context)
        await update.effective_message.reply_text(
            self.admin_panel_text(),
            reply_markup=self.create_admin_keyboard(),
        )

    async def handle_admin_panel(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        query = update.callback_query
        if not self.is_admin(query.from_user.id):
            await query.answer("دسترسی ندارید.", show_alert=True)
            return
        if query.message and query.message.chat.type != "private":
            await query.answer(
                "تنظیم هلپر فقط در گفت‌وگوی خصوصی انجام می‌شود.",
                show_alert=True,
            )
            return

        action = query.data
        if action.startswith("admin:cc"):
            await self.handle_control_center_callback(query, context, action)
            return
        existing_permissions = (
            (("admin:users", "admin:user", "admin:gift"), "users"),
            (("admin:selfs", "admin:self:"), "selfs"),
            (("admin:receipts", "admin:receipt", "admin:finance"), "finance"),
            (
                (
                    "admin:admins",
                    "admin:join",
                    "admin:content",
                    "admin:identities",
                    "admin:startmenu",
                    "admin:helper",
                ),
                "settings",
            ),
        )
        for prefixes, permission in existing_permissions:
            if action.startswith(prefixes) and not self.cc_allowed(
                query.from_user.id, permission
            ):
                await query.answer(
                    "برای این بخش دسترسی ندارید.",
                    show_alert=True,
                )
                return
        await query.answer()

        if action == "admin:startmenu":
            self.clear_admin_input(context)
            self.clear_force_join_draft(context)
            await query.edit_message_text(
                self.start_menu_settings_text(),
                reply_markup=self.create_start_menu_settings_keyboard(),
            )
            return

        if action == "admin:startmenu:name":
            self.clear_admin_input(context)
            field = BRANDING_SETTING_FIELDS["bot_display_name"]
            context.user_data["awaiting_admin_setting"] = (
                "branding:bot_display_name"
            )
            await query.edit_message_text(
                f"✏️ تنظیم {field['label']}\n\n{field['prompt']}",
                reply_markup=self.create_admin_cancel_keyboard(
                    "startmenu:cancel"
                ),
            )
            return

        if action in {
            "admin:startmenu:add:url",
            "admin:startmenu:add:text",
        }:
            button_type = action.rsplit(":", 1)[1]
            self.clear_admin_input(context)
            context.user_data["start_button_draft"] = {
                "button_type": button_type,
            }
            context.user_data["awaiting_start_button_label"] = True
            type_label = "لینک" if button_type == "url" else "متنی"
            await query.edit_message_text(
                f"➕ افزودن دکمه {type_label}\n\n"
                "نامی که روی دکمه نمایش داده شود را ارسال کنید.",
                reply_markup=self.create_admin_cancel_keyboard(
                    "startmenu:cancel"
                ),
            )
            return

        if action == "admin:startmenu:cancel":
            self.clear_admin_input(context)
            await query.edit_message_text(
                self.start_menu_settings_text("عملیات لغو شد."),
                reply_markup=self.create_start_menu_settings_keyboard(),
            )
            return

        if action.startswith("admin:startmenu:view:"):
            try:
                button_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("شماره دکمه نامعتبر است.", show_alert=True)
                return
            button = self.get_custom_start_button(button_id)
            if button is None:
                await query.answer("دکمه پیدا نشد.", show_alert=True)
                return
            self.clear_admin_input(context)
            await query.edit_message_text(
                self.start_menu_button_detail_text(button),
                reply_markup=self.create_start_menu_button_detail_keyboard(
                    button
                ),
            )
            return

        if action.startswith("admin:startmenu:toggle:"):
            try:
                button_id = int(action.rsplit(":", 1)[1])
                is_active = self.toggle_custom_start_button(button_id)
            except (ValueError, LookupError):
                await query.answer("دکمه پیدا نشد.", show_alert=True)
                return
            button = self.get_custom_start_button(button_id)
            state = "فعال" if is_active else "غیرفعال"
            await query.edit_message_text(
                self.start_menu_button_detail_text(
                    button,
                    f"✅ دکمه {state} شد.",
                ),
                reply_markup=self.create_start_menu_button_detail_keyboard(
                    button
                ),
            )
            return

        if action.startswith("admin:startmenu:delete-confirm:"):
            try:
                button_id = int(action.rsplit(":", 1)[1])
                self.delete_custom_start_button(button_id)
            except (ValueError, LookupError):
                await query.answer("دکمه پیدا نشد.", show_alert=True)
                return
            await query.edit_message_text(
                self.start_menu_settings_text("✅ دکمه حذف شد."),
                reply_markup=self.create_start_menu_settings_keyboard(),
            )
            return

        if action.startswith("admin:startmenu:delete:"):
            try:
                button_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("شماره دکمه نامعتبر است.", show_alert=True)
                return
            button = self.get_custom_start_button(button_id)
            if button is None:
                await query.answer("دکمه پیدا نشد.", show_alert=True)
                return
            await query.edit_message_text(
                f"⚠️ دکمه «{button['label']}» حذف شود؟",
                reply_markup=self.create_start_menu_button_delete_keyboard(
                    button_id
                ),
            )
            return

        if action == "admin:content":
            self.clear_admin_input(context)
            self.clear_force_join_draft(context)
            await query.edit_message_text(
                self.content_settings_text(),
                reply_markup=self.create_content_settings_keyboard(),
            )
            return

        if action.startswith("admin:content:set:"):
            key = action.removeprefix("admin:content:set:")
            field = CONTENT_SETTING_FIELDS.get(key)
            if not field:
                await query.answer("تنظیم ناشناخته است.", show_alert=True)
                return
            self.clear_admin_input(context)
            context.user_data["awaiting_admin_setting"] = f"content:{key}"
            await query.edit_message_text(
                f"✏️ تنظیم {field['label']}\n\n{field['prompt']}",
                reply_markup=self.create_admin_cancel_keyboard("content:cancel"),
            )
            return

        if action == "admin:content:cancel":
            self.clear_admin_input(context)
            await query.edit_message_text(
                self.content_settings_text("عملیات لغو شد."),
                reply_markup=self.create_content_settings_keyboard(),
            )
            return

        if action == "admin:identities":
            self.clear_admin_input(context)
            self.clear_force_join_draft(context)
            await query.edit_message_text(
                self.identity_settings_text(),
                reply_markup=self.create_identity_settings_keyboard(),
            )
            return

        if action.startswith("admin:identities:set:"):
            key = action.removeprefix("admin:identities:set:")
            field = IDENTITY_SETTING_FIELDS.get(key)
            if not field:
                await query.answer("تنظیم ناشناخته است.", show_alert=True)
                return
            self.clear_admin_input(context)
            context.user_data["awaiting_admin_setting"] = f"identity:{key}"
            await query.edit_message_text(
                f"✏️ تنظیم {field['label']}\n\n{field['prompt']}",
                reply_markup=self.create_admin_cancel_keyboard(
                    "identities:cancel"
                ),
            )
            return

        if action == "admin:identities:cancel":
            self.clear_admin_input(context)
            await query.edit_message_text(
                self.identity_settings_text("عملیات لغو شد."),
                reply_markup=self.create_identity_settings_keyboard(),
            )
            return

        if action.startswith("admin:selfs:page:"):
            try:
                page = int(action.rsplit(":", 1)[1])
            except ValueError:
                page = 0
            self.clear_admin_input(context)
            self.clear_force_join_draft(context)
            await query.edit_message_text(
                self.selfbots_panel_text(page),
                reply_markup=self.create_selfbots_keyboard(page),
            )
            return

        if action.startswith("admin:selfs:refresh:"):
            try:
                page = int(action.rsplit(":", 1)[1])
            except ValueError:
                page = 0
            await self.reconcile_selfbots()
            await query.edit_message_text(
                self.selfbots_panel_text(
                    page,
                    "✅ وضعیت بررسی شد؛ سلف‌های خاموشِ مجاز وارد صف "
                    "بازیابی شدند.",
                ),
                reply_markup=self.create_selfbots_keyboard(page),
            )
            return

        if action.startswith("admin:selfs:update-all:"):
            if not self.is_owner(query.from_user.id):
                await query.answer(
                    "آپدیت سلف‌ها فقط در اختیار مالک اصلی است.",
                    show_alert=True,
                )
                return
            try:
                page = int(action.rsplit(":", 1)[1])
            except ValueError:
                page = 0
            candidates = [
                row
                for row in self.registered_selfbot_rows()
                if int(row["self_enabled"] or 0)
                and not self.selfbot_is_expired(row)
                and str(row["self_status"] or "") != "invalid_session"
            ]
            await query.edit_message_text(
                self.selfbots_panel_text(
                    page,
                    f"⏳ در حال آپدیت {len(candidates)} سلف روشن...",
                )
            )
            succeeded = 0
            failed = 0
            for record in candidates:
                target_user_id = int(record["user_id"])
                await self.stop_selfbot(
                    target_user_id,
                    disable=False,
                    status="updating",
                    detail=None,
                )
                self.update_selfbot_runtime(
                    target_user_id,
                    self_enabled=1,
                    self_status="restarting",
                    self_last_error=None,
                    self_next_restart_at=None,
                )
                success, _ = await self.launch_saved_selfbot(
                    target_user_id,
                    reason="admin_bulk_update",
                )
                if success:
                    succeeded += 1
                else:
                    failed += 1
            await query.edit_message_text(
                self.selfbots_panel_text(
                    page,
                    f"✅ آپدیت پایان یافت؛ موفق: {succeeded}، "
                    f"ناموفق: {failed}.",
                ),
                reply_markup=self.create_selfbots_keyboard(page),
            )
            return

        if action.startswith("admin:self:view:"):
            try:
                target_user_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("آیدی سلف نامعتبر است.", show_alert=True)
                return
            record = self.get_selfbot_record(target_user_id)
            if (
                record is None
                or not record["phone"]
                or not record["session_file"]
            ):
                await query.answer("سلف پیدا نشد.", show_alert=True)
                return
            await query.edit_message_text(
                self.selfbot_detail_text(target_user_id),
                reply_markup=self.create_selfbot_detail_keyboard(
                    target_user_id
                ),
            )
            return

        if (
            action.startswith("admin:self:start:")
            or action.startswith("admin:self:restart:")
            or action.startswith("admin:self:update:")
            or action.startswith("admin:self:stop:")
        ):
            operation = action.split(":")[2]
            if (
                operation == "update"
                and not self.is_owner(query.from_user.id)
            ):
                await query.answer(
                    "آپدیت سلف فقط در اختیار مالک اصلی است.",
                    show_alert=True,
                )
                return
            try:
                target_user_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("آیدی سلف نامعتبر است.", show_alert=True)
                return
            record = self.get_selfbot_record(target_user_id)
            if (
                record is None
                or not record["phone"]
                or not record["session_file"]
            ):
                await query.answer("سلف پیدا نشد.", show_alert=True)
                return

            if operation == "stop":
                await self.stop_selfbot(
                    target_user_id,
                    disable=True,
                    status="stopped",
                    detail=None,
                )
                notice = "⏹ سلف خاموش و بازیابی خودکار آن غیرفعال شد."
            else:
                if operation in {"restart", "update"}:
                    await query.edit_message_text(
                        self.selfbot_detail_text(
                            target_user_id,
                            (
                                "⏳ در حال اجرای نسخه جدید سلف..."
                                if operation == "update"
                                else "⏳ در حال راه‌اندازی مجدد سلف..."
                            ),
                        )
                    )
                    await self.stop_selfbot(
                        target_user_id,
                        disable=False,
                        status="restarting",
                        detail=None,
                    )
                else:
                    await query.edit_message_text(
                        self.selfbot_detail_text(
                            target_user_id,
                            "⏳ در حال روشن‌کردن سلف...",
                        )
                    )
                self.update_selfbot_runtime(
                    target_user_id,
                    self_enabled=1,
                    self_status="restarting",
                    self_last_error=None,
                    self_next_restart_at=None,
                )
                success, detail = await self.launch_saved_selfbot(
                    target_user_id,
                    reason=f"admin_{operation}",
                )
                notice = (
                    "✅ سلف با موفقیت راه‌اندازی شد."
                    if success
                    else f"❌ راه‌اندازی سلف ناموفق بود: {detail}"
                )
            await query.edit_message_text(
                self.selfbot_detail_text(target_user_id, notice),
                reply_markup=self.create_selfbot_detail_keyboard(
                    target_user_id
                ),
            )
            return

        if action.startswith("admin:self:delete-confirm:"):
            if not self.is_owner(query.from_user.id):
                await query.answer(
                    "حذف سلف فقط در اختیار مالک اصلی است.",
                    show_alert=True,
                )
                return
            try:
                target_user_id = int(action.rsplit(":", 1)[1])
                await self.delete_selfbot(target_user_id)
            except ValueError:
                await query.answer("آیدی سلف نامعتبر است.", show_alert=True)
                return
            except LookupError as exc:
                await query.answer(str(exc), show_alert=True)
                return
            await query.edit_message_text(
                self.selfbots_panel_text(
                    0,
                    "✅ سلف حذف شد؛ فایل‌های آن برای بازیابی احتمالی "
                    "بایگانی شدند و موجودی کاربر حفظ شد.",
                ),
                reply_markup=self.create_selfbots_keyboard(0),
            )
            return

        if action.startswith("admin:self:delete:"):
            if not self.is_owner(query.from_user.id):
                await query.answer(
                    "حذف سلف فقط در اختیار مالک اصلی است.",
                    show_alert=True,
                )
                return
            try:
                target_user_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("آیدی سلف نامعتبر است.", show_alert=True)
                return
            record = self.get_selfbot_record(target_user_id)
            if (
                record is None
                or not record["phone"]
                or not record["session_file"]
            ):
                await query.answer("سلف پیدا نشد.", show_alert=True)
                return
            await query.edit_message_text(
                "⚠️ با حذف سلف، پردازش آن متوقف می‌شود و سشن و تنظیمات "
                "اختصاصی از حالت فعال خارج می‌شوند.\n"
                "موجودی و حساب کاربر حذف نمی‌شود.\n\n"
                "آیا مطمئن هستید؟",
                reply_markup=self.create_selfbot_delete_keyboard(
                    target_user_id
                ),
            )
            return

        if action.startswith("admin:receipts:page:"):
            try:
                page = int(action.rsplit(":", 1)[1])
            except ValueError:
                page = 0
            self.clear_admin_input(context)
            await query.edit_message_text(
                self.pending_receipts_text(page),
                reply_markup=self.create_pending_receipts_keyboard(page),
            )
            return

        if action.startswith("admin:receipt:view:"):
            try:
                receipt_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("شماره رسید نامعتبر است.", show_alert=True)
                return
            receipt = self.get_payment_receipt(receipt_id)
            if receipt is None:
                await query.answer("رسید پیدا نشد.", show_alert=True)
                return
            try:
                await self.send_receipt_media(
                    context,
                    chat_id=query.from_user.id,
                    receipt=receipt,
                )
                notice = f"رسید #{receipt_id} در پیام جدید باز شد."
            except TelegramError:
                notice = f"❌ فایل رسید #{receipt_id} قابل ارسال نبود."
            await query.edit_message_text(
                self.pending_receipts_text(0, notice),
                reply_markup=self.create_pending_receipts_keyboard(0),
            )
            return

        if (
            action.startswith("admin:receipt:approve:")
            or action.startswith("admin:receipt:reject:")
        ):
            approve = action.startswith("admin:receipt:approve:")
            try:
                receipt_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("شماره رسید نامعتبر است.", show_alert=True)
                return
            try:
                status, new_balance, receipt_user_id = (
                    self.review_payment_receipt(
                        receipt_id=receipt_id,
                        admin_id=query.from_user.id,
                        approve=approve,
                    )
                )
            except (LookupError, ValueError) as exc:
                await query.answer(str(exc), show_alert=True)
                return
            receipt = self.get_payment_receipt(receipt_id)
            if receipt is None:
                await query.answer("رسید پیدا نشد.", show_alert=True)
                return
            if status not in {"approved", "rejected"}:
                await self.edit_receipt_review_message(
                    query,
                    receipt,
                    "ℹ️ این رسید قبلاً بررسی شده است.",
                )
                return
            if new_balance is None and approve:
                await self.edit_receipt_review_message(
                    query,
                    receipt,
                    "ℹ️ این رسید قبلاً بررسی شده است.",
                )
                return

            if approve and new_balance is not None:
                notice = (
                    f"✅ رسید توسط ادمین {query.from_user.id} تأیید و "
                    f"{int(receipt['coin_amount']):,} سکه واریز شد."
                )
                user_text = (
                    f"✅ رسید خرید #{receipt_id} تأیید شد.\n"
                    f"💰 {int(receipt['coin_amount']):,} سکه به کیف پول شما "
                    f"اضافه شد.\n"
                    f"👛 موجودی جدید: {new_balance:,} سکه"
                )
            else:
                notice = (
                    f"❌ رسید توسط ادمین {query.from_user.id} رد شد."
                )
                user_text = (
                    f"❌ رسید خرید #{receipt_id} توسط مدیریت رد شد.\n"
                    "برای پیگیری از بخش پشتیبانی استفاده کنید."
                )
            try:
                await context.bot.send_message(
                    chat_id=receipt_user_id,
                    text=user_text,
                )
            except TelegramError:
                notice += "\nاعلان برای کاربر ارسال نشد."
            await self.edit_receipt_review_message(query, receipt, notice)
            return

        if action == "admin:finance":
            self.clear_admin_input(context)
            self.clear_force_join_draft(context)
            await query.edit_message_text(
                self.financial_settings_text(),
                reply_markup=self.create_financial_settings_keyboard(),
            )
            return

        if action.startswith("admin:finance:set:"):
            key = action.removeprefix("admin:finance:set:")
            field = FINANCIAL_SETTING_FIELDS.get(key)
            if not field:
                await query.answer("تنظیم ناشناخته است.", show_alert=True)
                return
            self.clear_admin_input(context)
            self.clear_force_join_draft(context)
            context.user_data["awaiting_admin_setting"] = key
            await query.edit_message_text(
                f"✏️ تنظیم {field['label']}\n\n{field['prompt']}",
                reply_markup=self.create_admin_cancel_keyboard("finance:cancel"),
            )
            return

        if action == "admin:finance:cancel":
            self.clear_admin_input(context)
            await query.edit_message_text(
                self.financial_settings_text("عملیات لغو شد."),
                reply_markup=self.create_financial_settings_keyboard(),
            )
            return

        if action.startswith("admin:users:page:"):
            try:
                page = int(action.rsplit(":", 1)[1])
            except ValueError:
                page = 0
            self.clear_admin_input(context)
            self.clear_force_join_draft(context)
            await query.edit_message_text(
                self.user_balances_text(page),
                reply_markup=self.create_user_balances_keyboard(page),
            )
            return

        if action == "admin:users:gift":
            self.clear_admin_input(context)
            self.clear_force_join_draft(context)
            context.user_data["awaiting_gift_user_id"] = True
            await query.edit_message_text(
                "🎁 اهدای سکه\n\nآیدی عددی کاربر را ارسال کنید.",
                reply_markup=self.create_admin_cancel_keyboard("gift:cancel"),
            )
            return

        if action.startswith("admin:user:gift:"):
            try:
                target_user_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("آیدی کاربر نامعتبر است.", show_alert=True)
                return
            if self.get_user_record(target_user_id) is None:
                await query.answer("کاربر پیدا نشد.", show_alert=True)
                return
            self.clear_admin_input(context)
            context.user_data["gift_target_user_id"] = target_user_id
            context.user_data["awaiting_gift_amount"] = True
            await query.edit_message_text(
                self.user_detail_text(target_user_id)
                + "\n\nتعداد سکه هدیه را فقط به‌صورت عدد ارسال کنید.",
                reply_markup=self.create_admin_cancel_keyboard("gift:cancel"),
            )
            return

        if action.startswith("admin:user:"):
            try:
                target_user_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("آیدی کاربر نامعتبر است.", show_alert=True)
                return
            if self.get_user_record(target_user_id) is None:
                await query.answer("کاربر پیدا نشد.", show_alert=True)
                return
            self.clear_admin_input(context)
            await query.edit_message_text(
                self.user_detail_text(target_user_id),
                reply_markup=self.create_user_detail_keyboard(target_user_id),
            )
            return

        if action == "admin:gift:cancel":
            self.clear_admin_input(context)
            await query.edit_message_text(
                self.user_balances_text(0, "عملیات لغو شد."),
                reply_markup=self.create_user_balances_keyboard(0),
            )
            return

        if action == "admin:admins":
            self.clear_admin_input(context)
            self.clear_force_join_draft(context)
            await query.edit_message_text(
                self.admins_panel_text(query.from_user.id),
                reply_markup=self.create_admins_keyboard(query.from_user.id),
            )
            return

        if action == "admin:admins:add":
            if not self.is_owner(query.from_user.id):
                await query.answer(
                    "فقط مالک اصلی می‌تواند ادمین اضافه کند.",
                    show_alert=True,
                )
                return
            self.clear_admin_input(context)
            context.user_data["awaiting_new_admin_id"] = True
            await query.edit_message_text(
                "➕ افزودن ادمین\n\n"
                "آیدی عددی کاربر را ارسال کنید.\n"
                "کاربر باید قبلاً ربات را Start کرده باشد.",
                reply_markup=self.create_admin_cancel_keyboard("admins:cancel"),
            )
            return

        if action == "admin:admins:cancel":
            self.clear_admin_input(context)
            await query.edit_message_text(
                self.admins_panel_text(query.from_user.id, "عملیات لغو شد."),
                reply_markup=self.create_admins_keyboard(query.from_user.id),
            )
            return

        if action.startswith("admin:admins:remove-confirm:"):
            if not self.is_owner(query.from_user.id):
                await query.answer(
                    "فقط مالک اصلی می‌تواند ادمین حذف کند.",
                    show_alert=True,
                )
                return
            try:
                admin_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("آیدی ادمین نامعتبر است.", show_alert=True)
                return
            if admin_id == self.owner_id:
                await query.answer("مالک اصلی قابل حذف نیست.", show_alert=True)
                return
            remove_admin(USERS_DB, admin_id)
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text="⛔ دسترسی مدیریت شما توسط مالک اصلی حذف شد.",
                )
            except TelegramError:
                pass
            await query.edit_message_text(
                self.admins_panel_text(
                    query.from_user.id,
                    f"✅ دسترسی ادمین {admin_id} حذف شد.",
                ),
                reply_markup=self.create_admins_keyboard(query.from_user.id),
            )
            return

        if action.startswith("admin:admins:remove:"):
            if not self.is_owner(query.from_user.id):
                await query.answer(
                    "فقط مالک اصلی می‌تواند ادمین حذف کند.",
                    show_alert=True,
                )
                return
            try:
                admin_id = int(action.rsplit(":", 1)[1])
            except ValueError:
                await query.answer("آیدی ادمین نامعتبر است.", show_alert=True)
                return
            await query.edit_message_text(
                "⚠️ حذف دسترسی ادمین\n\n"
                f"آیا دسترسی ادمین {admin_id} حذف شود؟",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            glass_button(
                                "✅ بله، حذف شود",
                                callback_data=(
                                    f"admin:admins:remove-confirm:{admin_id}"
                                ),
                                style="danger",
                            )
                        ],
                        [
                            glass_button(
                                "❌ لغو",
                                callback_data="admin:admins:cancel",
                                style="primary",
                            )
                        ],
                    ]
                ),
            )
            return

        if action == "admin:join":
            context.user_data.pop("awaiting_helper_token", None)
            self.clear_force_join_draft(context)
            await query.edit_message_text(
                self.force_join_panel_text(),
                reply_markup=self.create_force_join_admin_keyboard(),
            )
            return

        if action == "admin:join:set":
            context.user_data.pop("awaiting_helper_token", None)
            self.clear_force_join_draft(context)
            context.user_data["awaiting_force_join_channel"] = True
            await query.edit_message_text(
                "1️⃣ آیدی کانال جوین اجباری را ارسال کنید.\n\n"
                "کانال عمومی: @channel_username یا "
                "https://t.me/channel_username\n"
                "کانال خصوصی: آیدی عددی مانند -1001234567890\n\n"
                "ربات اصلی باید از قبل در کانال ادمین باشد.",
                reply_markup=self.create_admin_cancel_keyboard("join:cancel"),
            )
            return

        if action == "admin:join:cancel":
            self.clear_force_join_draft(context)
            await query.edit_message_text(
                self.force_join_panel_text("عملیات لغو شد."),
                reply_markup=self.create_force_join_admin_keyboard(),
            )
            return

        if action == "admin:join:toggle":
            self.clear_force_join_draft(context)
            config = get_force_join_config(USERS_DB)
            if not config["configured"] and not config["enabled"]:
                await query.edit_message_text(
                    self.force_join_panel_text(
                        "❌ ابتدا کانال جوین اجباری را ثبت کنید."
                    ),
                    reply_markup=self.create_force_join_admin_keyboard(),
                )
                return
            new_enabled = "0" if config["enabled"] else "1"
            set_app_settings(
                USERS_DB,
                {"force_join_enabled": new_enabled},
            )
            state = "فعال" if new_enabled == "1" else "غیرفعال"
            await query.edit_message_text(
                self.force_join_panel_text(
                    f"✅ جوین اجباری {state} شد."
                ),
                reply_markup=self.create_force_join_admin_keyboard(),
            )
            return

        if action == "admin:join:confirm":
            draft = context.user_data.get("force_join_draft")
            if not isinstance(draft, dict) or not draft.get("join_url"):
                self.clear_force_join_draft(context)
                await query.edit_message_text(
                    self.force_join_panel_text(
                        "❌ اطلاعات موقت منقضی شده است؛ دوباره کانال را ثبت کنید."
                    ),
                    reply_markup=self.create_force_join_admin_keyboard(),
                )
                return
            set_app_settings(
                USERS_DB,
                {
                    "force_join_enabled": "1",
                    "force_join_chat_id": draft["chat_id"],
                    "force_join_username": draft.get("username", ""),
                    "force_join_title": draft.get("title", ""),
                    "force_join_url": draft["join_url"],
                },
            )
            self.admin_store.upsert_force_join_channel(
                draft["chat_id"],
                draft.get("username", ""),
                draft.get("title", "کانال عضویت اجباری"),
                draft["join_url"],
                query.from_user.id,
            )
            self.clear_force_join_draft(context)
            await query.edit_message_text(
                self.cc_forcejoin_view()[0],
                reply_markup=self.cc_forcejoin_view()[1],
            )
            return

        if action == "admin:helper:set":
            self.clear_force_join_draft(context)
            context.user_data["awaiting_helper_token"] = True
            await query.edit_message_text(
                "🔑 توکن بات هلپر را ارسال کنید.\n\n"
                "قبل از ارسال، در @BotFather برای همان بات دستور "
                "/setinline را فعال کنید. توکن بعد از دریافت از پیام حذف "
                "می‌شود و در پنل نمایش داده نخواهد شد.",
                reply_markup=self.create_admin_cancel_keyboard(),
            )
            return

        if action == "admin:helper:cancel":
            context.user_data.pop("awaiting_helper_token", None)
            await query.edit_message_text(
                self.admin_panel_text("عملیات لغو شد."),
                reply_markup=self.create_admin_keyboard(),
            )
            return

        if action == "admin:helper:stop":
            context.user_data.pop("awaiting_helper_token", None)
            self.stop_helper_process(disable=True)
            await query.edit_message_text(
                self.admin_panel_text("⏹ بات هلپر متوقف شد."),
                reply_markup=self.create_admin_keyboard(),
            )
            return

        if action == "admin:helper:restart":
            context.user_data.pop("awaiting_helper_token", None)
            config = get_helper_config(USERS_DB)
            if not config.get("token") or not config.get("username"):
                await query.edit_message_text(
                    self.admin_panel_text(
                        "❌ ابتدا توکن معتبر بات هلپر را ثبت کنید."
                    ),
                    reply_markup=self.create_admin_keyboard(),
                )
                return

            set_app_settings(USERS_DB, {"helper_enabled": "1"})
            success, detail = await self.start_helper_process(wait_for_ready=True)
            notice = (
                f"✅ هلپر @{config['username']} راه‌اندازی شد."
                if success
                else f"❌ راه‌اندازی هلپر ناموفق بود: {detail}"
            )
            await query.edit_message_text(
                self.admin_panel_text(notice),
                reply_markup=self.create_admin_keyboard(),
            )
            return

        context.user_data.pop("awaiting_helper_token", None)
        self.clear_force_join_draft(context)
        await query.edit_message_text(
            self.admin_panel_text(),
            reply_markup=self.create_admin_keyboard(),
        )

    async def receive_admin_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        # Channel posts and a few service updates do not have an effective
        # Telegram user. python-telegram-bot therefore exposes user_data as
        # None for those updates. This handler intentionally listens to all
        # non-command text messages, so reject such updates before touching
        # the per-user state.
        effective_user = update.effective_user
        effective_message = update.effective_message
        user_data = context.user_data
        if (
            effective_user is None
            or effective_message is None
            or user_data is None
        ):
            return

        if user_data.get("awaiting_support_message"):
            await self.receive_support_message(update, context)
            return

        if not self.is_admin(effective_user.id):
            return

        if user_data.get("awaiting_cc_action"):
            await self.receive_control_center_text(update, context)
            return

        awaiting_force_join = (
            user_data.get("awaiting_force_join_channel")
            or user_data.get("awaiting_force_join_link")
        )
        awaiting_start_button = (
            user_data.get("awaiting_start_button_label")
            or user_data.get("awaiting_start_button_payload")
        )
        if not (
            user_data.get("awaiting_helper_token")
            or awaiting_force_join
            or awaiting_start_button
            or user_data.get("awaiting_admin_setting")
            or user_data.get("awaiting_gift_user_id")
            or user_data.get("awaiting_gift_amount")
            or user_data.get("awaiting_new_admin_id")
            or user_data.get("awaiting_cc_action")
        ):
            return

        if awaiting_force_join:
            await self.receive_force_join_text(update, context)
            raise ApplicationHandlerStop

        if awaiting_start_button:
            await self.receive_start_button_text(update, context)
            raise ApplicationHandlerStop

        if user_data.get("awaiting_admin_setting"):
            await self.receive_financial_setting(update, context)
            raise ApplicationHandlerStop

        if (
            user_data.get("awaiting_gift_user_id")
            or user_data.get("awaiting_gift_amount")
        ):
            await self.receive_gift_input(update, context)
            raise ApplicationHandlerStop

        if user_data.get("awaiting_new_admin_id"):
            await self.receive_new_admin_id(update, context)
            raise ApplicationHandlerStop

        token = (effective_message.text or "").strip()
        try:
            await effective_message.delete()
        except TelegramError:
            pass

        if update.effective_chat.type != "private":
            context.user_data.pop("awaiting_helper_token", None)
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="❌ توکن فقط باید در گفت‌وگوی خصوصی ربات ارسال شود.",
            )
            raise ApplicationHandlerStop

        if token == self.token:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "❌ توکن ربات اصلی را نمی‌توان به‌عنوان هلپر استفاده کرد؛ "
                    "برای هلپر یک بات جداگانه بسازید."
                ),
                reply_markup=self.create_admin_cancel_keyboard(),
            )
            raise ApplicationHandlerStop

        try:
            async with Bot(token=token) as helper_bot:
                helper_user = await helper_bot.get_me()
        except (TelegramError, ValueError):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "❌ توکن بات هلپر معتبر نیست. توکن صحیح را دوباره "
                    "ارسال کنید."
                ),
                reply_markup=self.create_admin_cancel_keyboard(),
            )
            raise ApplicationHandlerStop

        if not helper_user.username:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ بات هلپر باید نام کاربری داشته باشد.",
                reply_markup=self.create_admin_cancel_keyboard(),
            )
            raise ApplicationHandlerStop

        if not helper_user.supports_inline_queries:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "❌ حالت Inline این بات فعال نیست.\n\n"
                    "در @BotFather دستور /setinline را برای همین بات فعال "
                    "کنید، سپس همین توکن را دوباره بفرستید."
                ),
                reply_markup=self.create_admin_cancel_keyboard(),
            )
            raise ApplicationHandlerStop

        set_app_settings(
            USERS_DB,
            {
                "helper_token": token,
                "helper_username": helper_user.username,
                "helper_bot_id": helper_user.id,
                "helper_enabled": "1",
            },
        )
        context.user_data.pop("awaiting_helper_token", None)
        success, detail = await self.start_helper_process(wait_for_ready=True)
        notice = (
            f"✅ بات هلپر @{helper_user.username} ثبت و راه‌اندازی شد."
            if success
            else (
                f"⚠️ توکن @{helper_user.username} ذخیره شد، اما اجرای هلپر "
                f"تأیید نشد: {detail}"
            )
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=self.admin_panel_text(notice),
            reply_markup=self.create_admin_keyboard(),
        )
        raise ApplicationHandlerStop

    async def receive_start_button_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if update.effective_chat.type != "private":
            self.clear_admin_input(context)
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="❌ تنظیم دکمه فقط در گفت‌وگوی خصوصی انجام می‌شود.",
            )
            return

        draft = context.user_data.get("start_button_draft")
        if not isinstance(draft, dict) or draft.get("button_type") not in {
            "url",
            "text",
        }:
            self.clear_admin_input(context)
            await update.effective_message.reply_text(
                "❌ اطلاعات موقت دکمه منقضی شده است؛ از پنل دوباره شروع کنید."
            )
            return

        raw = update.effective_message.text or ""
        if context.user_data.get("awaiting_start_button_label"):
            try:
                label = self.normalize_start_button_label(raw)
            except ValueError as exc:
                await update.effective_message.reply_text(
                    f"❌ {exc}\n\nنام دکمه را دوباره ارسال کنید.",
                    reply_markup=self.create_admin_cancel_keyboard(
                        "startmenu:cancel"
                    ),
                )
                return
            draft["label"] = label
            context.user_data.pop("awaiting_start_button_label", None)
            context.user_data["awaiting_start_button_payload"] = True
            if draft["button_type"] == "url":
                prompt = (
                    "حالا لینک دکمه را ارسال کنید.\n\n"
                    "نمونه گروه یا کانال: @username یا https://t.me/username\n"
                    "لینک دعوت خصوصی مانند https://t.me/+AbCdEf نیز "
                    "پذیرفته می‌شود."
                )
            else:
                prompt = (
                    "حالا متنی را بفرستید که پس از لمس دکمه به کاربر "
                    "نمایش داده شود."
                )
            await update.effective_message.reply_text(
                f"✅ نام دکمه: {label}\n\n{prompt}",
                reply_markup=self.create_admin_cancel_keyboard(
                    "startmenu:cancel"
                ),
            )
            return

        try:
            payload = self.normalize_start_button_payload(
                draft["button_type"],
                raw,
            )
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"❌ {exc}\n\nمحتوای دکمه را دوباره ارسال کنید.",
                reply_markup=self.create_admin_cancel_keyboard(
                    "startmenu:cancel"
                ),
            )
            return

        button_id = self.create_custom_start_button(
            label=draft["label"],
            button_type=draft["button_type"],
            payload=payload,
            created_by=update.effective_user.id,
        )
        self.clear_admin_input(context)
        await update.effective_message.reply_text(
            self.start_menu_settings_text(
                f"✅ دکمه «{draft['label']}» با شماره #{button_id} اضافه شد."
            ),
            reply_markup=self.create_start_menu_settings_keyboard(),
        )

    async def receive_financial_setting(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if update.effective_chat.type != "private":
            self.clear_admin_input(context)
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="❌ تنظیمات فقط در گفت‌وگوی خصوصی انجام می‌شود.",
            )
            return
        stored_key = str(context.user_data.get("awaiting_admin_setting") or "")
        if stored_key.startswith("content:"):
            section = "content"
            key = stored_key.removeprefix("content:")
            field = CONTENT_SETTING_FIELDS.get(key)
        elif stored_key.startswith("identity:"):
            section = "identity"
            key = stored_key.removeprefix("identity:")
            field = IDENTITY_SETTING_FIELDS.get(key)
        elif stored_key.startswith("branding:"):
            section = "branding"
            key = stored_key.removeprefix("branding:")
            field = BRANDING_SETTING_FIELDS.get(key)
        else:
            section = "finance"
            key = stored_key
            field = FINANCIAL_SETTING_FIELDS.get(key)
        if not field:
            self.clear_admin_input(context)
            await update.effective_message.reply_text(
                "❌ تنظیم موردنظر پیدا نشد؛ از پنل دوباره شروع کنید."
            )
            return
        try:
            if section == "content":
                normalized = self.normalize_content_setting(
                    key,
                    update.effective_message.text or "",
                )
            elif section == "identity":
                normalized = self.normalize_identity_setting(
                    key,
                    update.effective_message.text or "",
                )
            elif section == "branding":
                normalized = self.normalize_branding_setting(
                    key,
                    update.effective_message.text or "",
                )
            else:
                normalized = self.normalize_financial_setting(
                    key,
                    update.effective_message.text or "",
                )
        except ValueError as exc:
            cancel_action = {
                "content": "content:cancel",
                "identity": "identities:cancel",
                "branding": "startmenu:cancel",
                "finance": "finance:cancel",
            }[section]
            await update.effective_message.reply_text(
                f"❌ {exc}\n\n{field['prompt']}",
                reply_markup=self.create_admin_cancel_keyboard(cancel_action),
            )
            return
        if section == "finance" and key in {
            "transfer_min_coins",
            "transfer_max_coins",
        }:
            current = get_financial_config(USERS_DB)
            proposed_min = (
                int(normalized)
                if key == "transfer_min_coins"
                else current["transfer_min"]
            )
            proposed_max = (
                int(normalized)
                if key == "transfer_max_coins"
                else current["transfer_max"]
            )
            if proposed_min > proposed_max:
                await update.effective_message.reply_text(
                    "❌ حداقل انتقال نمی‌تواند بیشتر از حداکثر انتقال "
                    "باشد؛ مقدار دیگری بفرستید.",
                    reply_markup=self.create_admin_cancel_keyboard(
                        "finance:cancel"
                    ),
                )
                return
        set_app_settings(USERS_DB, {key: normalized})
        if section == "finance" and key == "daily_self_cost_coins":
            self.sync_daily_billing_schedule(int(normalized))
        profile_notice = ""
        if section == "branding":
            try:
                await context.bot.set_my_name(name=normalized)
            except (TelegramError, AttributeError):
                logging.exception("Could not update the official Telegram bot name")
                profile_notice = (
                    "\n⚠️ نام داخل منو ذخیره شد، اما تغییر نام رسمی تلگرام "
                    "انجام نشد؛ آن را از BotFather بررسی کنید."
                )
        self.clear_admin_input(context)
        notice = (
            f"✅ {field['label']} با موفقیت ذخیره شد."
            f"{profile_notice}"
        )
        if section == "content":
            text = self.content_settings_text(notice)
            keyboard = self.create_content_settings_keyboard()
        elif section == "identity":
            text = self.identity_settings_text(notice)
            keyboard = self.create_identity_settings_keyboard()
        elif section == "branding":
            text = self.start_menu_settings_text(notice)
            keyboard = self.create_start_menu_settings_keyboard()
        else:
            text = self.financial_settings_text(notice)
            keyboard = self.create_financial_settings_keyboard()
        await update.effective_message.reply_text(
            text,
            reply_markup=keyboard,
        )

    async def receive_gift_input(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if update.effective_chat.type != "private":
            self.clear_admin_input(context)
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="❌ اهدای سکه فقط در گفت‌وگوی خصوصی انجام می‌شود.",
            )
            return
        text = (update.effective_message.text or "").strip()
        normalized = text.translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        ).replace(",", "").replace("٬", "")

        if context.user_data.get("awaiting_gift_user_id"):
            if not normalized.isdigit() or int(normalized) <= 0:
                await update.effective_message.reply_text(
                    "❌ آیدی عددی معتبر نیست؛ دوباره ارسال کنید.",
                    reply_markup=self.create_admin_cancel_keyboard("gift:cancel"),
                )
                return
            target_user_id = int(normalized)
            if self.get_user_record(target_user_id) is None:
                await update.effective_message.reply_text(
                    "❌ این کاربر در ربات پیدا نشد.\n"
                    "کاربر باید ابتدا ربات را Start کند؛ سپس آیدی را بفرستید.",
                    reply_markup=self.create_admin_cancel_keyboard("gift:cancel"),
                )
                return
            context.user_data.pop("awaiting_gift_user_id", None)
            context.user_data["gift_target_user_id"] = target_user_id
            context.user_data["awaiting_gift_amount"] = True
            await update.effective_message.reply_text(
                self.user_detail_text(target_user_id)
                + "\n\nتعداد سکه هدیه را فقط به‌صورت عدد ارسال کنید.",
                reply_markup=self.create_admin_cancel_keyboard("gift:cancel"),
            )
            return

        target_user_id = context.user_data.get("gift_target_user_id")
        if not normalized.isdigit() or not 1 <= int(normalized) <= 1_000_000_000:
            await update.effective_message.reply_text(
                "❌ تعداد سکه باید عددی بین ۱ تا ۱,۰۰۰,۰۰۰,۰۰۰ باشد.",
                reply_markup=self.create_admin_cancel_keyboard("gift:cancel"),
            )
            return
        amount = int(normalized)
        try:
            new_balance = self.gift_user_coins(
                user_id=int(target_user_id),
                amount=amount,
                admin_id=update.effective_user.id,
            )
        except (ValueError, LookupError) as exc:
            self.clear_admin_input(context)
            await update.effective_message.reply_text(
                f"❌ {exc}",
                reply_markup=self.create_user_balances_keyboard(0),
            )
            return
        self.clear_admin_input(context)
        try:
            await context.bot.send_message(
                chat_id=int(target_user_id),
                text=(
                    f"🎁 {amount:,} سکه از طرف مدیریت به حساب شما اضافه شد.\n"
                    f"💰 موجودی جدید: {new_balance:,} سکه"
                ),
            )
            delivery_notice = ""
        except TelegramError:
            delivery_notice = "\nاعلان برای کاربر ارسال نشد، اما موجودی ذخیره شد."
        await update.effective_message.reply_text(
            self.user_detail_text(
                int(target_user_id),
                f"✅ {amount:,} سکه اهدا شد.{delivery_notice}",
            ),
            reply_markup=self.create_user_detail_keyboard(int(target_user_id)),
        )

    async def receive_new_admin_id(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.is_owner(update.effective_user.id):
            self.clear_admin_input(context)
            await update.effective_message.reply_text(
                "❌ فقط مالک اصلی می‌تواند ادمین اضافه کند."
            )
            return
        if update.effective_chat.type != "private":
            self.clear_admin_input(context)
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="❌ افزودن ادمین فقط در گفت‌وگوی خصوصی انجام می‌شود.",
            )
            return
        text = (update.effective_message.text or "").strip()
        normalized = text.translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        )
        if not normalized.isdigit() or int(normalized) <= 0:
            await update.effective_message.reply_text(
                "❌ آیدی عددی معتبر نیست؛ دوباره ارسال کنید.",
                reply_markup=self.create_admin_cancel_keyboard("admins:cancel"),
            )
            return
        admin_id = int(normalized)
        if admin_id == self.owner_id:
            self.clear_admin_input(context)
            await update.effective_message.reply_text(
                self.admins_panel_text(
                    update.effective_user.id,
                    "ℹ️ این آیدی مالک اصلی است و از قبل دسترسی کامل دارد.",
                ),
                reply_markup=self.create_admins_keyboard(update.effective_user.id),
            )
            return
        user = self.get_user_record(admin_id)
        if user is None:
            await update.effective_message.reply_text(
                "❌ کاربر در دیتابیس ربات پیدا نشد.\n"
                "ابتدا از کاربر بخواهید ربات را Start کند، سپس آیدی را بفرستید.",
                reply_markup=self.create_admin_cancel_keyboard("admins:cancel"),
            )
            return
        add_admin(USERS_DB, admin_id, update.effective_user.id)
        self.clear_admin_input(context)
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "✅ شما توسط مالک اصلی به‌عنوان ادمین ربات اضافه شدید.\n"
                    "برای ورود به پنل دستور /admin را بفرستید."
                ),
            )
        except TelegramError:
            pass
        await update.effective_message.reply_text(
            self.admins_panel_text(
                update.effective_user.id,
                f"✅ کاربر {admin_id} به ادمین‌ها اضافه شد.",
            ),
            reply_markup=self.create_admins_keyboard(update.effective_user.id),
        )

    async def receive_force_join_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        text = (update.effective_message.text or "").strip()
        if update.effective_chat.type != "private":
            self.clear_force_join_draft(context)
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text="❌ تنظیم جوین اجباری فقط در گفت‌وگوی خصوصی انجام می‌شود.",
            )
            return

        if context.user_data.get("awaiting_force_join_channel"):
            try:
                target = self.normalize_force_join_target(text)
            except ValueError as exc:
                await update.effective_message.reply_text(
                    f"❌ {exc}\n\nآیدی صحیح را دوباره ارسال کنید.",
                    reply_markup=self.create_admin_cancel_keyboard("join:cancel"),
                )
                return

            try:
                chat = await context.bot.get_chat(target)
                if chat.type not in {"channel", "supergroup"}:
                    await update.effective_message.reply_text(
                        "❌ مقصد باید کانال یا سوپرگروه باشد.",
                        reply_markup=self.create_admin_cancel_keyboard(
                            "join:cancel"
                        ),
                    )
                    return
                bot_user = await context.bot.get_me()
                bot_member = await context.bot.get_chat_member(
                    chat_id=chat.id,
                    user_id=bot_user.id,
                )
            except TelegramError:
                await update.effective_message.reply_text(
                    "❌ کانال پیدا نشد یا ربات به آن دسترسی ندارد.\n\n"
                    "ابتدا ربات اصلی را در کانال ادمین کنید، سپس آیدی را "
                    "دوباره بفرستید.",
                    reply_markup=self.create_admin_cancel_keyboard("join:cancel"),
                )
                return

            if bot_member.status not in {"administrator", "creator"}:
                await update.effective_message.reply_text(
                    "❌ ربات اصلی در این کانال ادمین نیست.\n\n"
                    "ربات را ادمین کنید و سپس آیدی کانال را دوباره بفرستید.",
                    reply_markup=self.create_admin_cancel_keyboard("join:cancel"),
                )
                return

            username = (chat.username or "").strip().lstrip("@")
            context.user_data["force_join_draft"] = {
                "chat_id": str(chat.id),
                "username": username,
                "title": (chat.title or "").strip() or "کانال عضویت اجباری",
                "default_url": (
                    f"https://t.me/{username}" if username else ""
                ),
            }
            context.user_data.pop("awaiting_force_join_channel", None)
            context.user_data["awaiting_force_join_link"] = True
            auto_hint = (
                "\nبرای استفاده از لینک عمومی کانال، کلمه «خودکار» را بفرستید."
                if username
                else ""
            )
            await update.effective_message.reply_text(
                "2️⃣ لینک عضویت کانال را ارسال کنید.\n\n"
                "برای کانال خصوصی، لینک دعوتی مانند "
                "https://t.me/+AbCdEf123 را بفرستید."
                f"{auto_hint}",
                reply_markup=self.create_admin_cancel_keyboard("join:cancel"),
            )
            return

        if context.user_data.get("awaiting_force_join_link"):
            draft = context.user_data.get("force_join_draft")
            if not isinstance(draft, dict):
                self.clear_force_join_draft(context)
                await update.effective_message.reply_text(
                    "❌ اطلاعات موقت منقضی شد. از پنل دوباره ثبت کانال را شروع کنید.",
                    reply_markup=self.create_force_join_admin_keyboard(),
                )
                return

            if text == "خودکار":
                join_url = str(draft.get("default_url") or "")
                if not join_url:
                    await update.effective_message.reply_text(
                        "❌ این کانال نام کاربری عمومی ندارد؛ لینک دعوت خصوصی "
                        "را ارسال کنید.",
                        reply_markup=self.create_admin_cancel_keyboard(
                            "join:cancel"
                        ),
                    )
                    return
            else:
                public_target = text.strip().lstrip("@")
                if re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_]{3,30}[A-Za-z0-9]",
                    public_target,
                ):
                    join_url = f"https://t.me/{public_target}"
                else:
                    join_url = text.strip()
                if not re.fullmatch(
                    r"https://t\.me/(?:"
                    r"\+[A-Za-z0-9_-]+|"
                    r"joinchat/[A-Za-z0-9_-]+|"
                    r"[A-Za-z][A-Za-z0-9_]{3,30}[A-Za-z0-9]"
                    r")/?",
                    join_url,
                    flags=re.IGNORECASE,
                ):
                    await update.effective_message.reply_text(
                        "❌ لینک معتبر نیست. لینک باید با https://t.me/ شروع "
                        "شود؛ دوباره ارسال کنید.",
                        reply_markup=self.create_admin_cancel_keyboard(
                            "join:cancel"
                        ),
                    )
                    return

            draft["join_url"] = join_url
            context.user_data.pop("awaiting_force_join_link", None)
            channel_label = (
                f"@{draft['username']}"
                if draft.get("username")
                else draft["chat_id"]
            )
            await update.effective_message.reply_text(
                "📋 تأیید نهایی جوین اجباری\n\n"
                f"├ عنوان: {draft['title']}\n"
                f"├ کانال: {channel_label}\n"
                f"└ لینک عضویت: {join_url}\n\n"
                "اطلاعات بالا ذخیره شود؟",
                reply_markup=self.create_force_join_confirm_keyboard(),
            )

    def stop_helper_process(self, *, disable: bool) -> None:
        config = get_helper_config(USERS_DB)
        candidate_pids = set()
        if self.helper_process:
            candidate_pids.add(self.helper_process.pid)
        if config.get("pid"):
            candidate_pids.add(config["pid"])

        processes = []
        for pid in candidate_pids:
            try:
                process = psutil.Process(pid)
                command = " ".join(process.cmdline())
                if HELPER_BOT_SCRIPT.name not in command:
                    continue
                process.terminate()
                processes.append(process)
            except (psutil.Error, OSError):
                continue

        if processes:
            _, alive = psutil.wait_procs(processes, timeout=5)
            for process in alive:
                try:
                    process.kill()
                except psutil.Error:
                    pass

        self.helper_process = None
        values = {"helper_pid": ""}
        if disable:
            values["helper_enabled"] = "0"
        set_app_settings(USERS_DB, values)
        HELPER_STATUS_FILE.unlink(missing_ok=True)

    def launch_helper_process(self):
        config = get_helper_config(USERS_DB)
        if (
            not config.get("enabled")
            or not config.get("token")
            or not config.get("username")
        ):
            raise RuntimeError("تنظیمات بات هلپر کامل یا فعال نیست.")
        if not HELPER_BOT_SCRIPT.is_file():
            raise FileNotFoundError("فایل helper_bot.py پیدا نشد.")

        self.stop_helper_process(disable=False)
        HELPER_STATUS_FILE.unlink(missing_ok=True)
        child_env = os.environ.copy()
        child_env["BOT_DATA_DIR"] = str(DATA_DIR)
        process = subprocess.Popen(
            [
                sys.executable,
                str(HELPER_BOT_SCRIPT),
                "--data-dir",
                str(DATA_DIR),
                "--status-file",
                str(HELPER_STATUS_FILE),
            ],
            cwd=str(BASE_DIR),
            env=child_env,
            start_new_session=True,
        )
        self.helper_process = process
        set_app_settings(USERS_DB, {"helper_pid": process.pid})
        return process

    async def _start_helper_process_unlocked(
        self,
        *,
        wait_for_ready: bool,
    ) -> tuple[bool, str]:
        try:
            process = self.launch_helper_process()
        except Exception as exc:
            logging.exception("Could not launch helper bot")
            return False, str(exc)

        if not wait_for_ready:
            return True, "فرایند هلپر اجرا شد."

        loop = asyncio.get_running_loop()
        deadline = loop.time() + HELPER_START_TIMEOUT
        last_detail = "پاسخ آماده‌بودن دریافت نشد."

        while loop.time() < deadline:
            if HELPER_STATUS_FILE.exists():
                try:
                    status = json.loads(
                        HELPER_STATUS_FILE.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    status = {}
                if status.get("detail"):
                    last_detail = str(status["detail"])
                if status.get("status") == "ready":
                    return True, "آماده"
                if status.get("status") == "failed":
                    break

            if process.poll() is not None:
                last_detail = (
                    f"فرایند هلپر با کد {process.returncode} متوقف شد."
                )
                break
            await asyncio.sleep(0.25)

        self.stop_helper_process(disable=False)
        return False, last_detail

    async def start_helper_process(
        self,
        *,
        wait_for_ready: bool,
    ) -> tuple[bool, str]:
        async with self.helper_operation_lock:
            return await self._start_helper_process_unlocked(
                wait_for_ready=wait_for_ready,
            )

    async def reconcile_helper_process(self) -> None:
        """Keep the configured Bot API helper alive without duplicate pollers."""
        config = get_helper_config(USERS_DB)
        if not config.get("enabled"):
            return
        if not config.get("token") or not config.get("username"):
            logging.error(
                "Helper watchdog cannot start the helper: configuration "
                "is incomplete."
            )
            return
        if self.helper_is_running():
            return

        success, detail = await self.start_helper_process(
            wait_for_ready=True,
        )
        if success:
            logging.info(
                "Helper watchdog restored @%s.",
                config["username"],
            )
        else:
            logging.error(
                "Helper watchdog restart failed for @%s: %s",
                config["username"],
                detail,
            )

    async def helper_watchdog_loop(self) -> None:
        while True:
            try:
                await self.reconcile_helper_process()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Helper watchdog iteration failed")
            await asyncio.sleep(HELPER_WATCHDOG_INTERVAL)

    async def post_init(self, application: Application) -> None:
        self.sync_daily_billing_schedule(
            get_financial_config(USERS_DB)["daily_self_cost"]
        )
        await self.process_daily_self_billing()
        await self.reconcile_selfbots()
        await self.reconcile_helper_process()
        await self.expire_waiting_games(application.bot)
        await self.retry_unsynced_game_results(application.bot)
        await self.retry_unsynced_game_closures(application.bot)
        if (
            self.game_cleanup_task is None
            or self.game_cleanup_task.done()
        ):
            self.game_cleanup_task = asyncio.create_task(
                self.betting_cleanup_loop(application.bot),
                name="betting-cleanup",
            )
        if self.self_watchdog_task is None or self.self_watchdog_task.done():
            self.self_watchdog_task = asyncio.create_task(
                self.selfbot_watchdog_loop(),
                name="selfbot-watchdog",
            )
        if (
            self.helper_watchdog_task is None
            or self.helper_watchdog_task.done()
        ):
            self.helper_watchdog_task = asyncio.create_task(
                self.helper_watchdog_loop(),
                name="helper-watchdog",
            )
        if (
            self.admin_broadcast_task is None
            or self.admin_broadcast_task.done()
        ):
            self.admin_broadcast_task = asyncio.create_task(
                self.broadcast_worker_loop(application),
                name="admin-broadcast-worker",
            )

    async def post_shutdown(self, application: Application) -> None:
        if self.game_cleanup_task and not self.game_cleanup_task.done():
            self.game_cleanup_task.cancel()
        if self.self_watchdog_task and not self.self_watchdog_task.done():
            self.self_watchdog_task.cancel()
        if (
            self.helper_watchdog_task
            and not self.helper_watchdog_task.done()
        ):
            self.helper_watchdog_task.cancel()
        if (
            self.admin_broadcast_task
            and not self.admin_broadcast_task.done()
        ):
            self.admin_broadcast_task.cancel()
        restart_tasks = [
            task
            for task in self.self_restart_tasks.values()
            if not task.done()
        ]
        for task in restart_tasks:
            task.cancel()
        pending = [
            task
            for task in (
                self.game_cleanup_task,
                self.self_watchdog_task,
                self.helper_watchdog_task,
                self.admin_broadcast_task,
                *restart_tasks,
            )
            if task is not None
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.game_cleanup_task = None
        self.self_watchdog_task = None
        self.helper_watchdog_task = None
        self.admin_broadcast_task = None
        self.self_restart_tasks.clear()
        self.stop_helper_process(disable=False)
    
    def create_welcome_keyboard(self):
        channels = self.admin_store.active_force_join_channels()
        keyboard = []
        for channel in channels:
            keyboard.append(
                [
                    glass_button(
                        f"📥 {str(channel['title'])[:28]}",
                        url=channel["join_url"],
                        style="primary",
                    )
                ]
            )
        keyboard.append(
            [glass_button("✅ بررسی عضویت", callback_data="check", style="success")]
        )
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def activation_menu_text() -> str:
        bot_name = get_brand_config(USERS_DB)["bot_display_name"]
        return (
            f"✨ به «{bot_name}» خوش آمدید\n"
            "━━━━━━━━━━━━━━\n\n"
            "از اینجا می‌توانید سلف را فعال کنید، موجودی را ببینید یا "
            "راهنمای کامل پنل را باز کنید.\n\n"
            "یکی از گزینه‌های زیر را انتخاب کنید 👇"
        )

    def create_activation_keyboard(self):
        keyboard = [
            [
                glass_button(
                    "🚀 فعال‌سازی سلف",
                    callback_data="activate",
                    style="success",
                ),
            ],
            [
                glass_button(
                    "👛 کیف پول",
                    callback_data="wallet",
                    style="success",
                ),
                glass_button(
                    "💰 خرید سکه",
                    callback_data="buy_coins",
                    style="primary",
                ),
            ],
            [
                glass_button(
                    "📊 آمار",
                    callback_data="stats",
                    style="primary",
                ),
                glass_button(
                    "🎫 لینک دعوت",
                    callback_data="invite",
                    style="primary",
                ),
            ],
            [
                glass_button(
                    "🎛 راهنمای پنل سلف",
                    callback_data="panel_help",
                    style="primary",
                ),
            ],
        ]
        for button in self.list_custom_start_buttons(active_only=True):
            if button["button_type"] == "url":
                custom_button = glass_button(
                    button["label"],
                    url=button["payload"],
                    style="primary",
                )
            else:
                custom_button = glass_button(
                    button["label"],
                    callback_data=f"start_button:{int(button['id'])}",
                    style="primary",
                )
            keyboard.append([custom_button])
        keyboard.append(
            [
                glass_button(
                    "🛟 پشتیبانی",
                    callback_data="support",
                    style="primary",
                ),
                glass_button(
                    "📜 قوانین",
                    callback_data="rules",
                    style="primary",
                ),
            ]
        )
        return InlineKeyboardMarkup(keyboard)

    def create_wallet_keyboard(self):
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        "💳 افزایش موجودی",
                        callback_data="buy_coins",
                        style="success",
                    )
                ],
                [
                    glass_button(
                        "🧾 سوابق رسیدها",
                        callback_data="wallet_history",
                        style="primary",
                    ),
                    glass_button(
                        "📜 قوانین",
                        callback_data="rules",
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "🔙 بازگشت",
                        callback_data="back",
                        style="primary",
                    )
                ],
            ]
        )

    @staticmethod
    def create_receipt_upload_keyboard():
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        "❌ لغو خرید",
                        callback_data="receipt_cancel",
                        style="danger",
                    )
                ]
            ]
        )

    def wallet_text(self, user_id: int) -> str:
        coins = int(self.user_coins.get(int(user_id), 0))
        financial = get_financial_config(USERS_DB)
        pending = self.user_pending_receipt_count(user_id)
        return (
            "👛 کیف پول شما\n\n"
            f"├ موجودی: {coins:,} سکه\n"
            f"├ ارزش فعلی: {coins * financial['coin_price']:,} تومان\n"
            f"├ قیمت هر سکه: {financial['coin_price']:,} تومان\n"
            f"└ رسید در انتظار: {pending:,}\n\n"
            "برای شارژ کیف پول، تعداد سکه را انتخاب و رسید پرداخت را "
            "همین‌جا برای ربات ارسال کنید."
        )

    def wallet_history_text(self, user_id: int) -> str:
        receipts = self.user_receipt_history(user_id)
        if not receipts:
            body = "هنوز رسیدی ثبت نکرده‌اید."
        else:
            body = "\n".join(
                (
                    f"• #{int(item['id'])} | "
                    f"{int(item['coin_amount']):,} سکه | "
                    f"{int(item['amount_toman']):,} تومان | "
                    f"{self.receipt_status_label(item['status'])}"
                )
                for item in receipts
            )
        return f"🧾 سوابق رسیدهای شما\n\n{body}"

    def create_stats_keyboard(self):
        keyboard = [
            [
                glass_button(
                    "💳 افزایش موجودی",
                    callback_data="buy_coins",
                    style="success",
                ),
                glass_button(
                    "🎫 لینک دعوت",
                    callback_data="invite",
                    style="primary",
                ),
            ],
            [
                glass_button("🔙 بازگشت", callback_data="back", style="primary"),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    def create_invite_keyboard(self):
        keyboard = [
            [
                glass_button(
                    "📊 آمار دعوت",
                    callback_data="stats",
                    style="primary",
                ),
                glass_button(
                    "💳 خرید سکه",
                    callback_data="buy_coins",
                    style="success",
                ),
            ],
            [
                glass_button("🔙 بازگشت", callback_data="back", style="primary"),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    @staticmethod
    def create_phone_keyboard():
        """کیبورد امن دریافت شماره؛ فقط در گفت‌وگوی خصوصی نمایش داده می‌شود."""
        return ReplyKeyboardMarkup(
            [
                [
                    KeyboardButton(
                        "📱 ارسال شماره من",
                        request_contact=True,
                    )
                ],
                ["🔙 بازگشت به منوی اصلی"],
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
            input_field_placeholder="شماره خودتان را ارسال کنید",
        )
    
    def create_code_keyboard(self, current_code=""):
        entered_count = min(len(str(current_code or "")), 5)
        display_code = f"{entered_count}/5 رقم"
        
        keyboard = [
            [
                InlineKeyboardButton(
                    f"🔢 کد فعلی: {display_code}",
                    callback_data="display",
                ),
            ],
            [
                InlineKeyboardButton("1", callback_data="1"),
                InlineKeyboardButton("2", callback_data="2"),
                InlineKeyboardButton("3", callback_data="3"),
            ],
            [
                InlineKeyboardButton("4", callback_data="4"),
                InlineKeyboardButton("5", callback_data="5"),
                InlineKeyboardButton("6", callback_data="6"),
            ],
            [
                InlineKeyboardButton("7", callback_data="7"),
                InlineKeyboardButton("8", callback_data="8"),
                InlineKeyboardButton("9", callback_data="9"),
            ],
            [
                InlineKeyboardButton(
                    "🗑️ حذف",
                    callback_data="delete",
                ),
                InlineKeyboardButton("0", callback_data="0"),
                InlineKeyboardButton(
                    "✅ تایید",
                    callback_data="submit",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ لغو ورود",
                    callback_data="login_cancel",
                )
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def normalize_verification_code(value: str) -> str:
        """Return a five-digit ASCII login code or an empty string."""
        translated = str(value or "").translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        )
        compact = re.sub(r"[\s\-]+", "", translated)
        return compact if re.fullmatch(r"[0-9]{5}", compact) else ""

    async def send_code_entry_prompt(
        self,
        source_message,
        processing_message,
    ):
        """Show the login-code step without relying on editing one message."""
        code_message = (
            "✅ کد ورود توسط تلگرام ارسال شد.\n\n"
            "🔐 ورود کد تأیید\n"
            "━━━━━━━━━━━━━━\n\n"
            "کد ۵ رقمی را با دکمه‌های زیر وارد کنید یا همان کد را به‌صورت "
            "یک پیام متنی بفرستید؛ سپس «✅ تایید» را بزنید.\n\n"
            "⏱ این کد زمان محدودی اعتبار دارد.\n"
            "⚠️ کد ورود را برای هیچ فرد دیگری ارسال نکنید."
        )

        # Editing the progress message is cosmetic. Some Telegram/local Bot API
        # combinations reject that edit even though the login code was sent.
        # The actual code prompt is therefore always sent as a fresh message.
        try:
            await processing_message.edit_text("✅ کد ورود ارسال شد.")
        except Exception:
            logging.warning(
                "Could not edit verification progress message",
                exc_info=True,
            )

        return await source_message.reply_text(
            code_message,
            reply_markup=self.create_code_keyboard(),
        )
    
    def create_coin_keyboard(self, current_amount=""):
        display_amount = current_amount if current_amount else "0"
        
        keyboard = [
            [
                glass_button(
                    f"💌 تعداد سکه: {display_amount}",
                    callback_data="display_coins",
                    style="primary",
                ),
            ],
            [
                glass_button("1", callback_data="coin_1"),
                glass_button("2", callback_data="coin_2"),
                glass_button("3", callback_data="coin_3"),
            ],
            [
                glass_button("4", callback_data="coin_4"),
                glass_button("5", callback_data="coin_5"),
                glass_button("6", callback_data="coin_6"),
            ],
            [
                glass_button("7", callback_data="coin_7"),
                glass_button("8", callback_data="coin_8"),
                glass_button("9", callback_data="coin_9"),
            ],
            [
                glass_button(
                    "🗑️ حذف",
                    callback_data="coin_delete",
                    style="danger",
                ),
                glass_button("0", callback_data="coin_0"),
                glass_button(
                    "✅ تایید",
                    callback_data="coin_submit",
                    style="success",
                ),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    def create_game_keyboard(self, game_id):
        keyboard = [
            [
                glass_button(
                    "🎮 شرکت در بازی",
                    callback_data=f"join_game:{game_id}",
                    style="success",
                ),
            ],
            [
                glass_button(
                    "❌ لغو بازی توسط سازنده",
                    callback_data=f"cancel_game:{game_id}",
                    style="danger",
                ),
            ],
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def create_balance_keyboard(user_id: int, balance: int):
        return InlineKeyboardMarkup(
            [[
                glass_button(
                    f"💰 موجودی: {int(balance):,} سکه",
                    callback_data=f"balance_view:{int(user_id)}",
                    style="success",
                )
            ]]
        )

    @staticmethod
    def create_game_result_keyboard(
        prize: int,
        fee_amount: int,
        winner_balance: int,
        loser_balance: int,
    ):
        """Show the settled amounts as colored, non-action result buttons."""
        return InlineKeyboardMarkup(
            [
                [
                    glass_button(
                        f"🎁 جایزه برنده: {int(prize):,} الماس",
                        callback_data="game_result:prize",
                        style="success",
                    )
                ],
                [
                    glass_button(
                        f"🧾 کارمزد: {int(fee_amount):,} الماس",
                        callback_data="game_result:fee",
                        style="primary",
                    )
                ],
                [
                    glass_button(
                        f"💰 موجودی برنده: {int(winner_balance):,} الماس",
                        callback_data="game_result:winner",
                        style="primary",
                    )
                ],
                [
                    glass_button(
                        f"💸 موجودی بازنده: {int(loser_balance):,} الماس",
                        callback_data="game_result:loser",
                        style="danger",
                    )
                ],
            ]
        )

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return ConversationHandler.END
        user_id = user.id
        if user_id in self.user_sessions:
            await self.close_login_session(user_id)
        self.clear_admin_input(context)
        self.clear_force_join_draft(context)
        context.user_data.pop("pending_receipt_draft", None)
        context.user_data.pop("coin_amount", None)
        context.user_data.pop("waiting_for_password", None)
        was_known_user = self.get_user_record(user_id) is not None
        self.register_user_profile(user)
        current_user = self.get_user_record(user_id)
        if (
            current_user is not None
            and not bool(current_user["is_active"])
            and not self.is_admin(user_id)
        ):
            await message.reply_text(
                "⛔ حساب شما توسط مدیریت مسدود شده است.\n"
                "برای پیگیری از پشتیبانی استفاده کنید."
            )
            return ConversationHandler.END
        with db_connect(USERS_DB, timeout=10) as conn:
            maintenance_row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'maintenance_mode'"
            ).fetchone()
        maintenance = bool(
            maintenance_row
            and str(maintenance_row[0]).lower() in {"1", "on", "true"}
        )
        if maintenance and not self.is_admin(user_id):
            await message.reply_text(
                "🛠 ربات موقتاً در حالت تعمیرات است.\n"
                "لطفاً کمی بعد دوباره تلاش کنید."
            )
            return ConversationHandler.END
        financial = get_financial_config(USERS_DB)

        # ثبت دعوت در حالت معلق؛ واریز فقط پس از تأیید عضویت اجباری.
        if not was_known_user and context.args:
            self.register_pending_referral(user_id, context.args[0])

        force_join_channels = self.admin_store.active_force_join_channels()
        if self.is_admin(user_id) or not force_join_channels:
            rewards = self.credit_pending_onboarding_rewards(user_id)
            await self.notify_onboarding_rewards(context, user_id, rewards)
            activation_text = self.activation_menu_text()
            await message.reply_text(
                activation_text,
                reply_markup=self.create_activation_keyboard(),
            )
            return ACTIVATION_PANEL

        channel_names = "\n".join(
            f"• {channel['title']}" for channel in force_join_channels
        )
        welcome_text = (
            "📢 عضویت در کانال‌ها الزامی است\n\n"
            "برای استفاده از ربات ابتدا عضو همه کانال‌های زیر شوید:\n"
            f"{channel_names}\n\n"
            "پس از عضویت روی دکمه «✅ بررسی» بزنید."
        )
        
        await message.reply_text(
            welcome_text,
            reply_markup=self.create_welcome_keyboard(),
        )
        return CHECK_MEMBERSHIP
    
    async def check_membership(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        force_join_channels = self.admin_store.active_force_join_channels()

        if self.is_admin(user_id) or not force_join_channels:
            rewards = self.credit_pending_onboarding_rewards(user_id)
            await self.notify_onboarding_rewards(context, user_id, rewards)
            activation_text = self.activation_menu_text()
            await query.edit_message_text(
                text=activation_text,
                reply_markup=self.create_activation_keyboard(),
            )
            return ACTIVATION_PANEL
        
        if query.data == "join":
            await query.edit_message_text(
                "📥 در حال انتقال به کانال...\n\n"
                "پس از پیوستن، روی دکمه '✅ بررسی' کلیک کنید.",
                reply_markup=self.create_welcome_keyboard()
            )
            return CHECK_MEMBERSHIP
        
        await query.edit_message_text("🔍 در حال بررسی عضویت شما...")
        
        try:
            missing_channels = []
            for channel in force_join_channels:
                member = await context.bot.get_chat_member(
                    chat_id=channel["chat_id"],
                    user_id=user_id,
                )
                is_member = (
                    member.status in {"member", "administrator", "creator"}
                    or (
                        member.status == "restricted"
                        and bool(getattr(member, "is_member", False))
                    )
                )
                if not is_member:
                    missing_channels.append(str(channel["title"]))
            if not missing_channels:
                await query.edit_message_text("🎉 عضویت شما تأیید شد!")
                rewards = self.credit_pending_onboarding_rewards(user_id)
                await self.notify_onboarding_rewards(context, user_id, rewards)

                activation_text = self.activation_menu_text()
                
                await query.edit_message_text(
                    text=activation_text,
                    reply_markup=self.create_activation_keyboard(),
                )
                return ACTIVATION_PANEL
            
            await query.edit_message_text(
                "❌ عضویت شما هنوز تأیید نشده است.\n\n"
                "ابتدا عضو کانال‌های زیر شوید:\n"
                + "\n".join(f"• {title}" for title in missing_channels)
                + "\n\nو دوباره روی "
                "«✅ بررسی» بزنید.",
                reply_markup=self.create_welcome_keyboard()
            )
            return CHECK_MEMBERSHIP
                
        except TelegramError as e:
            logging.error(f"Error checking membership: {e}")
            await query.edit_message_text(
                "❌ خطا در بررسی عضویت!\n\n"
                "از ادمین‌بودن ربات اصلی در کانال مطمئن شوید و دوباره "
                "تلاش کنید.",
                reply_markup=self.create_welcome_keyboard()
            )
            return CHECK_MEMBERSHIP
    
    async def handle_custom_start_button(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        if not query:
            return
        if query.data == "start_button:back":
            await query.answer()
            await query.edit_message_text(
                self.activation_menu_text(),
                reply_markup=self.create_activation_keyboard(),
            )
            return
        try:
            button_id = int((query.data or "").rsplit(":", 1)[1])
        except (IndexError, ValueError):
            await query.answer("دکمه نامعتبر است.", show_alert=True)
            return
        button = self.get_custom_start_button(button_id)
        if (
            button is None
            or not button["is_active"]
            or button["button_type"] != "text"
        ):
            await query.answer(
                "این دکمه غیرفعال یا حذف شده است.",
                show_alert=True,
            )
            return
        await query.answer()
        await query.edit_message_text(
            text=button["payload"],
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        glass_button(
                            "🔙 بازگشت به منوی اصلی",
                            callback_data="start_button:back",
                            style="primary",
                        )
                    ]
                ]
            ),
        )

    async def activation_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if query.data != "support_ticket":
            context.user_data.pop("awaiting_support_message", None)
        
        user_id = query.from_user.id
        financial = get_financial_config(USERS_DB)
        
        if query.data == "activate":
            if not query.message or query.message.chat.type != "private":
                bot_user = await context.bot.get_me()
                await query.edit_message_text(
                    "🔐 فعال‌سازی سلف فقط در گفت‌وگوی خصوصی ربات انجام می‌شود.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                glass_button(
                                    "💬 ورود به چت خصوصی ربات",
                                    url=f"https://t.me/{bot_user.username}?start=activate",
                                    style="success",
                                )
                            ]
                        ]
                    ),
                )
                return ACTIVATION_PANEL

            self_record = self.get_selfbot_record(user_id)
            running_pid = self.running_selfbot_pid(user_id, self_record)
            if running_pid:
                if self_record is not None:
                    self.adopt_running_selfbot(self_record, running_pid)
                await query.edit_message_text(
                    "✅ سلف شما هم‌اکنون فعال و در حال اجرا است.\n\n"
                    "برای مشاهده امکانات، با حساب سلف عبارت «پنل» را "
                    "ارسال کنید.",
                    reply_markup=self.create_activation_keyboard(),
                )
                return ACTIVATION_PANEL
            if not self.helper_is_ready():
                await query.edit_message_text(
                    "⚠️ بات هلپر پنل هنوز توسط مدیریت آماده نشده است.\n\n"
                    "برای جلوگیری از ساخت سلف بدون پنل، فعال‌سازی موقتاً "
                    "متوقف شده است. پس از تنظیم هلپر دوباره تلاش کنید.",
                    reply_markup=self.create_activation_keyboard(),
                )
                return ACTIVATION_PANEL

            user_coins = self.user_coins.get(user_id, 0)
            activation_cost = financial["activation_cost"]
            if user_coins < activation_cost:
                await query.edit_message_text(
                    f"❌ موجودی سکه شما کافی نیست!\n\n"
                    f"💰 موجودی فعلی: {user_coins} سکه\n"
                    f"💸 برای فعال‌سازی سلف به {activation_cost} سکه نیاز دارید.\n\n"
                    f"لطفاً از بخش '💰 خرید سکه' اقدام به خرید نمایید.",
                    reply_markup=self.create_activation_keyboard()
                )
                return ACTIVATION_PANEL
            
            await query.edit_message_text(
                "📱 ثبت شماره حساب تلگرام\n"
                "━━━━━━━━━━━━━━\n\n"
                "برای دریافت کد ورود، شماره‌ای را بفرستید که همین حساب "
                "تلگرام روی آن فعال است."
            )
            await query.message.reply_text(
                "روی دکمه «📱 ارسال شماره من» بزنید.\n\n"
                "اگر لازم بود می‌توانید شماره را دستی و با کد کشور وارد کنید؛ "
                "مثال: +989123456789",
                reply_markup=self.create_phone_keyboard(),
            )
            return GET_PHONE
        
        elif query.data == "buy_coins":
            coin_text = (
                "💰 خرید سکه\n"
                "━━━━━━━━━━━━━━\n\n"
                f"💰 هر عدد سکه: {financial['coin_price']:,} تومان\n"
                "تعداد سکه موردنظر را با صفحه‌کلید زیر وارد کنید."
            )
            
            await query.edit_message_text(
                coin_text,
                reply_markup=self.create_coin_keyboard()
            )
            return COIN_PURCHASE

        elif query.data == "wallet":
            await query.edit_message_text(
                self.wallet_text(user_id),
                reply_markup=self.create_wallet_keyboard(),
            )
            return ACTIVATION_PANEL

        elif query.data == "wallet_history":
            await query.edit_message_text(
                self.wallet_history_text(user_id),
                reply_markup=self.create_wallet_keyboard(),
            )
            return ACTIVATION_PANEL
        
        elif query.data == "stats":
            await self.show_stats_panel(query)
            return ACTIVATION_PANEL
        
        elif query.data == "invite":
            await self.show_invite_panel(query, context)
            return ACTIVATION_PANEL

        elif query.data == "panel_help":
            config = get_helper_config(USERS_DB)
            helper_name = (
                f"@{config['username']}"
                if config.get("username")
                else "بات هلپر"
            )
            await query.edit_message_text(
                "🎛 راهنمای پنل سلف\n\n"
                "پس از فعال‌شدن سلف، در پیوی، گروه یا Saved Messages "
                "عبارت «پنل» را با همان حساب ارسال کنید.\n"
                f"سلف نتیجه Inline را از {helper_name} می‌گیرد و پنل "
                "دکمه‌ای را دقیقاً در همان چت نمایش می‌دهد.",
                reply_markup=self.create_activation_keyboard(),
            )
            return ACTIVATION_PANEL
        
        elif query.data == "support":
            content = get_content_config(USERS_DB)
            rows = []
            rows.append(
                [
                    glass_button(
                        "🎫 ثبت تیکت داخل ربات",
                        callback_data="support_ticket",
                        style="success",
                    )
                ]
            )
            if content["support_url"]:
                rows.append(
                    [
                        glass_button(
                            "💬 ارتباط با پشتیبانی",
                            url=content["support_url"],
                            style="success",
                        )
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔙 بازگشت",
                        callback_data="back",
                        style="primary",
                    )
                ]
            )
            await query.edit_message_text(
                "🛟 پشتیبانی\n\n"
                + (
                    content["support_text"]
                    or "متن پشتیبانی هنوز توسط مدیریت تنظیم نشده است."
                ),
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return ACTIVATION_PANEL

        elif query.data == "support_ticket":
            context.user_data["awaiting_support_message"] = True
            await query.edit_message_text(
                "🎫 ثبت درخواست پشتیبانی\n\n"
                "مشکل یا درخواست خود را در یک پیام کامل ارسال کنید. "
                "پس از ثبت، پاسخ مدیریت در همین گفت‌وگو می‌آید.",
                reply_markup=InlineKeyboardMarkup(
                    [[
                        glass_button(
                            "🔙 بازگشت",
                            callback_data="support",
                            style="primary",
                        )
                    ]]
                ),
            )
            return ACTIVATION_PANEL

        elif query.data == "rules":
            content = get_content_config(USERS_DB)
            await query.edit_message_text(
                "📜 قوانین ربات\n\n"
                + (
                    content["rules_text"]
                    or "قوانین هنوز توسط مدیریت تنظیم نشده است."
                ),
                reply_markup=InlineKeyboardMarkup(
                    [[
                        glass_button(
                            "🔙 بازگشت",
                            callback_data="back",
                            style="primary",
                        )
                    ]]
                ),
            )
            return ACTIVATION_PANEL
        
        elif query.data == "back":
            activation_text = self.activation_menu_text()
            
            await query.edit_message_text(
                activation_text,
                reply_markup=self.create_activation_keyboard()
            )
            return ACTIVATION_PANEL
    
    async def show_stats_panel(self, query):
        """نمایش پنل آمار و موجودی"""
        user_id = query.from_user.id
        user_coins = self.user_coins.get(user_id, 0)
        financial = get_financial_config(USERS_DB)
        total_value = user_coins * financial["coin_price"]
        referrals_count = self.referral_count(user_id)
        
        stats_text = (
            "📊 آمار و موجودی شما\n"
            "━━━━━━━━━━━━━━\n\n"
            f"💰 موجودی سکه: {user_coins} سکه\n"
            f"💎 ارزش ریالی: {total_value:,} تومان\n"
            f"👥 تعداد دعوت‌ها: {referrals_count} نفر\n"
            f"🎁 سکه از دعوت: {referrals_count * financial['referral_reward']} سکه\n\n"
            "💡 به ازای هر دعوت موفق "
            f"{financial['referral_reward']} سکه پاداش دریافت می‌کنید!"
        )
        
        await query.edit_message_text(
            stats_text,
            reply_markup=self.create_stats_keyboard()
        )
    
    async def show_invite_panel(self, query, context: ContextTypes.DEFAULT_TYPE):
        """نمایش پنل لینک دعوت"""
        user_id = query.from_user.id
        
        invite_code = self.get_or_create_invite_code(user_id)
        
        invite_link = f"https://t.me/{context.bot.username}?start={invite_code}"
        referrals_count = self.referral_count(user_id)
        financial = get_financial_config(USERS_DB)
        
        invite_text = (
            f"🎫 **لینک دعوت شما**\n\n"
            f"🔗 **لینک:** `{invite_link}`\n\n"
            f"💎 **مزایای دعوت:**\n"
            f"• به ازای هر دعوت: **{financial['referral_reward']} سکه** پاداش\n"
            f"• دعوت شده: **{financial['new_user_gift']} سکه** هدیه اولیه\n"
            f"• بدون محدودیت تعداد دعوت\n\n"
            f"📊 **آمار دعوت‌های شما:** {referrals_count} نفر\n"
            f"💰 **سکه‌های کسب شده:** "
            f"{referrals_count * financial['referral_reward']} سکه"
        )
        
        await query.edit_message_text(
            invite_text,
            reply_markup=self.create_invite_keyboard(),
            parse_mode='Markdown'
        )
    
    async def coin_purchase(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        financial = get_financial_config(USERS_DB)
        
        if 'coin_amount' not in context.user_data:
            context.user_data['coin_amount'] = ''
        
        coin_amount = context.user_data['coin_amount']
        
        if query.data == "coin_cancel":
            context.user_data.pop("coin_amount", None)
            context.user_data.pop("pending_receipt_draft", None)
            await query.edit_message_text(
                self.wallet_text(user_id),
                reply_markup=self.create_wallet_keyboard(),
            )
            return ACTIVATION_PANEL

        if query.data == "coin_delete":
            context.user_data['coin_amount'] = ''
            await query.edit_message_text(
                "🗑️ تعداد سکه پاک شد.\nلطفاً تعداد سکه مورد نظر را وارد کنید:",
                reply_markup=self.create_coin_keyboard()
            )
            return COIN_PURCHASE
        
        elif query.data == "coin_submit":
            if not coin_amount or int(coin_amount) <= 0:
                await query.edit_message_text(
                    "❌ لطفاً تعداد سکه معتبر وارد کنید!",
                    reply_markup=self.create_coin_keyboard(coin_amount)
                )
                return COIN_PURCHASE
            
            coin_count = int(coin_amount)
            total_price = coin_count * financial["coin_price"]
            if not financial["card_number"]:
                await query.edit_message_text(
                    "❌ شماره کارت هنوز توسط مدیریت ثبت نشده است.\n"
                    "لطفاً بعداً دوباره تلاش کنید.",
                    reply_markup=self.create_wallet_keyboard(),
                )
                return ACTIVATION_PANEL
            card_number = (
                self.format_card_number(financial["card_number"])
                or "شماره کارت ثبت نشده"
            )
            holder_line = (
                f"👤 صاحب کارت: {financial['card_holder']}\n"
                if financial["card_holder"]
                else ""
            )
            
            purchase_text = (
                "🧾 ارسال رسید پرداخت\n\n"
                f"├ مبلغ: {total_price:,} تومان\n"
                f"├ تعداد: {coin_count:,} سکه\n"
                f"💳 {card_number}\n"
                f"{holder_line}\n"
                "کاربر گرامی، مبلغ تعیین‌شده را به شماره کارت بالا انتقال "
                "دهید؛ سپس تصویر یا فایل PDF رسید را همین‌جا برای ربات "
                "ارسال کنید.\n\n"
                "پس از بررسی ادمین، سکه‌ها خودکار به کیف پول شما واریز می‌شوند."
            )
            
            context.user_data["pending_receipt_draft"] = {
                "coin_amount": coin_count,
                "amount_toman": total_price,
                "coin_price_toman": financial["coin_price"],
            }
            context.user_data["coin_amount"] = ""
            await query.edit_message_text(
                purchase_text,
                reply_markup=self.create_receipt_upload_keyboard(),
            )
            return RECEIPT_UPLOAD
        
        elif query.data.startswith("coin_"):
            digit = query.data.split("_")[1]
            context.user_data['coin_amount'] += digit
            
            updated_amount = context.user_data['coin_amount']
            await query.edit_message_text(
                f"💌 تعداد سکه: {updated_amount}\n\n"
                f"💰 مبلغ قابل پرداخت: "
                f"{int(updated_amount or 0) * financial['coin_price']:,} تومان\n\n"
                f"⌨️ از کیبورد زیر برای ادامه استفاده کنید:",
                reply_markup=self.create_coin_keyboard(updated_amount)
            )
            return COIN_PURCHASE
        
        elif query.data == "display_coins":
            await query.answer(f"تعداد سکه فعلی: {coin_amount or '0'}")
            return COIN_PURCHASE

    async def receive_payment_receipt(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        user_id = update.effective_user.id
        self.register_user_profile(update.effective_user)
        draft = context.user_data.get("pending_receipt_draft")
        if not isinstance(draft, dict):
            await update.effective_message.reply_text(
                "❌ اطلاعات خرید منقضی شده است؛ از کیف پول دوباره شروع کنید.",
                reply_markup=self.create_wallet_keyboard(),
            )
            return ACTIVATION_PANEL

        if update.effective_message.photo:
            media = update.effective_message.photo[-1]
            file_id = media.file_id
            file_unique_id = media.file_unique_id
            file_type = "photo"
        elif update.effective_message.document:
            document = update.effective_message.document
            mime_type = (document.mime_type or "").lower()
            if not (
                mime_type.startswith("image/")
                or mime_type == "application/pdf"
            ):
                await update.effective_message.reply_text(
                    "❌ فقط تصویر یا فایل PDF رسید پذیرفته می‌شود.",
                    reply_markup=self.create_receipt_upload_keyboard(),
                )
                return RECEIPT_UPLOAD
            file_id = document.file_id
            file_unique_id = document.file_unique_id
            file_type = "document"
        else:
            await update.effective_message.reply_text(
                "❌ رسید را به‌صورت تصویر یا فایل PDF ارسال کنید.",
                reply_markup=self.create_receipt_upload_keyboard(),
            )
            return RECEIPT_UPLOAD

        try:
            receipt_id = self.create_payment_receipt(
                user_id=user_id,
                coin_amount=int(draft["coin_amount"]),
                amount_toman=int(draft["amount_toman"]),
                coin_price_toman=int(draft["coin_price_toman"]),
                file_id=file_id,
                file_unique_id=file_unique_id,
                file_type=file_type,
            )
        except (KeyError, TypeError, ValueError) as exc:
            await update.effective_message.reply_text(
                f"❌ {exc}\n\nیک رسید معتبر دیگر ارسال کنید.",
                reply_markup=self.create_receipt_upload_keyboard(),
            )
            return RECEIPT_UPLOAD

        receipt = self.get_payment_receipt(receipt_id)
        delivered = 0
        if receipt is not None:
            for admin_id in self.receipt_admin_ids():
                try:
                    await self.send_receipt_media(
                        context,
                        chat_id=admin_id,
                        receipt=receipt,
                    )
                    delivered += 1
                except TelegramError:
                    logging.warning(
                        "Could not deliver receipt %s to admin %s",
                        receipt_id,
                        admin_id,
                    )

        context.user_data.pop("pending_receipt_draft", None)
        context.user_data.pop("coin_amount", None)
        delivery_note = (
            ""
            if delivered
            else (
                "\n\n⚠️ رسید ذخیره شد، اما اعلان مستقیم به ادمین ارسال نشد؛ "
                "رسید همچنان در پنل مدیریت قابل مشاهده است."
            )
        )
        await update.effective_message.reply_text(
            f"✅ رسید #{receipt_id} ثبت شد.\n\n"
            f"💰 مبلغ: {int(draft['amount_toman']):,} تومان\n"
            f"🪙 تعداد: {int(draft['coin_amount']):,} سکه\n"
            "پس از تأیید ادمین، سکه‌ها خودکار واریز و نتیجه برای شما "
            f"ارسال می‌شود.{delivery_note}",
            reply_markup=self.create_wallet_keyboard(),
        )
        return ACTIVATION_PANEL

    async def cancel_receipt_upload(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        query = update.callback_query
        await query.answer()
        context.user_data.pop("pending_receipt_draft", None)
        context.user_data.pop("coin_amount", None)
        await query.edit_message_text(
            self.wallet_text(query.from_user.id),
            reply_markup=self.create_wallet_keyboard(),
        )
        return ACTIVATION_PANEL
    
    async def get_phone_number(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        contact = update.message.contact
        user_input = (update.message.text or "").strip()
        user_id = update.message.from_user.id
        
        if user_input == "🔙 بازگشت به منوی اصلی":
            activation_text = self.activation_menu_text()

            await update.message.reply_text(
                "✅ ثبت شماره لغو شد.",
                reply_markup=ReplyKeyboardRemove(),
            )
            await update.message.reply_text(
                activation_text,
                reply_markup=self.create_activation_keyboard()
            )
            return ACTIVATION_PANEL

        if contact:
            if contact.user_id != user_id:
                await update.message.reply_text(
                    "❌ فقط شماره متعلق به حساب خودتان قابل قبول است.\n"
                    "لطفاً از دکمه «📱 ارسال شماره من» استفاده کنید.",
                    reply_markup=self.create_phone_keyboard(),
                )
                return GET_PHONE
            user_input = contact.phone_number or ""

        phone_digits = ''.join(filter(str.isdigit, user_input))
        if phone_digits.startswith('00'):
            phone_digits = phone_digits[2:]

        if phone_digits.startswith('09') and len(phone_digits) == 11:
            phone_digits = '98' + phone_digits[1:]
        elif phone_digits.startswith('9') and len(phone_digits) == 10:
            phone_digits = '98' + phone_digits

        if not 8 <= len(phone_digits) <= 15:
            await update.message.reply_text(
                "❌ شماره تلفن معتبر نیست.\n\n"
                "از دکمه «📱 ارسال شماره من» استفاده کنید یا شماره را با "
                "کد کشور وارد کنید؛ مثال: +989123456789",
                reply_markup=self.create_phone_keyboard()
            )
            return GET_PHONE

        phone_number = '+' + phone_digits
        login_client = None

        existing_owner = self.phone_owner(phone_number)
        if existing_owner is not None and existing_owner != user_id:
            await update.message.reply_text(
                "❌ این شماره قبلاً برای حساب دیگری ثبت شده است.",
                reply_markup=self.create_phone_keyboard(),
            )
            return GET_PHONE

        security_store = getattr(self, "admin_store", None)
        allowed, wait_seconds = (
            security_store.register_login_request(user_id)
            if security_store is not None
            else (True, 0)
        )
        if not allowed:
            wait_minutes = max(1, (wait_seconds + 59) // 60)
            await update.message.reply_text(
                "⛔ تعداد درخواست‌های کد ورود بیش از حد مجاز است.\n"
                f"لطفاً حدود {wait_minutes} دقیقه دیگر دوباره تلاش کنید.",
                reply_markup=self.create_phone_keyboard(),
            )
            return GET_PHONE
        
        try:
            # A failed prompt from an earlier attempt must not leave a connected
            # temporary Telethon client behind.
            if user_id in self.user_sessions:
                await self.close_login_session(user_id)

            processing_msg = await update.message.reply_text(
                "⏳ در حال ارسال کد تأیید...",
                reply_markup=ReplyKeyboardRemove(),
            )
            
            result = await self.send_verification_code(phone_number, user_id)
            
            if result['success']:
                login_client = result['client']
                self.user_sessions[user_id] = {
                    'phone_number': phone_number,
                    'phone_code_hash': result['phone_code_hash'],
                    'client': login_client,
                    'timestamp': time.time(),
                    'entered_code': ''
                }
                await self.send_code_entry_prompt(
                    update.message,
                    processing_msg,
                )
                
                return GET_CODE
                
            else:
                try:
                    await processing_msg.edit_text(
                        "❌ ارسال کد تأیید ناموفق بود."
                    )
                except Exception:
                    logging.warning(
                        "Could not edit failed verification progress message",
                        exc_info=True,
                    )
                await update.message.reply_text(
                    f"❌ خطا در ارسال کد تأیید:\n{result['error']}\n\n"
                    "لطفاً شماره دیگری وارد نمایید:",
                    reply_markup=self.create_phone_keyboard(),
                )
                return GET_PHONE
                
        except Exception as e:
            logging.exception("Error while opening verification-code step")
            if user_id in self.user_sessions:
                await self.close_login_session(user_id)
            elif login_client is not None:
                try:
                    await login_client.disconnect()
                except Exception:
                    logging.exception(
                        "Error while closing failed temporary login client"
                    )
            await update.message.reply_text(
                "❌ کد ارسال شد، اما صفحه ورود کد باز نشد.\n\n"
                "نشست ناقص بسته شد. لطفاً دوباره شماره را ارسال کنید:",
                reply_markup=self.create_phone_keyboard()
            )
            return GET_PHONE
    
    async def send_verification_code(self, phone_number: str, user_id: int):
        client = None
        try:
            client = TelegramClient(StringSession(), self.api_id, self.api_hash)
            await client.connect()
            
            result = await client.send_code_request(phone_number)
            
            return {
                'success': True,
                'phone_code_hash': result.phone_code_hash,
                'client': client,
                'message': 'کد تأیید با موفقیت ارسال شد'
            }
            
        except Exception as e:
            logging.error(f"Telethon error in send_verification_code: {e}")
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    logging.exception(
                        "Error while closing temporary verification client"
                    )
            
            error_message = str(e)
            if "FLOOD" in error_message:
                return {'success': False, 'error': 'تعداد درخواست‌ها زیاد است. لطفاً چند دقیقه صبر کنید.'}
            elif "PHONE_NUMBER_INVALID" in error_message:
                return {'success': False, 'error': 'شماره تلفن معتبر نیست.'}
            elif "PHONE_NUMBER_BANNED" in error_message:
                return {'success': False, 'error': 'شماره تلفن مسدود شده است.'}
            else:
                return {
                    'success': False,
                    'error': 'ارسال کد انجام نشد؛ کمی بعد دوباره تلاش کنید.',
                }
    
    async def verify_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        if user_id not in self.user_sessions:
            await query.edit_message_text(
                "❌ سشن شما منقضی شده است. لطفاً دوباره /start را ارسال کنید."
            )
            return ConversationHandler.END
        
        session_data = self.user_sessions[user_id]

        if query.data == "login_cancel":
            await self.close_login_session(user_id)
            await query.edit_message_text(
                "❌ ورود به حساب لغو شد.\n\n"
                + self.activation_menu_text(),
                reply_markup=self.create_activation_keyboard(),
            )
            return ACTIVATION_PANEL
        
        if query.data == "delete":
            session_data['entered_code'] = ''
            await query.edit_message_text(
                "🗑️ کد وارد شده پاک شد.\nلطفاً کد را دوباره وارد کنید:",
                reply_markup=self.create_code_keyboard()
            )
            return GET_CODE
        
        elif query.data == "submit":
            if len(session_data['entered_code']) != 5:
                await query.edit_message_text(
                    "❌ کد باید ۵ رقمی باشد! لطفاً کد کامل را وارد کنید.",
                    reply_markup=self.create_code_keyboard(session_data['entered_code'])
                )
                return GET_CODE
            
            return await self.check_verification_code(query, context, session_data['entered_code'])
        
        elif query.data in ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]:
            if len(session_data['entered_code']) < 5:
                session_data['entered_code'] += query.data
                
                if len(session_data['entered_code']) == 5:
                    await query.edit_message_text(
                        "✅ هر ۵ رقم کد وارد شد.\n"
                        "📲 برای تأیید روی دکمه '✅ تایید' کلیک کنید.",
                        reply_markup=self.create_code_keyboard(session_data['entered_code'])
                    )
                else:
                    await query.edit_message_text(
                        "🔢 در حال ورود کد تأیید\n"
                        f"📝 {5 - len(session_data['entered_code'])} رقم باقی مانده",
                        reply_markup=self.create_code_keyboard(session_data['entered_code'])
                    )
            else:
                await query.edit_message_text(
                    "❌ کد کامل شده! برای تأیید روی دکمه '✅ تایید' کلیک کنید.",
                    reply_markup=self.create_code_keyboard(session_data['entered_code'])
                )
            
            return GET_CODE
        
        elif query.data == "display":
            await query.answer(
                f"{len(session_data['entered_code'])} رقم از ۵ رقم وارد شده"
            )
            return GET_CODE

        return GET_CODE

    async def verify_code_text(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        """Accept the Telegram login code as a five-digit text message."""
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return GET_CODE

        user_id = user.id
        session_data = self.user_sessions.get(user_id)
        if session_data is None:
            await message.reply_text(
                "❌ نشست ورود منقضی شده است. لطفاً دوباره /start را ارسال کنید."
            )
            return ConversationHandler.END
        code = self.normalize_verification_code(message.text or "")
        if not code:
            await message.reply_text(
                "❌ کد باید دقیقاً ۵ رقم باشد.\n"
                "کد را به‌صورت 12345 بفرستید یا از دکمه‌ها استفاده کنید.",
                reply_markup=self.create_code_keyboard(
                    session_data.get("entered_code", "")
                ),
            )
            return GET_CODE

        session_data["entered_code"] = code
        status_message = await message.reply_text(
            "⏳ در حال بررسی کد و ورود به اکانت..."
        )
        try:
            await message.delete()
        except Exception:
            # Deleting the incoming code is a privacy improvement, not a
            # requirement for completing the login.
            logging.info("Could not delete text verification-code message")

        return await self.finish_verification_code(
            user_id,
            context,
            code,
            status_message,
            status_message.edit_text,
        )
    
    async def check_verification_code(self, query, context: ContextTypes.DEFAULT_TYPE, code: str):
        user_id = query.from_user.id
        if user_id not in self.user_sessions:
            await query.edit_message_text(
                "❌ نشست ورود منقضی شده است. لطفاً دوباره /start را ارسال کنید."
            )
            return ConversationHandler.END

        await query.edit_message_text("⏳ در حال بررسی کد و ورود به اکانت...")
        return await self.finish_verification_code(
            user_id,
            context,
            code,
            query.message,
            query.edit_message_text,
        )

    async def finish_verification_code(
        self,
        user_id: int,
        context: ContextTypes.DEFAULT_TYPE,
        code: str,
        reply_message,
        edit_status,
    ):
        """Complete sign-in for either the inline keypad or a text code."""
        session_data = self.user_sessions.get(user_id)
        if session_data is None:
            await edit_status(
                "❌ نشست ورود منقضی شده است. لطفاً دوباره /start را ارسال کنید."
            )
            return ConversationHandler.END
        started_at = float(session_data.get("timestamp") or 0)
        if started_at and time.time() - started_at > 600:
            await edit_status(
                "❌ نشست ورود پس از ۱۰ دقیقه منقضی شد.\n"
                "لطفاً فرایند ساخت سلف را دوباره شروع کنید."
            )
            await self.close_login_session(user_id)
            return ConversationHandler.END

        client = session_data['client']
        phone_number = session_data['phone_number']
        phone_code_hash = session_data['phone_code_hash']
        
        try:
            await client.sign_in(
                phone=phone_number,
                code=code,
                phone_code_hash=phone_code_hash
            )

            await edit_status(
                "✅ کد تأیید صحیح است! در حال فعال‌سازی سلف بات..."
            )
            return await self.complete_selfbot_activation(
                user_id,
                session_data,
                reply_message,
            )

        except SessionPasswordNeededError:
            context.user_data['waiting_for_password'] = True
            await edit_status(
                "🔐 حساب شما دارای رمز دومرحله‌ای است.\n"
                "لطفاً رمز عبور خود را به صورت متن ارسال کنید:"
            )
            return GET_PASSWORD

        except PhoneCodeExpiredError:
            await edit_status(
                "❌ کد تأیید منقضی شده است!\n"
                "لطفاً دوباره /start را ارسال کنید."
            )

        except PhoneCodeInvalidError:
            security_store = getattr(self, "admin_store", None)
            can_retry, failures = (
                security_store.record_login_failure(user_id)
                if security_store is not None
                else (True, 1)
            )
            if not can_retry:
                await edit_status(
                    "⛔ تعداد کدهای نامعتبر بیش از حد مجاز بود.\n"
                    "ورود موقتاً قفل شد؛ ۳۰ دقیقه دیگر دوباره تلاش کنید."
                )
                await self.close_login_session(user_id)
                return ConversationHandler.END
            session_data['entered_code'] = ''
            await edit_status(
                "❌ کد تأیید نامعتبر است!\n"
                f"تلاش ناموفق: {failures} از ۵\n"
                "لطفاً کد صحیح را وارد کنید:",
                reply_markup=self.create_code_keyboard()
            )
            return GET_CODE

        except Exception as sign_in_error:
            logging.exception("Error while checking Telegram verification code")
            await edit_status(
                "❌ ورود به حساب کامل نشد.\n"
                f"نوع خطا: {type(sign_in_error).__name__}\n"
                "لطفاً دوباره /start را ارسال کنید."
            )

        await self.close_login_session(user_id)
        return ConversationHandler.END

    async def verify_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """دریافت و بررسی رمز دومرحله‌ای در یک حالت مکالمه مستقل."""
        user_id = update.message.from_user.id
        session_data = self.user_sessions.get(user_id)

        if not session_data:
            await update.message.reply_text(
                "❌ نشست ورود منقضی شده است. لطفاً دوباره /start را ارسال کنید."
            )
            return ConversationHandler.END

        try:
            await session_data['client'].sign_in(password=update.message.text)
            security_store = getattr(self, "admin_store", None)
            if security_store is not None:
                security_store.reset_login_security(user_id)
            context.user_data.pop('waiting_for_password', None)
            await update.message.reply_text(
                "✅ رمز دومرحله‌ای صحیح است! در حال فعال‌سازی سلف بات..."
            )
            return await self.complete_selfbot_activation(
                user_id,
                session_data,
                update.message,
            )

        except PasswordHashInvalidError:
            security_store = getattr(self, "admin_store", None)
            can_retry, failures = (
                security_store.record_login_failure(user_id)
                if security_store is not None
                else (True, 1)
            )
            if not can_retry:
                await update.message.reply_text(
                    "⛔ تعداد رمزهای نامعتبر بیش از حد مجاز بود.\n"
                    "ورود موقتاً قفل شد؛ ۳۰ دقیقه دیگر دوباره تلاش کنید."
                )
                await self.close_login_session(user_id)
                return ConversationHandler.END
            await update.message.reply_text(
                f"❌ رمز دومرحله‌ای نادرست است؛ تلاش {failures} از ۵.\n"
                "دوباره وارد کنید:"
            )
            return GET_PASSWORD

        except Exception:
            logging.exception("Error while checking Telegram two-step password")
            await update.message.reply_text(
                "❌ بررسی رمز دومرحله‌ای ناموفق بود. لطفاً دوباره /start را ارسال کنید."
            )
            await self.close_login_session(user_id)
            return ConversationHandler.END

    async def complete_selfbot_activation(self, user_id, session_data, reply_message):
        """Reserve the fee atomically, launch, then commit or refund once."""
        phone_number = session_data['phone_number']
        activation_cost = get_financial_config(USERS_DB)["activation_cost"]
        security_store = getattr(self, "admin_store", None)
        if security_store is not None:
            security_store.reset_login_security(user_id)

        try:
            reservation = self.reserve_activation_cost(
                user_id, phone_number, activation_cost
            )
        except ValueError as exc:
            await reply_message.reply_text(f"❌ {exc}")
            await self.close_login_session(user_id)
            return ConversationHandler.END

        session_string = session_data['client'].session.save()
        try:
            success = await self.activate_selfbot(
                session_string, user_id, phone_number
            )
            if not success:
                self.finish_activation_reservation(
                    reservation["id"], success=False,
                    error_text="پردازش سلف آماده نشد",
                )
                await reply_message.reply_text(
                    "⚠️ ورود موفق بود، اما اجرای سلف تأیید نشد.\n"
                    "هزینه رزروشده به کیف پول برگشت داده شد."
                )
            else:
                try:
                    process_info = self.active_selfbots[user_id]
                    self.save_activated_user(
                        user_id, phone_number, process_info['process'],
                        process_info['session_file'],
                    )
                    self.finish_activation_reservation(
                        reservation["id"], success=True
                    )
                    self.apply_feature_policies_for_user(user_id, phone_number)
                except Exception as exc:
                    await self.stop_selfbot(
                        user_id, disable=True, status="activation_failed",
                        detail=str(exc),
                    )
                    self.finish_activation_reservation(
                        reservation["id"], success=False, error_text=str(exc)
                    )
                    raise
                await reply_message.reply_text(
                    "🎉 سلف با موفقیت فعال شد\n"
                    "━━━━━━━━━━━━━━\n\n"
                    "✅ حساب تلگرام تأیید شد\n"
                    "✅ اجرای سلف و Watchdog تأیید شد\n"
                    f"💰 هزینه فعال‌سازی: {activation_cost} سکه\n\n"
                    "اکنون با همان حساب تلگرام عبارت «پنل» را ارسال کنید."
                )
        except Exception as exc:
            logging.exception("Activation finalization failed")
            try:
                self.finish_activation_reservation(
                    reservation["id"], success=False, error_text=str(exc)
                )
            except Exception:
                logging.exception("Could not refund activation reservation")
            await reply_message.reply_text(
                "❌ فعال‌سازی کامل نشد و هزینه به کیف پول برگشت داده شد."
            )

        await self.close_login_session(user_id)
        return ConversationHandler.END

    async def close_login_session(self, user_id):
        session_data = self.user_sessions.pop(user_id, None)
        if session_data and session_data.get('client'):
            try:
                await session_data['client'].disconnect()
            except Exception:
                logging.exception("Error while closing temporary login client")
    
    async def activate_selfbot(self, session_string: str, user_id: int, phone_number: str):
        """اجرای سلف و انتظار برای پیام آماده‌بودن از پردازش فرزند."""
        session_file = SESSIONS_DIR / f"session_{user_id}.txt"
        try:
            write_session_file(session_file, DATA_DIR, session_string)
            self.update_selfbot_runtime(
                user_id,
                phone=str(phone_number),
                session_file=str(session_file),
                self_enabled=0,
                self_status="activating",
                self_last_error=None,
                self_next_restart_at=None,
            )
            success, detail = await self.launch_saved_selfbot(
                user_id,
                reason="activation",
                enable_watchdog=False,
            )
            if not success:
                self.update_selfbot_runtime(
                    user_id,
                    self_enabled=0,
                    self_status="activation_failed",
                    self_last_error=detail,
                    self_next_restart_at=None,
                )
            return success
        except Exception as exc:
            logging.exception("Error activating selfbot: %s", exc)
            await self.stop_selfbot(
                user_id,
                disable=True,
                status="activation_failed",
                detail=str(exc),
            )
            return False
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        context.user_data.pop("pending_receipt_draft", None)
        context.user_data.pop("coin_amount", None)
        
        if user_id in self.user_sessions:
            await self.close_login_session(user_id)
        
        await update.message.reply_text(
            "❌ عملیات لغو شد.\n\n"
            "برای شروع مجدد /start را ارسال کنید."
        )
        return ConversationHandler.END

    # بازی دونفره و انتقال موجودی
    @staticmethod
    def _record_balance_transaction(
        conn,
        *,
        user_id: int,
        amount: int,
        balance_after: int,
        transaction_type: str,
        note: str,
        admin_id: int | None = None,
    ) -> None:
        conn.execute(
            '''INSERT INTO balance_transactions (
                   user_id, amount, balance_after, transaction_type,
                   admin_id, note
               ) VALUES (?, ?, ?, ?, ?, ?)''',
            (
                int(user_id),
                int(amount),
                int(balance_after),
                str(transaction_type),
                None if admin_id is None else int(admin_id),
                str(note),
            ),
        )

    @staticmethod
    def _change_system_balance(
        conn,
        *,
        account_key: str,
        amount: int,
        transaction_type: str,
        note: str,
    ) -> int:
        row = conn.execute(
            "SELECT balance FROM system_balances WHERE account_key = ?",
            (str(account_key),),
        ).fetchone()
        current = int(row[0] or 0) if row else 0
        new_balance = current + int(amount)
        if new_balance < 0:
            raise ValueError("System balance cannot become negative")
        conn.execute(
            '''INSERT INTO system_balances(account_key, balance, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(account_key) DO UPDATE SET
                   balance = excluded.balance,
                   updated_at = CURRENT_TIMESTAMP''',
            (str(account_key), new_balance),
        )
        conn.execute(
            '''INSERT INTO system_balance_transactions (
                   account_key, amount, balance_after, transaction_type, note
               ) VALUES (?, ?, ?, ?, ?)''',
            (
                str(account_key),
                int(amount),
                new_balance,
                str(transaction_type),
                str(note),
            ),
        )
        return new_balance

    def betting_treasury_balance(self) -> int:
        with db_connect(USERS_DB, timeout=10) as conn:
            row = conn.execute(
                '''SELECT balance FROM system_balances
                   WHERE account_key = 'betting_treasury' '''
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def _game_operation_lock(self, game_id: str) -> asyncio.Lock:
        lock = self.game_operation_locks.get(str(game_id))
        if lock is None:
            lock = asyncio.Lock()
            self.game_operation_locks[str(game_id)] = lock
        return lock

    @staticmethod
    def _utc_sql_now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _local_game_time() -> str:
        return datetime.now(BOT_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def calculate_game_payout(
        diamond_amount: int,
        fee_percent: int,
    ) -> tuple[int, int]:
        # کارمزد رو به پایین گرد می‌شود؛ حداقل مبلغ بازی جداگانه کنترل می‌شود.
        stake = max(0, int(diamond_amount))
        percent = max(0, min(int(fee_percent), 50))
        total_pot = stake * 2
        if percent <= 0 or total_pot <= 0:
            fee_amount = 0
        else:
            fee_amount = min(total_pot, (total_pot * percent) // 100)
        return total_pot - fee_amount, fee_amount

    @staticmethod
    def minimum_stake_for_fee(fee_percent: int) -> int:
        percent = max(0, min(int(fee_percent), 50))
        if percent <= 0:
            return 1
        return max(1, (100 + (2 * percent) - 1) // (2 * percent))

    @staticmethod
    def _consume_betting_rate_limit(
        conn,
        *,
        user_id: int,
        action: str,
        limit: int,
    ) -> tuple[bool, int]:
        now = int(time.time())
        row = conn.execute(
            '''SELECT window_started_at, action_count
               FROM betting_rate_limits WHERE user_id = ? AND action = ?''',
            (int(user_id), str(action)),
        ).fetchone()
        if row is None or now - int(row[0]) >= BETTING_RATE_WINDOW_SECONDS:
            conn.execute(
                '''INSERT INTO betting_rate_limits (
                       user_id, action, window_started_at, action_count
                   ) VALUES (?, ?, ?, 1)
                   ON CONFLICT(user_id, action) DO UPDATE SET
                       window_started_at = excluded.window_started_at,
                       action_count = 1''',
                (int(user_id), str(action), now),
            )
            return True, 0
        retry_after = max(
            1,
            BETTING_RATE_WINDOW_SECONDS - (now - int(row[0])),
        )
        if int(row[1]) >= int(limit):
            return False, retry_after
        conn.execute(
            '''UPDATE betting_rate_limits SET action_count = action_count + 1
               WHERE user_id = ? AND action = ?''',
            (int(user_id), str(action)),
        )
        return True, 0

    def consume_betting_rate_limit(
        self,
        *,
        user_id: int,
        action: str,
        limit: int,
    ) -> tuple[bool, int]:
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            return self._consume_betting_rate_limit(
                conn,
                user_id=user_id,
                action=action,
                limit=limit,
            )

    def betting_chat_allowed(self, chat_id: int) -> bool:
        chat_id = int(chat_id)
        if BETTING_ALLOWED_CHAT_IDS:
            return chat_id in BETTING_ALLOWED_CHAT_IDS
        with db_connect(USERS_DB, timeout=10) as conn:
            row = conn.execute(
                "SELECT is_active FROM betting_allowed_chats WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            if row is not None and not bool(row[0]):
                return False
            active_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM betting_allowed_chats WHERE is_active = 1"
                ).fetchone()[0]
            )
            if active_count == 0:
                return True
            return row is not None and bool(row[0])

    def get_waiting_game(self, game_id: str) -> dict | None:
        cached = self.active_games.get(str(game_id))
        if cached:
            return cached
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                '''SELECT * FROM two_player_games
                   WHERE game_id = ? AND status = 'waiting' ''',
                (str(game_id),),
            ).fetchone()
        if row is None:
            return None
        game = {
            "creator_id": int(row["creator_id"]),
            "creator_name": str(row["creator_name"]),
            "chat_id": int(row["chat_id"]),
            "message_id": (
                int(row["message_id"]) if row["message_id"] is not None else None
            ),
            "message_thread_id": (
                int(row["message_thread_id"])
                if row["message_thread_id"] is not None
                else None
            ),
            "diamond_amount": int(row["diamond_amount"]),
            "fee_percent": int(row["fee_percent"] or 0),
            "expires_at": str(row["expires_at"] or ""),
        }
        self.active_games[str(game_id)] = game
        return game

    async def betting_membership_status(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
    ) -> tuple[bool, str, bool]:
        """Return (is_member, detail, definitive).

        ``definitive`` is False for temporary Telegram/API failures. Financial
        actions must never be reversed based on a non-definitive check.
        """
        if self.is_admin(int(user_id)):
            return True, "", True
        channels = self.admin_store.active_force_join_channels()
        if not channels:
            return True, "", True
        missing = []
        try:
            for channel in channels:
                member = await context.bot.get_chat_member(
                    chat_id=channel["chat_id"],
                    user_id=int(user_id),
                )
                is_member = (
                    member.status in {"member", "administrator", "creator"}
                    or (
                        member.status == "restricted"
                        and bool(getattr(member, "is_member", False))
                    )
                )
                if not is_member:
                    missing.append(str(channel["title"]))
        except TelegramError:
            logging.exception("Could not verify betting membership")
            return (
                False,
                "بررسی عضویت ممکن نشد؛ کمی بعد دوباره تلاش کنید.",
                False,
            )
        if missing:
            shown = "، ".join(missing[:3])
            if len(missing) > 3:
                shown += f" و {len(missing) - 3} کانال دیگر"
            return (
                False,
                ("ابتدا عضو کانال‌های اجباری شوید: " + shown)[:180],
                True,
            )
        return True, "", True

    def _reserve_game_funds(
        self,
        *,
        game_id: str,
        creator_id: int,
        creator_name: str,
        chat_id: int,
        diamond_amount: int,
        fee_percent: int,
        message_thread_id: int | None = None,
        enforce_rate_limit: bool = True,
    ) -> dict:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(minutes=BETTING_GAME_TTL_MINUTES)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            if enforce_rate_limit:
                allowed, retry_after = self._consume_betting_rate_limit(
                    conn,
                    user_id=creator_id,
                    action="create",
                    limit=BETTING_CREATE_RATE_LIMIT,
                )
                if not allowed:
                    return {
                        "ok": False,
                        "reason": "rate_limited",
                        "retry_after": retry_after,
                    }
            open_count = int(
                conn.execute(
                    '''SELECT COUNT(*) FROM two_player_games
                       WHERE creator_id = ? AND status = 'waiting'
                         AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)''',
                    (int(creator_id),),
                ).fetchone()[0]
            )
            if open_count >= BETTING_MAX_OPEN_GAMES_PER_USER:
                return {"ok": False, "reason": "too_many_open", "count": open_count}
            row = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?",
                (int(creator_id),),
            ).fetchone()
            current_balance = int(row[0] or 0) if row else 0
            if current_balance < int(diamond_amount):
                return {
                    "ok": False,
                    "reason": "insufficient_balance",
                    "balance": current_balance,
                }
            new_balance = current_balance - int(diamond_amount)
            self._upsert_user_coins(conn, creator_id, new_balance)
            conn.execute(
                '''INSERT INTO two_player_games (
                       game_id, creator_id, creator_name, chat_id,
                       message_thread_id, diamond_amount, fee_percent,
                       status, expires_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'waiting', ?, CURRENT_TIMESTAMP)''',
                (
                    str(game_id),
                    int(creator_id),
                    str(creator_name),
                    int(chat_id),
                    (
                        None
                        if message_thread_id is None
                        else int(message_thread_id)
                    ),
                    int(diamond_amount),
                    int(fee_percent),
                    expires_at,
                ),
            )
            self._record_balance_transaction(
                conn,
                user_id=creator_id,
                amount=-int(diamond_amount),
                balance_after=new_balance,
                transaction_type="betting_stake_reserved",
                note=f"رزرو مبلغ بازی {game_id} در گروه {chat_id}",
            )
        return {"ok": True, "balance": new_balance, "expires_at": expires_at}

    def _refund_waiting_game(
        self,
        *,
        game_id: str,
        status: str,
        reason: str,
    ) -> dict | None:
        if status not in {"canceled", "expired", "failed"}:
            raise ValueError("Invalid game refund status")
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                '''SELECT creator_id, creator_name, chat_id, message_id,
                          message_thread_id, diamond_amount, status
                   FROM two_player_games WHERE game_id = ?''',
                (str(game_id),),
            ).fetchone()
            if row is None or str(row[6]) != "waiting":
                return None
            creator_id = int(row[0])
            balance_row = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?",
                (creator_id,),
            ).fetchone()
            current_balance = int(balance_row[0] or 0) if balance_row else 0
            refunded_balance = current_balance + int(row[5])
            self._upsert_user_coins(conn, creator_id, refunded_balance)
            time_column = "expired_at" if status == "expired" else "canceled_at"
            conn.execute(
                f'''UPDATE two_player_games
                    SET status = ?, {time_column} = CURRENT_TIMESTAMP,
                        cancel_reason = ?, creator_balance_after = ?,
                        closure_message_synced = ?,
                        closure_next_retry_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE game_id = ? AND status = 'waiting' ''',
                (
                    status,
                    str(reason),
                    refunded_balance,
                    0 if row[3] is not None else 1,
                    str(game_id),
                ),
            )
            self._record_balance_transaction(
                conn,
                user_id=creator_id,
                amount=int(row[5]),
                balance_after=refunded_balance,
                transaction_type="betting_stake_refund",
                note=f"بازگشت مبلغ بازی {game_id}: {reason}",
            )
        self.user_coins[creator_id] = refunded_balance
        return {
            "game_id": str(game_id),
            "creator_id": creator_id,
            "creator_name": str(row[1]),
            "chat_id": int(row[2]),
            "message_id": int(row[3]) if row[3] is not None else None,
            "message_thread_id": int(row[4]) if row[4] is not None else None,
            "diamond_amount": int(row[5]),
            "balance": refunded_balance,
            "status": status,
        }

    def _settle_waiting_game(
        self,
        *,
        game_id: str,
        participant_id: int,
        participant_name: str,
        winner_id: int,
        winner_name: str,
        loser_id: int,
        loser_name: str,
    ) -> dict:
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                '''SELECT creator_id, creator_name, chat_id, message_id,
                          message_thread_id, diamond_amount, fee_percent,
                          status, expires_at
                   FROM two_player_games WHERE game_id = ?''',
                (str(game_id),),
            ).fetchone()
            if row is None or str(row[7]) != "waiting":
                return {"ok": False, "reason": "unavailable"}

            creator_id = int(row[0])
            diamond_amount = int(row[5])
            fee_percent = int(row[6] or 0)
            expires_at = str(row[8] or "")
            if expires_at and expires_at <= self._utc_sql_now():
                balance_row = conn.execute(
                    "SELECT coins FROM users WHERE user_id = ?",
                    (creator_id,),
                ).fetchone()
                current = int(balance_row[0] or 0) if balance_row else 0
                refunded = current + diamond_amount
                self._upsert_user_coins(conn, creator_id, refunded)
                conn.execute(
                    '''UPDATE two_player_games
                       SET status = 'expired', expired_at = CURRENT_TIMESTAMP,
                           cancel_reason = 'ttl_expired',
                           creator_balance_after = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE game_id = ? AND status = 'waiting' ''',
                    (refunded, str(game_id)),
                )
                self._record_balance_transaction(
                    conn,
                    user_id=creator_id,
                    amount=diamond_amount,
                    balance_after=refunded,
                    transaction_type="betting_stake_refund",
                    note=f"بازگشت مبلغ بازی منقضی‌شده {game_id}",
                )
                self.user_coins[creator_id] = refunded
                return {"ok": False, "reason": "expired", "balance": refunded}

            participant_row = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?",
                (int(participant_id),),
            ).fetchone()
            participant_start = int(participant_row[0] or 0) if participant_row else 0
            if participant_start < diamond_amount:
                return {
                    "ok": False,
                    "reason": "insufficient_balance",
                    "balance": participant_start,
                    "required": diamond_amount,
                }

            creator_row = conn.execute(
                "SELECT coins FROM users WHERE user_id = ?",
                (creator_id,),
            ).fetchone()
            balances = {
                creator_id: int(creator_row[0] or 0) if creator_row else 0,
                int(participant_id): participant_start,
            }
            prize, fee_amount = self.calculate_game_payout(
                diamond_amount,
                fee_percent,
            )
            balances[int(participant_id)] -= diamond_amount
            participant_after_stake = balances[int(participant_id)]
            self._record_balance_transaction(
                conn,
                user_id=participant_id,
                amount=-diamond_amount,
                balance_after=participant_after_stake,
                transaction_type="betting_stake_reserved",
                note=f"ورود به بازی {game_id}",
            )

            balances[int(winner_id)] = balances.get(int(winner_id), 0) + prize
            self._record_balance_transaction(
                conn,
                user_id=winner_id,
                amount=prize,
                balance_after=balances[int(winner_id)],
                transaction_type="betting_prize",
                note=f"جایزه بازی {game_id}; بازنده {loser_id}",
            )

            if fee_amount > 0:
                treasury_balance = self._change_system_balance(
                    conn,
                    account_key="betting_treasury",
                    amount=fee_amount,
                    transaction_type="betting_fee_income",
                    note=f"کارمزد بازی {game_id}",
                )
            else:
                treasury_row = conn.execute(
                    "SELECT balance FROM system_balances WHERE account_key = ?",
                    ("betting_treasury",),
                ).fetchone()
                treasury_balance = int(treasury_row[0] or 0) if treasury_row else 0

            for affected_user_id, final_balance in balances.items():
                self._upsert_user_coins(conn, affected_user_id, final_balance)

            creator_after = balances[creator_id]
            participant_after = balances[int(participant_id)]
            conn.execute(
                '''UPDATE two_player_games
                   SET participant_id = ?, participant_name = ?,
                       winner_id = ?, winner_name = ?,
                       loser_id = ?, loser_name = ?,
                       prize_amount = ?, fee_amount = ?,
                       creator_balance_after = ?,
                       participant_balance_after = ?,
                       status = 'settled', settled_at = CURRENT_TIMESTAMP,
                       result_delivery_state = 'pending',
                       result_next_retry_at = CURRENT_TIMESTAMP,
                       closure_message_synced = 1,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE game_id = ? AND status = 'waiting' ''',
                (
                    int(participant_id),
                    str(participant_name),
                    int(winner_id),
                    str(winner_name),
                    int(loser_id),
                    str(loser_name),
                    int(prize),
                    int(fee_amount),
                    int(creator_after),
                    int(participant_after),
                    str(game_id),
                ),
            )

        for affected_user_id, final_balance in balances.items():
            self.user_coins[int(affected_user_id)] = int(final_balance)
        return {
            "ok": True,
            "game_id": str(game_id),
            "creator_id": creator_id,
            "chat_id": int(row[2]),
            "message_id": int(row[3]) if row[3] is not None else None,
            "message_thread_id": int(row[4]) if row[4] is not None else None,
            "diamond_amount": diamond_amount,
            "fee_percent": fee_percent,
            "prize": prize,
            "fee_amount": fee_amount,
            "treasury_balance": treasury_balance,
            "winner_id": int(winner_id),
            "winner_name": str(winner_name),
            "winner_balance": int(balances[int(winner_id)]),
            "loser_id": int(loser_id),
            "loser_name": str(loser_name),
            "loser_balance": int(balances[int(loser_id)]),
        }

    def _mark_game_result_synced(
        self,
        game_id: str,
        *,
        synced: bool,
        result_message_id: int | None = None,
        error: str = "",
        state: str | None = None,
    ) -> None:
        try:
            with db_connect(USERS_DB, timeout=10) as conn:
                if synced:
                    conn.execute(
                        '''UPDATE two_player_games
                           SET result_message_synced = 1,
                               result_message_id = ?,
                               result_delivery_state = 'synced',
                               result_last_attempt_at = CURRENT_TIMESTAMP,
                               result_delivery_error = '',
                               result_next_retry_at = NULL,
                               updated_at = CURRENT_TIMESTAMP
                           WHERE game_id = ?''',
                        (
                            None
                            if result_message_id is None
                            else int(result_message_id),
                            str(game_id),
                        ),
                    )
                else:
                    row = conn.execute(
                        '''SELECT result_retry_count FROM two_player_games
                           WHERE game_id = ?''',
                        (str(game_id),),
                    ).fetchone()
                    retry_count = int(row[0] or 0) + 1 if row else 1
                    delay = min(21600, 300 * (2 ** min(retry_count - 1, 6)))
                    next_state = state or "pending"
                    conn.execute(
                        '''UPDATE two_player_games
                           SET result_message_synced = 0,
                               result_delivery_state = ?,
                               result_last_attempt_at = CURRENT_TIMESTAMP,
                               result_delivery_error = ?,
                               result_retry_count = ?,
                               result_next_retry_at = CASE
                                   WHEN ? IN ('manual_review', 'fallback_ambiguous')
                                       THEN NULL
                                   ELSE datetime('now', ?)
                               END,
                               updated_at = CURRENT_TIMESTAMP
                           WHERE game_id = ?''',
                        (
                            next_state,
                            str(error)[:500],
                            retry_count,
                            next_state,
                            f"+{delay} seconds",
                            str(game_id),
                        ),
                    )
        except sqlite3.Error:
            logging.exception("Could not update game result delivery status")

    @staticmethod
    def _is_permanent_game_edit_error(exc: Exception) -> bool:
        detail = str(exc).lower()
        return any(
            marker in detail
            for marker in (
                "message to edit not found",
                "message can't be edited",
                "message can not be edited",
                "message_id_invalid",
            )
        )

    def _claim_game_fallback(self, game_id: str) -> bool:
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                '''UPDATE two_player_games
                   SET result_fallback_attempted = 1,
                       result_delivery_state = 'fallback_sending',
                       result_last_attempt_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE game_id = ? AND status = 'settled'
                     AND result_message_synced = 0
                     AND result_fallback_attempted = 0''',
                (str(game_id),),
            )
            return cursor.rowcount == 1

    def _release_game_fallback_for_retry(
        self,
        game_id: str,
        *,
        retry_after: float,
        error: str,
    ) -> None:
        """Release a fallback claim when Telegram definitively rejected it.

        RetryAfter means Telegram did not accept the send request, so retrying
        later is safe and does not create a duplicate result.
        """
        delay = max(1, min(int(float(retry_after)) + 1, 86400))
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute(
                '''UPDATE two_player_games
                   SET result_fallback_attempted = 0,
                       result_delivery_state = 'pending',
                       result_delivery_error = ?,
                       result_retry_count = result_retry_count + 1,
                       result_next_retry_at = datetime('now', ?),
                       result_last_attempt_at = CURRENT_TIMESTAMP,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE game_id = ? AND status = 'settled'
                     AND result_message_synced = 0''',
                (str(error)[:500], f"+{delay} seconds", str(game_id)),
            )

    @staticmethod
    def _stored_game_result_text(row: sqlite3.Row) -> str:
        creator_id = int(row["creator_id"])
        participant_id = int(row["participant_id"])
        winner_id = int(row["winner_id"])
        creator_balance = int(row["creator_balance_after"] or 0)
        participant_balance = int(row["participant_balance_after"] or 0)
        winner_balance = (
            creator_balance if winner_id == creator_id else participant_balance
        )
        loser_balance = (
            participant_balance if winner_id == creator_id else creator_balance
        )
        settled_text = str(row["settled_at"] or "")
        try:
            settled_utc = datetime.strptime(
                settled_text, "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            settled_display = settled_utc.astimezone(BOT_TIMEZONE).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except (TypeError, ValueError):
            settled_display = settled_text or "-"
        return (
            "🎲 نتیجه بازی دونفره\n\n"
            f"🆔 شناسه بازی: {row['game_id']}\n"
            f"🏆 برنده: {row['winner_name']}\n"
            f"🥀 بازنده: {row['loser_name']}\n"
            f"🎁 جایزه برنده: {int(row['prize_amount'] or 0):,} الماس\n"
            f"🧾 کارمزد بازی: {int(row['fee_amount'] or 0):,} الماس "
            f"({int(row['fee_percent'] or 0)}٪)\n"
            f"💰 موجودی جدید برنده: {winner_balance:,} الماس\n"
            f"💸 موجودی جدید بازنده: {loser_balance:,} الماس\n"
            f"🎯 مبلغ هر نفر: {int(row['diamond_amount']):,} الماس\n"
            f"🕐 زمان تسویه: {settled_display}"
        )

    async def deliver_game_result(self, bot: Bot, game_id: str) -> bool:
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                '''SELECT * FROM two_player_games
                   WHERE game_id = ? AND status = 'settled' ''',
                (str(game_id),),
            ).fetchone()
        if row is None:
            return False
        if int(row["result_message_synced"] or 0):
            return True

        text = self._stored_game_result_text(row)
        markup = self.create_game_result_keyboard(
            int(row["prize_amount"] or 0),
            int(row["fee_amount"] or 0),
            (
                int(row["creator_balance_after"] or 0)
                if int(row["winner_id"]) == int(row["creator_id"])
                else int(row["participant_balance_after"] or 0)
            ),
            (
                int(row["participant_balance_after"] or 0)
                if int(row["winner_id"]) == int(row["creator_id"])
                else int(row["creator_balance_after"] or 0)
            ),
        )
        edit_error = None
        if row["message_id"] is not None:
            try:
                await bot.edit_message_text(
                    chat_id=int(row["chat_id"]),
                    message_id=int(row["message_id"]),
                    text=text,
                    reply_markup=markup,
                )
                self._mark_game_result_synced(
                    game_id,
                    synced=True,
                    result_message_id=int(row["message_id"]),
                )
                return True
            except TelegramError as exc:
                edit_error = exc
                if "message is not modified" in str(exc).lower():
                    self._mark_game_result_synced(
                        game_id,
                        synced=True,
                        result_message_id=int(row["message_id"]),
                    )
                    return True
                if not self._is_permanent_game_edit_error(exc):
                    self._mark_game_result_synced(
                        game_id,
                        synced=False,
                        error=str(exc),
                        state="pending",
                    )
                    return False

        # ارسال جایگزین فقط یک‌بار رزرو می‌شود. خطای Timeout مبهم است و
        # خودکار دوباره ارسال نمی‌شود تا نتیجه تکراری ایجاد نشود.
        if not self._claim_game_fallback(game_id):
            return False
        try:
            kwargs = {}
            if row["message_thread_id"] is not None:
                kwargs["message_thread_id"] = int(row["message_thread_id"])
            sent = await bot.send_message(
                chat_id=int(row["chat_id"]),
                text=text,
                reply_markup=markup,
                **kwargs,
            )
            self._mark_game_result_synced(
                game_id,
                synced=True,
                result_message_id=int(sent.message_id),
            )
            return True
        except RetryAfter as exc:
            self._release_game_fallback_for_retry(
                game_id,
                retry_after=float(exc.retry_after),
                error=str(exc),
            )
            return False
        except TelegramError as exc:
            self._mark_game_result_synced(
                game_id,
                synced=False,
                error=str(exc or edit_error or "fallback delivery failed"),
                state="fallback_ambiguous",
            )
            return False

    async def retry_unsynced_game_results(self, bot: Bot) -> None:
        with db_connect(USERS_DB, timeout=10) as conn:
            # A process crash during fallback send is ambiguous: retrying could
            # duplicate the visible result, so surface it for explicit review.
            conn.execute(
                '''UPDATE two_player_games
                   SET result_delivery_state = 'fallback_ambiguous',
                       result_delivery_error = CASE
                           WHEN TRIM(COALESCE(result_delivery_error, '')) = ''
                               THEN 'process interrupted during fallback send'
                           ELSE result_delivery_error
                       END,
                       result_next_retry_at = NULL,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE status = 'settled'
                     AND result_message_synced = 0
                     AND result_delivery_state = 'fallback_sending'
                     AND result_last_attempt_at <= datetime('now', '-5 minutes')'''
            )
            rows = conn.execute(
                '''SELECT game_id FROM two_player_games
                   WHERE status = 'settled'
                     AND result_message_synced = 0
                     AND result_delivery_state IN ('pending', 'editing')
                     AND (
                         result_next_retry_at IS NULL
                         OR result_next_retry_at <= CURRENT_TIMESTAMP
                     )
                   ORDER BY settled_at ASC
                   LIMIT ?''',
                (BETTING_CLEANUP_BATCH_SIZE,),
            ).fetchall()
        for (game_id,) in rows:
            try:
                await self.deliver_game_result(bot, str(game_id))
            except RetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.5)
                break
            except TelegramError:
                logging.exception("Could not retry completed game result")
            await asyncio.sleep(0.05)

    @staticmethod
    def _stored_game_closure_text(row: sqlite3.Row) -> str:
        title = {
            "canceled": "❌ بازی لغو شد",
            "expired": "⌛ بازی منقضی شد",
            "failed": "⚠️ ساخت بازی ناموفق شد",
        }.get(str(row["status"]), "بازی بسته شد")
        return (
            f"{title}\n\n"
            f"🆔 شناسه بازی: {row['game_id']}\n"
            f"👤 سازنده: {row['creator_name']}\n"
            f"💎 مبلغ برگشتی: {int(row['diamond_amount']):,} الماس\n"
            f"💰 موجودی جدید: {int(row['creator_balance_after'] or 0):,} الماس"
        )

    def _mark_game_closure_synced(
        self,
        game_id: str,
        *,
        synced: bool,
        error: str = "",
    ) -> None:
        with db_connect(USERS_DB, timeout=10) as conn:
            row = conn.execute(
                '''SELECT closure_retry_count FROM two_player_games
                   WHERE game_id = ?''',
                (str(game_id),),
            ).fetchone()
            retry_count = int(row[0] or 0) + (0 if synced else 1) if row else 0
            delay = min(21600, 300 * (2 ** min(max(0, retry_count - 1), 6)))
            conn.execute(
                '''UPDATE two_player_games
                   SET closure_message_synced = ?,
                       closure_retry_count = ?,
                       closure_last_attempt_at = CURRENT_TIMESTAMP,
                       closure_delivery_error = ?,
                       closure_next_retry_at = CASE
                           WHEN ? = 1 THEN NULL
                           ELSE datetime('now', ?)
                       END,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE game_id = ?''',
                (
                    1 if synced else 0,
                    retry_count,
                    "" if synced else str(error)[:500],
                    1 if synced else 0,
                    f"+{delay} seconds",
                    str(game_id),
                ),
            )

    async def deliver_game_closure(self, bot: Bot, game_id: str) -> bool:
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                '''SELECT * FROM two_player_games
                   WHERE game_id = ?
                     AND status IN ('canceled', 'expired', 'failed')''',
                (str(game_id),),
            ).fetchone()
        if row is None:
            return False
        if int(row["closure_message_synced"] or 0):
            return True
        if row["message_id"] is None:
            self._mark_game_closure_synced(game_id, synced=True)
            return True
        try:
            await bot.edit_message_text(
                chat_id=int(row["chat_id"]),
                message_id=int(row["message_id"]),
                text=self._stored_game_closure_text(row),
            )
            self._mark_game_closure_synced(game_id, synced=True)
            return True
        except TelegramError as exc:
            if "message is not modified" in str(exc).lower():
                self._mark_game_closure_synced(game_id, synced=True)
                return True
            self._mark_game_closure_synced(
                game_id,
                synced=False,
                error=str(exc),
            )
            return False

    async def retry_unsynced_game_closures(self, bot: Bot) -> None:
        with db_connect(USERS_DB, timeout=10) as conn:
            rows = conn.execute(
                '''SELECT game_id FROM two_player_games
                   WHERE status IN ('canceled', 'expired', 'failed')
                     AND closure_message_synced = 0
                     AND closure_retry_count < ?
                     AND (
                         closure_next_retry_at IS NULL
                         OR closure_next_retry_at <= CURRENT_TIMESTAMP
                     )
                   ORDER BY COALESCE(expired_at, canceled_at, updated_at) ASC
                   LIMIT ?''',
                (BETTING_CLOSURE_MAX_RETRIES, BETTING_CLEANUP_BATCH_SIZE),
            ).fetchall()
        for (game_id,) in rows:
            try:
                await self.deliver_game_closure(bot, str(game_id))
            except RetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.5)
                break
            await asyncio.sleep(0.05)

    async def expire_waiting_games(self, bot: Bot) -> None:
        with db_connect(USERS_DB, timeout=10) as conn:
            rows = conn.execute(
                '''SELECT game_id FROM two_player_games
                   WHERE status = 'waiting'
                     AND expires_at IS NOT NULL
                     AND expires_at <= CURRENT_TIMESTAMP
                   ORDER BY expires_at ASC
                   LIMIT ?''',
                (BETTING_CLEANUP_BATCH_SIZE,),
            ).fetchall()
        for (game_id,) in rows:
            game_id = str(game_id)
            lock = self._game_operation_lock(game_id)
            async with lock:
                expired = self._refund_waiting_game(
                    game_id=game_id,
                    status="expired",
                    reason="ttl_expired",
                )
                self.active_games.pop(game_id, None)
            if expired:
                try:
                    await self.deliver_game_closure(bot, game_id)
                except RetryAfter as exc:
                    await asyncio.sleep(float(exc.retry_after) + 0.5)
                    break
            self.game_operation_locks.pop(game_id, None)
            await asyncio.sleep(0.05)

    def cleanup_betting_history(self) -> None:
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute(
                '''DELETE FROM two_player_games
                   WHERE status != 'waiting'
                     AND created_at < datetime('now', ?)
                     AND (
                         (status = 'settled' AND result_message_synced = 1)
                         OR
                         (status IN ('canceled', 'expired', 'failed')
                          AND closure_message_synced = 1)
                     )''',
                (f"-{BETTING_HISTORY_RETENTION_DAYS} days",),
            )
            conn.execute(
                '''DELETE FROM balance_transactions
                   WHERE transaction_type LIKE 'betting_%'
                     AND created_at < datetime('now', ?)''',
                (f"-{BETTING_TRANSACTION_RETENTION_DAYS} days",),
            )
            conn.execute(
                '''DELETE FROM system_balance_transactions
                   WHERE transaction_type LIKE 'betting_%'
                     AND created_at < datetime('now', ?)''',
                (f"-{BETTING_TRANSACTION_RETENTION_DAYS} days",),
            )
            conn.execute(
                "DELETE FROM betting_rate_limits WHERE window_started_at < ?",
                (int(time.time()) - 86400,),
            )
            # WAL checkpoint must run after the write transaction is committed.
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    async def betting_cleanup_loop(self, bot: Bot) -> None:
        while True:
            try:
                await self.expire_waiting_games(bot)
                await self.retry_unsynced_game_results(bot)
                await self.retry_unsynced_game_closures(bot)
                if time.time() - self.last_betting_maintenance_at >= 86400:
                    self.cleanup_betting_history()
                    self.last_betting_maintenance_at = time.time()
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Betting cleanup iteration failed")
            await asyncio.sleep(BETTING_CLEANUP_INTERVAL)

    async def create_game(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        user = update.effective_user
        if not message or not user or message.chat.type == "private":
            return
        if not self.betting_chat_allowed(int(message.chat_id)):
            await message.reply_text(
                "❌ شرط‌بندی در این گروه توسط مدیریت فعال نشده است."
            )
            return

        match = re.fullmatch(
            r"\s*بازی\s+([0-9۰-۹٠-٩]+)\s*",
            message.text or "",
        )
        if not match:
            await message.reply_text("❌ فرمت بازی درست نیست.\nمثال: بازی ۱۰")
            return
        try:
            diamond_amount = int(match.group(1))
        except (ValueError, OverflowError):
            await message.reply_text("❌ مقدار الماس باید عدد معتبر باشد.")
            return
        if diamond_amount <= 0:
            await message.reply_text("❌ مقدار الماس باید بیشتر از صفر باشد.")
            return
        if diamond_amount > BETTING_MAX_STAKE:
            await message.reply_text(
                "❌ مبلغ بازی از سقف مجاز بیشتر است.\n"
                f"حداکثر مبلغ هر نفر: {BETTING_MAX_STAKE:,} الماس"
            )
            return

        user_id = int(user.id)
        allowed, retry_after = self.consume_betting_rate_limit(
            user_id=user_id,
            action="create",
            limit=BETTING_CREATE_RATE_LIMIT,
        )
        if not allowed:
            await message.reply_text(
                "⏳ ساخت بازی بیش از حد سریع انجام شد.\n"
                f"{int(retry_after)} ثانیه دیگر تلاش کنید."
            )
            return
        membership_ok, membership_detail, _ = (
            await self.betting_membership_status(context, user_id)
        )
        if not membership_ok:
            await message.reply_text(f"❌ {membership_detail}")
            return
        game_id = secrets.token_urlsafe(6)
        fee_percent = get_financial_config(USERS_DB)["betting_fee_percent"]
        minimum_stake = self.minimum_stake_for_fee(fee_percent)
        if diamond_amount < minimum_stake:
            await message.reply_text(
                "❌ مبلغ بازی برای درصد کارمزد فعلی خیلی کم است.\n"
                f"حداقل مبلغ هر نفر: {minimum_stake:,} الماس"
            )
            return
        potential_prize, potential_fee = self.calculate_game_payout(
            diamond_amount,
            fee_percent,
        )
        creator_name = (
            f"@{user.username}"
            if user.username
            else (user.first_name or f"کاربر {user_id}")
        )
        try:
            reservation = self._reserve_game_funds(
                game_id=game_id,
                creator_id=user_id,
                creator_name=creator_name,
                chat_id=message.chat_id,
                message_thread_id=getattr(message, "message_thread_id", None),
                diamond_amount=diamond_amount,
                fee_percent=fee_percent,
                enforce_rate_limit=False,
            )
        except (sqlite3.Error, OverflowError):
            logging.exception("Could not reserve diamonds for game")
            await message.reply_text(
                "❌ ثبت مالی بازی ناموفق بود؛ هیچ الماسی کسر نشد."
            )
            return

        if not reservation["ok"]:
            if reservation["reason"] == "too_many_open":
                await message.reply_text(
                    "❌ تعداد بازی‌های باز شما به سقف رسیده است.\n"
                    f"حداکثر بازی باز هم‌زمان: {BETTING_MAX_OPEN_GAMES_PER_USER}\n"
                    "یکی از بازی‌های قبلی را لغو کنید یا منتظر پایان آن بمانید."
                )
            elif reservation["reason"] == "rate_limited":
                await message.reply_text(
                    "⏳ ساخت بازی بیش از حد سریع انجام شد.\n"
                    f"{int(reservation['retry_after'])} ثانیه دیگر تلاش کنید."
                )
            else:
                await message.reply_text(
                    "❌ موجودی الماس شما کافی نیست!\n"
                    f"💎 موجودی فعلی: {reservation['balance']:,}\n"
                    f"🎯 مقدار موردنیاز: {diamond_amount:,}"
                )
            return

        game = {
            "creator_id": user_id,
            "creator_name": creator_name,
            "chat_id": int(message.chat_id),
            "message_thread_id": getattr(message, "message_thread_id", None),
            "diamond_amount": diamond_amount,
            "fee_percent": fee_percent,
            "message_id": None,
            "expires_at": reservation["expires_at"],
        }
        self.user_coins[user_id] = int(reservation["balance"])
        self.active_games[game_id] = game
        expires_local = (
            datetime.now(BOT_TIMEZONE) + timedelta(minutes=BETTING_GAME_TTL_MINUTES)
        ).strftime("%H:%M")
        game_text = (
            "🎮 بازی دونفره ساخته شد\n\n"
            f"🆔 شناسه بازی: {game_id}\n"
            f"👤 سازنده: {creator_name}\n"
            f"💎 مقدار بازی: {diamond_amount:,} الماس\n"
            f"🧾 کارمزد: {fee_percent}٪ ({potential_fee:,} الماس)\n"
            f"🎁 جایزه احتمالی برنده: {potential_prize:,} الماس\n"
            f"⌛ اعتبار بازی: {BETTING_GAME_TTL_MINUTES} دقیقه، تا {expires_local}\n"
            "👥 ظرفیت: ۲ نفر\n\n"
            "اولین نفری که دکمه زیر را بزند وارد بازی می‌شود."
        )
        try:
            game_message = await message.reply_text(
                game_text,
                reply_markup=self.create_game_keyboard(game_id),
            )
        except Exception:
            logging.exception("Could not publish two-player game")
            self._refund_waiting_game(
                game_id=game_id,
                status="failed",
                reason="telegram_publish_failed",
            )
            self.active_games.pop(game_id, None)
            await message.reply_text(
                "❌ ساخت بازی ناموفق بود و الماس شما بازگردانده شد."
            )
            return

        game["message_id"] = int(game_message.message_id)
        try:
            with db_connect(USERS_DB, timeout=10) as conn:
                cursor = conn.execute(
                    '''UPDATE two_player_games
                       SET message_id = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE game_id = ? AND status = 'waiting' ''',
                    (int(game_message.message_id), str(game_id)),
                )
                if cursor.rowcount != 1:
                    raise sqlite3.IntegrityError("game is no longer waiting")
        except sqlite3.Error:
            logging.exception("Could not persist game message id")
            self._refund_waiting_game(
                game_id=game_id,
                status="failed",
                reason="message_id_persist_failed",
            )
            self.active_games.pop(game_id, None)
            try:
                await game_message.edit_text(
                    "❌ ثبت بازی کامل نشد و مبلغ سازنده بازگردانده شد."
                )
            except TelegramError:
                pass

    async def cancel_game(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        query = update.callback_query
        if not query:
            return
        game_id = (query.data or "").removeprefix("cancel_game:")
        game = self.get_waiting_game(game_id)
        if not game:
            await query.answer("این بازی دیگر باز نیست.", show_alert=True)
            return
        if int(query.from_user.id) != int(game["creator_id"]):
            await query.answer(
                "فقط سازنده بازی می‌تواند آن را لغو کند.",
                show_alert=True,
            )
            return
        query_message = getattr(query, "message", None)
        query_chat = getattr(query_message, "chat_id", None)
        if query_chat is not None and int(query_chat) != int(game["chat_id"]):
            await query.answer("این دکمه در گروه دیگری معتبر نیست.", show_alert=True)
            return

        allowed, retry_after = self.consume_betting_rate_limit(
            user_id=int(query.from_user.id),
            action="cancel",
            limit=BETTING_CANCEL_RATE_LIMIT,
        )
        if not allowed:
            await query.answer(
                f"لغوهای پیاپی زیاد است؛ {retry_after} ثانیه دیگر تلاش کنید.",
                show_alert=True,
            )
            return

        lock = self._game_operation_lock(game_id)
        async with lock:
            canceled = self._refund_waiting_game(
                game_id=game_id,
                status="canceled",
                reason="creator_canceled",
            )
            self.active_games.pop(game_id, None)
        self.game_operation_locks.pop(game_id, None)
        if not canceled:
            await query.answer("این بازی قبلاً بسته شده است.", show_alert=True)
            return
        await query.answer("✅ بازی لغو و مبلغ بازگردانده شد.")
        await self.deliver_game_closure(context.bot, game_id)

    async def join_game(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        query = update.callback_query
        if not query:
            return
        game_id = (query.data or "").removeprefix("join_game:")
        user = query.from_user
        user_id = int(user.id)

        allowed, retry_after = self.consume_betting_rate_limit(
            user_id=user_id,
            action="join",
            limit=BETTING_JOIN_RATE_LIMIT,
        )
        if not allowed:
            await query.answer(
                f"کلیک‌های پیاپی زیاد است؛ {retry_after} ثانیه دیگر تلاش کنید.",
                show_alert=True,
            )
            return

        membership_ok, membership_detail, _ = (
            await self.betting_membership_status(context, user_id)
        )
        if not membership_ok:
            await query.answer(f"❌ {membership_detail}", show_alert=True)
            return

        game = self.get_waiting_game(game_id)
        if not game:
            await query.answer(
                "❌ این بازی قبلاً تکمیل شده یا در دسترس نیست.",
                show_alert=True,
            )
            return
        if user_id == int(game["creator_id"]):
            await query.answer(
                "❌ نمی‌توانید وارد بازی خودتان شوید.",
                show_alert=True,
            )
            return
        if not self.betting_chat_allowed(int(game["chat_id"])):
            await query.answer(
                "❌ شرط‌بندی در این گروه غیرفعال شده است.",
                show_alert=True,
            )
            return
        query_message = getattr(query, "message", None)
        query_chat = getattr(query_message, "chat_id", None)
        if query_chat is not None and int(query_chat) != int(game["chat_id"]):
            await query.answer(
                "❌ این دکمه فقط در گروه اصلی بازی معتبر است.",
                show_alert=True,
            )
            return

        creator_membership_ok, creator_membership_detail, creator_check_definitive = (
            await self.betting_membership_status(
                context,
                int(game["creator_id"]),
            )
        )
        if not creator_membership_ok and not creator_check_definitive:
            await query.answer(
                "بررسی عضویت سازنده موقتاً ممکن نیست؛ کمی بعد دوباره تلاش کنید.",
                show_alert=True,
            )
            return
        if not creator_membership_ok:
            lock = self._game_operation_lock(game_id)
            async with lock:
                canceled = self._refund_waiting_game(
                    game_id=game_id,
                    status="canceled",
                    reason="creator_left_required_channel",
                )
                self.active_games.pop(game_id, None)
            self.game_operation_locks.pop(game_id, None)
            if canceled:
                await self.deliver_game_closure(context.bot, game_id)
            await query.answer(
                (
                    "سازنده شرایط عضویت اجباری را ندارد؛ مبلغ او برگشت خورد.\n"
                    + creator_membership_detail
                )[:180],
                show_alert=True,
            )
            return

        creator_id = int(game["creator_id"])
        participant_name = (
            f"@{user.username}"
            if user.username
            else (user.first_name or f"کاربر {user_id}")
        )
        participants = [
            (creator_id, str(game["creator_name"])),
            (user_id, participant_name),
        ]
        winner_id, winner_name = secrets.choice(participants)
        loser_id, loser_name = (
            participants[1] if winner_id == creator_id else participants[0]
        )

        lock = self._game_operation_lock(game_id)
        async with lock:
            try:
                result = self._settle_waiting_game(
                    game_id=game_id,
                    participant_id=user_id,
                    participant_name=participant_name,
                    winner_id=winner_id,
                    winner_name=winner_name,
                    loser_id=loser_id,
                    loser_name=loser_name,
                )
            except (sqlite3.Error, OverflowError):
                logging.exception("Could not settle two-player game")
                result = {"ok": False, "reason": "database_error"}
            if result.get("ok") or result.get("reason") in {"expired", "unavailable"}:
                self.active_games.pop(game_id, None)
        if result.get("ok") or result.get("reason") in {"expired", "unavailable"}:
            self.game_operation_locks.pop(game_id, None)

        if not result.get("ok"):
            reason = result.get("reason")
            if reason == "insufficient_balance":
                await query.answer(
                    "❌ موجودی الماس شما کافی نیست.\n"
                    f"💎 موجودی فعلی: {result['balance']:,}\n"
                    f"🎯 مقدار موردنیاز: {result['required']:,}",
                    show_alert=True,
                )
            elif reason == "expired":
                await query.answer(
                    "⌛ زمان این بازی تمام شد و مبلغ سازنده برگشت خورد.",
                    show_alert=True,
                )
                await self.deliver_game_closure(context.bot, game_id)
            elif reason == "database_error":
                await query.answer(
                    "❌ تسویه بازی ناموفق بود؛ دوباره تلاش کنید.",
                    show_alert=True,
                )
            else:
                await query.answer(
                    "❌ این بازی قبلاً تکمیل شده یا در دسترس نیست.",
                    show_alert=True,
                )
            return

        try:
            await query.answer("✅ وارد بازی شدید؛ نتیجه مشخص شد.")
        except TelegramError:
            pass
        await self.deliver_game_result(context.bot, game_id)

    async def game_history_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return
        if not self.is_admin(int(user.id)):
            await message.reply_text("❌ دسترسی به تاریخچه بازی‌ها ندارید.")
            return
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                '''SELECT game_id, creator_name, participant_name,
                          winner_name, diamond_amount, fee_amount, status,
                          created_at, settled_at, canceled_at, expired_at,
                          result_delivery_state, result_retry_count,
                          closure_message_synced, closure_retry_count
                   FROM two_player_games
                   ORDER BY created_at DESC
                   LIMIT 10'''
            ).fetchall()
        if not rows:
            await message.reply_text("هنوز بازی‌ای ثبت نشده است.")
            return
        status_labels = {
            "waiting": "در انتظار",
            "settled": "تسویه‌شده",
            "canceled": "لغوشده",
            "expired": "منقضی",
            "failed": "ناموفق",
        }
        parts = [
            "🎲 آخرین بازی‌های دونفره\n"
            f"🏦 خزانه شرط‌بندی: {self.betting_treasury_balance():,} الماس\n"
        ]
        for row in rows:
            participants = row["creator_name"]
            if row["participant_name"]:
                participants += f" × {row['participant_name']}"
            if row["status"] == "waiting":
                delivery = "-"
            elif row["status"] == "settled":
                delivery = str(row["result_delivery_state"] or "pending")
                if int(row["result_retry_count"] or 0):
                    delivery += f" / تلاش {int(row['result_retry_count'])}"
            else:
                delivery = (
                    "synced"
                    if int(row["closure_message_synced"] or 0)
                    else f"pending / تلاش {int(row['closure_retry_count'] or 0)}"
                )
            parts.append(
                f"\n• {row['game_id']} | "
                f"{status_labels.get(row['status'], row['status'])}\n"
                f"  بازیکنان: {participants}\n"
                f"  مبلغ هر نفر: {int(row['diamond_amount']):,} | "
                f"کارمزد: {int(row['fee_amount'] or 0):,}\n"
                f"  برنده: {row['winner_name'] or '-'} | "
                f"تحویل: {delivery}\n"
                f"  زمان: {row['settled_at'] or row['canceled_at'] or row['expired_at'] or row['created_at']}"
            )
        await message.reply_text("".join(parts))

    async def game_retry_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        user = update.effective_user
        if not message or not user or not self.is_admin(int(user.id)):
            return
        args = list(getattr(context, "args", []) or [])
        if len(args) < 2 or str(args[1]).upper() != "CONFIRM":
            await message.reply_text(
                "⚠️ تلاش دستی ممکن است در خطاهای مبهم، نتیجه را دوباره در گروه "
                "نمایش دهد.\nفرمت تأییدشده:\n/gameretry GAME_ID CONFIRM"
            )
            return
        game_id = str(args[0]).strip()
        validation_error = ""
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                '''SELECT status, result_message_synced, result_delivery_state
                   FROM two_player_games WHERE game_id = ?''',
                (game_id,),
            ).fetchone()
            if row is None:
                validation_error = "❌ بازی پیدا نشد."
            elif str(row[0]) != "settled":
                validation_error = "❌ فقط نتیجه بازی تسویه‌شده قابل ارسال است."
            elif bool(row[1]):
                validation_error = "✅ نتیجه این بازی قبلاً با موفقیت تحویل شده است."
            else:
                previous_state = str(row[2] or "pending")
                conn.execute(
                    '''UPDATE two_player_games
                       SET result_fallback_attempted = 0,
                           result_delivery_state = 'pending',
                           result_next_retry_at = CURRENT_TIMESTAMP,
                           result_delivery_error = ?,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE game_id = ? AND status = 'settled'
                         AND result_message_synced = 0''',
                    (
                        f"manual retry by admin {int(user.id)}; previous={previous_state}",
                        game_id,
                    ),
                )
        if validation_error:
            await message.reply_text(validation_error)
            return
        delivered = await self.deliver_game_result(context.bot, game_id)
        await message.reply_text(
            "✅ نتیجه تحویل شد."
            if delivered
            else "⏳ تلاش ثبت شد؛ وضعیت جدید را با /gamehistory بررسی کنید."
        )

    async def treasury_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        user = update.effective_user
        if not message or not user or not self.is_admin(int(user.id)):
            return
        await message.reply_text(
            f"🏦 موجودی خزانه شرط‌بندی: {self.betting_treasury_balance():,} الماس"
        )

    async def bet_allow_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        user = update.effective_user
        if not message or not user or not self.is_admin(int(user.id)):
            return
        if message.chat.type == "private":
            await message.reply_text("این دستور را داخل گروه موردنظر اجرا کنید.")
            return
        if BETTING_ALLOWED_CHAT_IDS:
            await message.reply_text(
                "⚠️ فهرست گروه‌ها از BETTING_ALLOWED_CHAT_IDS در فایل .env "
                "خوانده می‌شود؛ ابتدا همان تنظیم را تغییر دهید و ربات را ری‌استارت کنید."
            )
            return
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute(
                '''INSERT INTO betting_allowed_chats (
                       chat_id, title, added_by, is_active
                   ) VALUES (?, ?, ?, 1)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       title = excluded.title,
                       added_by = excluded.added_by,
                       is_active = 1''',
                (
                    int(message.chat_id),
                    str(message.chat.title or ""),
                    int(user.id),
                ),
            )
        await message.reply_text("✅ شرط‌بندی برای این گروه فعال شد.")

    async def bet_deny_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        user = update.effective_user
        if not message or not user or not self.is_admin(int(user.id)):
            return
        if message.chat.type == "private":
            await message.reply_text("این دستور را داخل گروه موردنظر اجرا کنید.")
            return
        if BETTING_ALLOWED_CHAT_IDS:
            await message.reply_text(
                "⚠️ فهرست گروه‌ها از BETTING_ALLOWED_CHAT_IDS در فایل .env "
                "خوانده می‌شود؛ ابتدا همان تنظیم را تغییر دهید و ربات را ری‌استارت کنید."
            )
            return
        with db_connect(USERS_DB, timeout=10) as conn:
            conn.execute(
                '''INSERT INTO betting_allowed_chats (
                       chat_id, title, added_by, is_active
                   ) VALUES (?, ?, ?, 0)
                   ON CONFLICT(chat_id) DO UPDATE SET
                       added_by = excluded.added_by,
                       is_active = 0''',
                (
                    int(message.chat_id),
                    str(message.chat.title or ""),
                    int(user.id),
                ),
            )
        await message.reply_text("⛔ شرط‌بندی برای این گروه غیرفعال شد.")

    async def bet_groups_command(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        message = update.effective_message
        user = update.effective_user
        if not message or not user or not self.is_admin(int(user.id)):
            return
        if BETTING_ALLOWED_CHAT_IDS:
            await message.reply_text(
                "گروه‌های مجاز از BETTING_ALLOWED_CHAT_IDS خوانده می‌شوند:\n"
                + "\n".join(str(chat_id) for chat_id in sorted(BETTING_ALLOWED_CHAT_IDS))
            )
            return
        with db_connect(USERS_DB, timeout=10) as conn:
            rows = conn.execute(
                '''SELECT chat_id, title, is_active
                   FROM betting_allowed_chats
                   ORDER BY is_active DESC, created_at DESC
                   LIMIT 30'''
            ).fetchall()
        if not rows:
            await message.reply_text(
                "هیچ گروهی ثبت نشده است؛ برای سازگاری، شرط‌بندی فعلاً در همه گروه‌ها مجاز است.\n"
                "داخل گروه موردنظر /betallow را اجرا کنید تا حالت محدود آغاز شود."
            )
            return
        active_count = sum(1 for _, _, active in rows if active)
        mode_text = (
            "حالت لیست مجاز: فقط گروه‌های ✅ مجازند."
            if active_count
            else "حالت عمومی: همه گروه‌های ثبت‌نشده مجازند و موارد ⛔ مسدودند."
        )
        await message.reply_text(
            "🎲 گروه‌های شرط‌بندی\n" + mode_text + "\n\n"
            + "\n".join(
                f"{'✅' if active else '⛔'} {title or chat_id} — {chat_id}"
                for chat_id, title, active in rows
            )
        )

    @staticmethod
    async def game_result_button(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        """Result buttons are informational and must not mutate a settled game."""
        if update.callback_query:
            await update.callback_query.answer(
                "این دکمه فقط نتیجه نهایی بازی را نمایش می‌دهد."
            )

    async def balance_button(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        query = update.callback_query
        if not query:
            return
        try:
            owner_id = int((query.data or "").split(":", 1)[1])
        except (IndexError, ValueError):
            await query.answer("دکمه موجودی معتبر نیست.", show_alert=True)
            return
        if query.from_user.id != owner_id:
            await query.answer(
                "این موجودی متعلق به کاربر دیگری است.",
                show_alert=True,
            )
            return
        balance = int(self.user_coins.get(owner_id, 0))
        await query.answer(
            f"💰 موجودی فعلی شما: {balance:,} سکه",
            show_alert=True,
        )
    
    async def create_invite_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        
        invite_code = self.get_or_create_invite_code(user_id)
        
        invite_link = f"https://t.me/{context.bot.username}?start={invite_code}"
        referrals_count = self.referral_count(user_id)
        financial = get_financial_config(USERS_DB)
        
        invite_text = (
            f"🎫 **لینک دعوت شما**\n\n"
            f"🔗 لینک: `{invite_link}`\n\n"
            f"💎 **مزایا:**\n"
            f"• به ازای هر دعوت: **{financial['referral_reward']} سکه** پاداش\n"
            f"• دعوت شده: **{financial['new_user_gift']} سکه** هدیه اولیه\n"
            f"• بدون محدودیت تعداد دعوت\n\n"
            f"📊 آمار دعوت‌های شما: {referrals_count} نفر\n"
            f"💰 سکه‌های کسب شده: "
            f"{referrals_count * financial['referral_reward']} سکه"
        )
        
        await update.message.reply_text(invite_text, parse_mode='Markdown')
    
    async def show_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show_balance(update, context)
    
    async def show_balance_farsi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._show_balance(update, context)
    
    async def _show_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        username = update.message.from_user.first_name or "کاربر"
        user_coins = self.user_coins.get(user_id, 0)
        total_value = user_coins * get_financial_config(USERS_DB)["coin_price"]
        current_time = datetime.now().strftime("%H:%M:%S")
        
        balance_text = (
            f"🥃 کاربر: {username}\n"
            f"🚜 موجودی: {user_coins} سکه\n"
            f"🫟 قیمت: {total_value:,} تومن\n"
            f"🍺 ساعت: {current_time}"
        )
        
        await update.message.reply_text(
            balance_text,
            reply_markup=self.create_balance_keyboard(user_id, user_coins),
        )
    
    async def transfer_coins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._transfer_coins(update, context)
    
    async def transfer_coins_farsi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._transfer_coins(update, context)

    @staticmethod
    def normalize_digits(value: str) -> str:
        return str(value or "").translate(
            str.maketrans(
                "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                "01234567890123456789",
            )
        )

    async def complete_coin_transfer(
        self,
        *,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        target_user_id: int,
        target_username: str,
        coin_amount: int,
    ) -> bool:
        message = update.effective_message
        sender = update.effective_user
        if message is None or sender is None:
            return False
        if int(target_user_id) <= 0:
            await message.reply_text("❌ آیدی عددی کاربر معتبر نیست.")
            return False

        target_record = self.get_user_record(target_user_id)
        if target_record is None:
            await message.reply_text(
                "❌ کاربر مقصد هنوز ربات را Start نکرده است."
            )
            return False
        if not target_username:
            target_username = (
                target_record["first_name"]
                or (
                    f"@{target_record['username']}"
                    if target_record["username"]
                    else f"کاربر {int(target_user_id)}"
                )
            )
        try:
            sender_after, target_after, used_today = self.transfer_user_coins(
                sender_id=sender.id,
                target_id=target_user_id,
                amount=coin_amount,
            )
        except (ValueError, LookupError) as exc:
            await message.reply_text(f"❌ {exc}")
            return False

        sender_name = sender.username or sender.first_name or f"user_{sender.id}"
        transfer_text = (
            "💸 انتقال الماس انجام شد\n\n"
            f"👤 از: {sender_name}\n"
            f"👥 به: {target_username}\n"
            f"💎 مقدار: {coin_amount:,} الماس\n"
            f"💵 ارزش: "
            f"{coin_amount * get_financial_config(USERS_DB)['coin_price']:,} تومان\n"
            f"💰 موجودی جدید شما: {sender_after:,} الماس\n"
            f"📊 انتقال ۲۴ ساعت اخیر: {used_today:,} الماس\n"
            f"🕐 زمان: {datetime.now().strftime('%H:%M:%S')}"
        )
        await message.reply_text(transfer_text)

        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"🎉 شما {coin_amount:,} الماس از کاربر "
                    f"{sender_name} دریافت کردید.\n"
                    f"💰 موجودی جدید: {target_after:,} الماس"
                ),
            )
        except TelegramError:
            pass
        return True
    
    async def _transfer_coins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        message_text = message.text or ""
        try:
            if message_text.startswith('/transfer') and context.args:
                coin_amount = int(self.normalize_digits(context.args[0]))
            elif message_text.startswith('انتقال'):
                parts = message_text.split()
                if len(parts) >= 2:
                    coin_amount = int(self.normalize_digits(parts[1]))
                else:
                    raise ValueError
            else:
                await message.reply_text(
                    "❌ فرمت دستور نادرست است!\n"
                    "مثال: `انتقال 10` یا `/transfer 10`"
                )
                return
        except (ValueError, IndexError):
            await message.reply_text(
                "❌ لطفاً تعداد الماس را به‌درستی مشخص کنید:\n"
                "مثال: `انتقال 10` یا `/transfer 10`"
            )
            return

        if coin_amount <= 0:
            await message.reply_text("❌ تعداد الماس باید بیشتر از صفر باشد.")
            return

        if not message.reply_to_message:
            if message.chat.type not in {"group", "supergroup"}:
                await message.reply_text(
                    "❌ دستور را روی پیام کاربر مقصد ریپلای کنید."
                )
                return
            self.pending_coin_transfers[(message.chat_id, user.id)] = {
                "amount": coin_amount,
                "created_at": time.monotonic(),
            }
            await message.reply_text(
                f"🆔 آیدی عددی کاربر را برای انتقال "
                f"{coin_amount:,} الماس وارد کنید.\n"
                "برای لغو بنویسید: «لغو انتقال»"
            )
            return

        target_user = message.reply_to_message.from_user
        if target_user is None:
            await message.reply_text("❌ کاربر مقصد از پیام ریپلای‌شده پیدا نشد.")
            return
        if getattr(target_user, "is_bot", False):
            await message.reply_text(
                "❌ انتقال الماس به حساب ربات امکان‌پذیر نیست."
            )
            return

        await self.complete_coin_transfer(
            update=update,
            context=context,
            target_user_id=target_user.id,
            target_username=target_user.first_name or "کاربر",
            coin_amount=coin_amount,
        )

    async def receive_transfer_target(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        pending_key = (message.chat_id, user.id)
        pending = self.pending_coin_transfers.get(pending_key)
        if not pending:
            return
        if time.monotonic() - float(pending["created_at"]) > 180:
            self.pending_coin_transfers.pop(pending_key, None)
            await message.reply_text(
                "⌛ درخواست انتقال منقضی شد؛ دوباره دستور «انتقال مقدار» "
                "را بفرستید."
            )
            return
        target_text = self.normalize_digits(message.text or "")
        if not target_text.isdigit():
            return
        target_user_id = int(target_text)
        self.pending_coin_transfers.pop(pending_key, None)
        await self.complete_coin_transfer(
            update=update,
            context=context,
            target_user_id=target_user_id,
            target_username="",
            coin_amount=int(pending["amount"]),
        )

    async def cancel_pending_transfer(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        removed = self.pending_coin_transfers.pop(
            (message.chat_id, user.id),
            None,
        )
        await message.reply_text(
            "✅ انتقال لغو شد."
            if removed
            else "درخواست انتقال فعالی برای شما وجود ندارد."
        )
    
    async def kasr_coins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ شما دسترسی به این دستور را ندارید!")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text(
                "❌ لطفاً روی پیام کاربر مورد نظر ریپلای کنید و دستور را ارسال نمایید:\n"
                "مثال: `/kasr 10`"
            )
            return
        
        if not context.args:
            await update.message.reply_text(
                "❌ لطفاً تعداد سکه را مشخص کنید:\n"
                "مثال: `/kasr 10`"
            )
            return
        
        try:
            coin_amount = int(context.args[0])
            if coin_amount <= 0:
                await update.message.reply_text("❌ تعداد سکه باید بیشتر از صفر باشد!")
                return
            
            target_user = update.message.reply_to_message.from_user
            target_user_id = target_user.id
            target_username = target_user.first_name or "کاربر"
            
            with db_connect(USERS_DB, timeout=10) as conn:
                row = conn.execute(
                    "SELECT coins FROM users WHERE user_id = ?",
                    (target_user_id,),
                ).fetchone()
            current_coins = int(row[0] or 0) if row else 0
            coins_to_deduct = min(current_coins, coin_amount)
            if coins_to_deduct <= 0:
                await update.message.reply_text("❌ موجودی کاربر صفر است.")
                return
            new_balance = self.admin_store.adjust_balance(
                target_user_id, -coins_to_deduct, user_id,
                "کسر سکه با دستور /kasr",
            )
            self.user_coins[target_user_id] = new_balance
            
            kasr_text = (
                "⚡ کسر سکه توسط ادمین\n\n"
                f"👤 کاربر: {target_username}\n"
                f"🆔 آیدی: {target_user_id}\n"
                f"💰 مبلغ کسر شده: {coins_to_deduct} سکه\n"
                f"💎 موجودی جدید: {new_balance} سکه\n"
                f"🕐 زمان: {datetime.now().strftime('%H:%M:%S')}"
            )
            
            await update.message.reply_text(kasr_text)
            
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"⚠️ {coins_to_deduct} سکه از حساب شما توسط مدیریت کسر شد!\n"
                         f"💰 موجودی جدید: {new_balance} سکه"
                )
            except:
                pass
                
        except ValueError:
            await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید!")
    
    async def add_coins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ شما دسترسی به این دستور را ندارید!")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text(
                "❌ لطفاً روی پیام کاربر مورد نظر ریپلای کنید و دستور را ارسال نمایید:\n"
                "مثال: `/addcoins 10`"
            )
            return
        
        if not context.args:
            await update.message.reply_text(
                "❌ لطفاً تعداد سکه را مشخص کنید:\n"
                "مثال: `/addcoins 10`"
            )
            return
        
        try:
            coin_amount = int(context.args[0])
            if coin_amount <= 0:
                await update.message.reply_text("❌ تعداد سکه باید بیشتر از صفر باشد!")
                return
            
            target_user = update.message.reply_to_message.from_user
            target_user_id = target_user.id
            target_username = target_user.first_name or "کاربر"
            self.register_user_profile(target_user)
            new_balance = self.gift_user_coins(
                user_id=target_user_id,
                amount=coin_amount,
                admin_id=user_id,
            )
            
            add_text = (
                "🎁 افزودن سکه توسط ادمین\n\n"
                f"👤 کاربر: {target_username}\n"
                f"🆔 آیدی: {target_user_id}\n"
                f"💰 مبلغ افزوده شده: {coin_amount} سکه\n"
                f"💎 موجودی جدید: {new_balance} سکه\n"
                f"🕐 زمان: {datetime.now().strftime('%H:%M:%S')}"
            )
            
            await update.message.reply_text(add_text)
            
            try:
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text=f"🎉 {coin_amount} سکه توسط مدیریت به حساب شما افزوده شد!\n"
                         f"💰 موجودی جدید: {new_balance} سکه"
                )
            except:
                pass
                
        except ValueError:
            await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید!")
    
    async def get_user_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.message.from_user.id
        
        if not self.is_admin(user_id):
            await update.message.reply_text("❌ شما دسترسی به این دستور را ندارید!")
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text(
                "❌ لطفاً روی پیام کاربر مورد نظر ریپلای کنید و دستور را ارسال نمایید:\n"
                "مثال: `/id`"
            )
            return
        
        target_user = update.message.reply_to_message.from_user
        target_user_id = target_user.id
        target_username = target_user.username or "ندارد"
        target_first_name = target_user.first_name or "ندارد"
        target_last_name = target_user.last_name or "ندارد"
        
        user_coins = self.user_coins.get(target_user_id, 0)
        self_status = self.selfbot_status_info(
            self.get_selfbot_record(target_user_id)
        )[1]
        total_value = (
            user_coins * get_financial_config(USERS_DB)["coin_price"]
        )
        
        user_info_text = (
            f"👤 **اطلاعات کاربر**\n\n"
            f"🆔 **آیدی عددی:** `{target_user_id}`\n"
            f"👁️ **نام کاربری:** @{target_username}\n"
            f"📛 **نام:** {target_first_name}\n"
            f"📛 **نام خانوادگی:** {target_last_name}\n"
            f"💰 **تعداد سکه:** {user_coins}\n"
            f"💎 **ارزش سکه‌ها:** {total_value:,} تومن\n"
            f"🎯 **وضعیت سلف:** {self_status}\n"
            f"📊 **تعداد دعوت‌ها:** {self.referral_count(target_user_id)}\n"
            f"🕐 **زمان:** {datetime.now().strftime('%H:%M:%S')}"
        )
        
        await update.message.reply_text(user_info_text, parse_mode='Markdown')
    
    def run(self):
        print("🤖 ربات SelfStruct System در حال اجراست...")
        print("🔑 API ID:", self.api_id)
        print("👑 مالک ربات:", self.owner_id)
        print(f"🏦 خزانه شرط‌بندی: {self.betting_treasury_balance():,} سکه")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

# تنظیمات اصلی
if __name__ == "__main__":
    BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
    API_ID = os.getenv("TELEGRAM_API_ID", "").strip()
    API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
    OWNER_ID = os.getenv("OWNER_ID", "").strip()

    missing_settings = [
        name
        for name, value in (
            ("BOT_TOKEN", BOT_TOKEN),
            ("TELEGRAM_API_ID", API_ID),
            ("TELEGRAM_API_HASH", API_HASH),
            ("OWNER_ID", OWNER_ID),
        )
        if not value
    ]
    if missing_settings:
        raise RuntimeError(
            "تنظیمات اجباری وارد نشده‌اند: " + ", ".join(missing_settings)
        )
    
    bot = TelegramAuthBot(BOT_TOKEN, API_ID, API_HASH)
    bot.run()

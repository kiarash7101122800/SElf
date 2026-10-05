"""Shared SQLite helpers for the main bot, helper bot, and self-bots."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_SELF_SETTINGS = {
    "timename": "off",
    "timebio": "off",
    "bot": "on",
    "hashtag": "off",
    "bold": "off",
    "italic": "off",
    "delete": "off",
    "code": "off",
    "underline": "off",
    "reverse": "off",
    "part": "off",
    "mention": "off",
    "comment": "on",
    "text": "first !",
    "typing": "off",
    "voice": "off",
    "video": "off",
    "sticker": "off",
    "font": "1",
    "original_bio": "",
    "secretary": "off",
    "auto_reply": "off",
    "secretary_fallback_text": (
        "سلام 🌹 پیام شما دریافت شد. لطفاً موضوع درخواستتان را بنویسید."
    ),
    "secretary_fallback_cooldown_minutes": "60",
    "offline_reply_enabled": "off",
    "offline_reply_text": (
        "سلام 🌹 در حال حاضر آفلاین هستم؛ پیام شما دریافت شد و "
        "در اولین فرصت پاسخ می‌دهم."
    ),
    "offline_reply_cooldown_minutes": "360",
    "online_status": "on",
    "presence_emoji_enabled": "off",
    "presence_auto_detect": "on",
    "online_name_emoji": "🟢",
    "offline_name_emoji": "🔴",
    "profile_base_first_name": "",
    "presence_last_state": "unknown",
    "send_queue_min_interval_ms": "900",
    "typing_action": "off",
    "typing_duration": "5",
    "auto_forward": "off",
    "save_timed_photos": "on",
    "anti_delete_enabled": "on",
    "anti_delete_private": "on",
    "anti_delete_groups": "on",
    "anti_delete_channels": "off",
    "anti_delete_max_mb": "50",
    "anti_delete_retention_days": "7",
    "scheduled_message_enabled": "off",
    "scheduled_message_target": "",
    "scheduled_message_text": "",
    "scheduled_message_interval_minutes": "5",
    # v2.2+ feature switches.  All values are strings because the historical
    # per-account settings table stores text and older databases must remain
    # directly readable.
    "force_join_private": "off",
    "auto_read_private": "off",
    "auto_read_groups": "off",
    "auto_reaction": "off",
    "auto_reaction_emoji": "❤️",
    "relationship_reaction": "off",
    "friend_reaction_emoji": "❤️",
    "enemy_reaction_emoji": "👎",
    "friend_affection_reply": "on",
    "enemy_hostile_reply": "on",
    "outgoing_text_style": "none",
    "outgoing_signature_enabled": "off",
    "outgoing_signature_text": "",
    "lock_links": "off",
    "lock_forwards": "off",
    "lock_photos": "off",
    "lock_videos": "off",
    "lock_gifs": "off",
    "lock_stickers": "off",
    "lock_voice": "off",
    "lock_files": "off",
    "lock_polls": "off",
    "word_filter_enabled": "off",
    "word_filter_action": "delete",
    "word_filter_mute_minutes": "10",
    "watermark_text": "",
    "translate_default_language": "fa",
    "tts_voice": "female",
    "profile_monitor_enabled": "off",
    "profile_monitor_interval_minutes": "10",
    "first_comment_enabled": "off",
    "first_comment_delay_seconds": "2",
    "first_comment_text": "اولین کامنت ✨",
    "safe_download_max_mb": "50",
    "form_builder_enabled": "off",
    "form_intro_text": (
        "سلام 🌹 برای ثبت درخواست، نام یکی از فرم‌های زیر را ارسال کنید:"
    ),
    # v2.8 advanced security, message, profile, group, and panel settings.
    "panel_language": "fa",
    "private_lock_enabled": "off",
    "private_lock_delete_unknown": "on",
    "private_lock_warn_before_block": "on",
    "private_lock_warning_limit": "1",
    "private_lock_warning_text": (
        "⛔ پیام خصوصی این حساب بسته است. لطفاً بدون هماهنگی پیام ندهید."
    ),
    "anti_edit_private": "off",
    "anti_edit_groups": "off",
    "anti_edit_notify_saved": "on",
    "welcome_enabled": "off",
    "welcome_text": (
        "سلام {name} عزیز، به {chat} خوش آمدی 🌹"
    ),
    "goodbye_enabled": "off",
    "goodbye_text": (
        "{name} از {chat} خارج شد."
    ),
    "action_default_duration": "5",
    "profile_backup_enabled": "on",
    "analog_clock_enabled": "off",
    "analog_clock_update_minutes": "5",
    "analog_clock_generated_photo_id": "",
    "timename_font": "1",
    "timebio_font": "1",
    "original_last_name": "",
    "timename_applied": "off",
    "timebio_applied": "off",
    "group_report_admin_limit": "10",
    "chatgpt_daily_limit": "0",
    "chatgpt_model": "",
}

DEFAULT_APP_SETTINGS = {
    "helper_enabled": "0",
    "bot_display_name": "GardTeam",
    # The previous hard-coded force-join target is migrated to the channel
    # requested for this deployment. Administrators can change or disable it
    # later from /admin without editing the source.
    "force_join_enabled": "1",
    "force_join_chat_id": "@gardtem",
    "force_join_username": "gardtem",
    "force_join_title": "GardTeam",
    "force_join_url": "https://t.me/gardtem",
    # Financial and user-facing values are deliberately stored in the shared
    # settings table so they can be changed from /admin without editing code.
    "coin_price_toman": "200",
    "payment_card_number": "6104337952046736",
    "payment_card_holder": "",
    "payment_contact_username": "sinyoremad",
    "activation_cost_coins": "3",
    "daily_self_cost_coins": "0",
    "new_user_gift_coins": "3",
    "referral_reward_coins": "7",
    "transfer_min_coins": "1",
    "transfer_max_coins": "1000",
    "transfer_daily_limit_coins": "5000",
    # Percentage deducted from the complete two-player pot. Zero preserves
    # the historical no-commission behaviour for existing installations.
    "betting_fee_percent": "0",
    "support_url": "https://t.me/gardtem",
    "support_text": (
        "برای ارتباط با پشتیبانی از دکمه زیر استفاده کنید. "
        "پشتیبانی در اولین فرصت پاسخ می‌دهد."
    ),
    "rules_text": (
        "۱) مسئولیت استفاده از حساب و سلف بر عهده کاربر است.\n"
        "۲) پیش از پرداخت، تعداد سکه و مبلغ را بررسی کنید.\n"
        "۳) رسید تکراری یا نامعتبر تأیید نمی‌شود."
    ),
    # Telegram destinations and public identities used by the self-bot are
    # centralized here. They remain compatible with older installations while
    # becoming editable from the main bot's /admin panel.
    "receipt_admin_ids": "",
    "self_admin_target": "@SAHAND_ADLER",
    "self_group_target": "@Self_saz_Chat",
    "self_channel_target": "@COD_MAZ",
    # Kept under the legacy key so existing installations are migrated without
    # losing their configured replacement. The value may be either a Telegram
    # username or arbitrary display text.
    "brand_powered_by_username": "Sourrce_kade",
    "brand_owner_username": "sinyouremad",
    "brand_self_username": "SelfDoppelBot",
    "brand_group_username": "DoppelGAP",
}


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback and close the SQLite handle at context exit."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def restrict_file_permissions(path: str | Path) -> None:
    try:
        Path(path).chmod(0o600)
    except OSError:
        pass


def ensure_app_settings(users_db: str | Path) -> None:
    with connect(users_db) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS app_settings (
                   key TEXT PRIMARY KEY,
                   value TEXT NOT NULL DEFAULT '',
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.executemany(
            """INSERT OR IGNORE INTO app_settings (key, value)
               VALUES (?, ?)""",
            list(DEFAULT_APP_SETTINGS.items()),
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS app_admins (
                   user_id INTEGER PRIMARY KEY,
                   added_by INTEGER NOT NULL,
                   added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
    restrict_file_permissions(users_db)


def get_app_settings(
    users_db: str | Path,
    *keys: str,
) -> dict[str, str]:
    ensure_app_settings(users_db)
    with connect(users_db) as connection:
        if keys:
            placeholders = ",".join("?" for _ in keys)
            rows = connection.execute(
                f"SELECT key, value FROM app_settings WHERE key IN ({placeholders})",
                keys,
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT key, value FROM app_settings"
            ).fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def set_app_settings(
    users_db: str | Path,
    values: Mapping[str, Any],
) -> None:
    ensure_app_settings(users_db)
    with connect(users_db) as connection:
        connection.executemany(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = CURRENT_TIMESTAMP""",
            [(str(key), str(value)) for key, value in values.items()],
        )
    restrict_file_permissions(users_db)


def get_helper_config(users_db: str | Path) -> dict[str, Any]:
    settings = get_app_settings(
        users_db,
        "helper_token",
        "helper_username",
        "helper_bot_id",
        "helper_enabled",
        "helper_pid",
    )
    return {
        "token": settings.get("helper_token", "").strip(),
        "username": settings.get("helper_username", "").strip().lstrip("@"),
        "bot_id": settings.get("helper_bot_id", "").strip(),
        "enabled": settings.get("helper_enabled", "0") == "1",
        "pid": _safe_int(settings.get("helper_pid")),
    }


def get_force_join_config(users_db: str | Path) -> dict[str, Any]:
    settings = get_app_settings(
        users_db,
        "force_join_enabled",
        "force_join_chat_id",
        "force_join_username",
        "force_join_title",
        "force_join_url",
    )
    chat_id = settings.get("force_join_chat_id", "").strip()
    username = settings.get("force_join_username", "").strip().lstrip("@")
    join_url = settings.get("force_join_url", "").strip()
    title = settings.get("force_join_title", "").strip()
    return {
        "enabled": settings.get("force_join_enabled", "0") == "1",
        "chat_id": chat_id,
        "username": username,
        "title": title or (f"@{username}" if username else "کانال عضویت اجباری"),
        "join_url": join_url,
        "configured": bool(chat_id and join_url),
    }


def get_financial_config(users_db: str | Path) -> dict[str, Any]:
    settings = get_app_settings(
        users_db,
        "coin_price_toman",
        "payment_card_number",
        "payment_card_holder",
        "payment_contact_username",
        "activation_cost_coins",
        "daily_self_cost_coins",
        "new_user_gift_coins",
        "referral_reward_coins",
        "transfer_min_coins",
        "transfer_max_coins",
        "transfer_daily_limit_coins",
        "betting_fee_percent",
        "support_url",
    )
    return {
        "coin_price": _bounded_int(
            settings.get("coin_price_toman"),
            default=200,
            minimum=1,
        ),
        "card_number": settings.get("payment_card_number", "").strip(),
        "card_holder": settings.get("payment_card_holder", "").strip(),
        "payment_contact": settings.get(
            "payment_contact_username",
            "",
        ).strip().lstrip("@"),
        "activation_cost": _bounded_int(
            settings.get("activation_cost_coins"),
            default=3,
            minimum=0,
        ),
        "daily_self_cost": _bounded_int(
            settings.get("daily_self_cost_coins"),
            default=0,
            minimum=0,
        ),
        "new_user_gift": _bounded_int(
            settings.get("new_user_gift_coins"),
            default=3,
            minimum=0,
        ),
        "referral_reward": _bounded_int(
            settings.get("referral_reward_coins"),
            default=7,
            minimum=0,
        ),
        "transfer_min": _bounded_int(
            settings.get("transfer_min_coins"),
            default=1,
            minimum=1,
        ),
        "transfer_max": _bounded_int(
            settings.get("transfer_max_coins"),
            default=1000,
            minimum=1,
        ),
        "transfer_daily_limit": _bounded_int(
            settings.get("transfer_daily_limit_coins"),
            default=5000,
            minimum=0,
        ),
        "betting_fee_percent": _bounded_int(
            settings.get("betting_fee_percent"),
            default=0,
            minimum=0,
            maximum=50,
        ),
        "support_url": settings.get("support_url", "").strip(),
    }


def get_content_config(users_db: str | Path) -> dict[str, str]:
    settings = get_app_settings(
        users_db,
        "support_url",
        "support_text",
        "rules_text",
    )
    return {
        "support_url": settings.get("support_url", "").strip(),
        "support_text": settings.get("support_text", "").strip(),
        "rules_text": settings.get("rules_text", "").strip(),
    }


def get_brand_config(users_db: str | Path) -> dict[str, str]:
    settings = get_app_settings(
        users_db,
        "bot_display_name",
    )
    return {
        "bot_display_name": (
            settings.get("bot_display_name", "").strip() or "GardTeam"
        ),
    }


def get_identity_config(users_db: str | Path) -> dict[str, Any]:
    settings = get_app_settings(
        users_db,
        "receipt_admin_ids",
        "payment_contact_username",
        "self_admin_target",
        "self_group_target",
        "self_channel_target",
        "brand_powered_by_username",
        "brand_owner_username",
        "brand_self_username",
        "brand_group_username",
    )
    receipt_admin_ids = []
    for item in settings.get("receipt_admin_ids", "").split(","):
        try:
            parsed = int(item.strip())
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in receipt_admin_ids:
            receipt_admin_ids.append(parsed)
    return {
        "receipt_admin_ids": receipt_admin_ids,
        "payment_contact": settings.get(
            "payment_contact_username",
            "",
        ).strip().lstrip("@"),
        "self_admin_target": settings.get(
            "self_admin_target",
            "",
        ).strip(),
        "self_group_target": settings.get(
            "self_group_target",
            "",
        ).strip(),
        "self_channel_target": settings.get(
            "self_channel_target",
            "",
        ).strip(),
        "brand_powered_by": settings.get(
            "brand_powered_by_username",
            "",
        ).strip(),
        "brand_owner": settings.get(
            "brand_owner_username",
            "",
        ).strip().lstrip("@"),
        "brand_self": settings.get(
            "brand_self_username",
            "",
        ).strip().lstrip("@"),
        "brand_group": settings.get(
            "brand_group_username",
            "",
        ).strip().lstrip("@"),
    }


def get_admin_ids(
    users_db: str | Path,
    owner_id: int | None = None,
) -> set[int]:
    ensure_app_settings(users_db)
    with connect(users_db) as connection:
        rows = connection.execute(
            "SELECT user_id FROM app_admins ORDER BY added_at, user_id"
        ).fetchall()
    admin_ids = {int(row["user_id"]) for row in rows}
    if owner_id is not None:
        admin_ids.add(int(owner_id))
    return admin_ids


def add_admin(
    users_db: str | Path,
    user_id: int,
    added_by: int,
) -> None:
    ensure_app_settings(users_db)
    with connect(users_db) as connection:
        connection.execute(
            """INSERT INTO app_admins (user_id, added_by)
               VALUES (?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   added_by = excluded.added_by,
                   added_at = CURRENT_TIMESTAMP""",
            (int(user_id), int(added_by)),
        )


def remove_admin(users_db: str | Path, user_id: int) -> None:
    ensure_app_settings(users_db)
    with connect(users_db) as connection:
        connection.execute(
            "DELETE FROM app_admins WHERE user_id = ?",
            (int(user_id),),
        )


def get_active_user(users_db: str | Path, user_id: int) -> dict[str, Any] | None:
    if not Path(users_db).is_file():
        return None

    try:
        with connect(users_db) as connection:
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(users)")
            }
            optional_columns = [
                (
                    column
                    if column in columns
                    else f"NULL AS {column}"
                )
                for column in ("username", "first_name", "last_name")
            ]
            row = connection.execute(
                f"""SELECT user_id, {", ".join(optional_columns)},
                           phone, is_active, expiration_date,
                           self_pid, session_file, updated_at
                    FROM users
                    WHERE user_id = ?""",
                (int(user_id),),
            ).fetchone()
    except sqlite3.OperationalError:
        return None

    return dict(row) if row else None


def self_database_path(data_dir: str | Path, phone: str) -> Path:
    safe_phone = str(phone).replace("+", "").strip()
    if not safe_phone or any(char in safe_phone for char in ("/", "\\", "\0")):
        raise ValueError("شماره حساب برای ساخت مسیر دیتابیس معتبر نیست.")
    return Path(data_dir) / f"bot_data_{safe_phone}.db"


def ensure_self_settings(data_dir: str | Path, phone: str) -> Path:
    db_path = self_database_path(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                   key TEXT PRIMARY KEY,
                   value TEXT
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS secretary (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   pattern TEXT,
                   response TEXT,
                   is_active INTEGER DEFAULT 1
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS auto_reply_rules (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   scope TEXT NOT NULL DEFAULT 'private',
                   match_mode TEXT NOT NULL DEFAULT 'contains',
                   priority INTEGER NOT NULL DEFAULT 100,
                   cooldown_seconds INTEGER NOT NULL DEFAULT 30,
                   is_active INTEGER NOT NULL DEFAULT 1,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS auto_reply_triggers (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   rule_id INTEGER NOT NULL,
                   trigger_text TEXT NOT NULL COLLATE NOCASE,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY(rule_id) REFERENCES auto_reply_rules(id)
                       ON DELETE CASCADE,
                   UNIQUE(rule_id, trigger_text)
               )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_auto_reply_triggers_rule
               ON auto_reply_triggers(rule_id, id)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS auto_reply_responses (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   rule_id INTEGER NOT NULL,
                   response_type TEXT NOT NULL DEFAULT 'text',
                   content_text TEXT NOT NULL DEFAULT '',
                   media_path TEXT NOT NULL DEFAULT '',
                   caption TEXT NOT NULL DEFAULT '',
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY(rule_id) REFERENCES auto_reply_rules(id)
                       ON DELETE CASCADE
               )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_auto_reply_responses_rule
               ON auto_reply_responses(rule_id, id)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS friends (
                   user_id INTEGER PRIMARY KEY,
                   added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS friend_affection_replies (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   response TEXT NOT NULL COLLATE NOCASE UNIQUE,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS enemy (
                   user_id INTEGER PRIMARY KEY
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS enemy_hostile_replies (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   response TEXT NOT NULL COLLATE NOCASE UNIQUE,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS word_filters (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   phrase TEXT NOT NULL COLLATE NOCASE UNIQUE,
                   action TEXT NOT NULL DEFAULT 'delete',
                   is_active INTEGER NOT NULL DEFAULT 1,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS tracked_profiles (
                   user_id INTEGER PRIMARY KEY,
                   label TEXT NOT NULL DEFAULT '',
                   snapshot_json TEXT NOT NULL DEFAULT '{}',
                   is_active INTEGER NOT NULL DEFAULT 1,
                   last_checked_at TEXT,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS first_comment_channels (
                   chat_id TEXT PRIMARY KEY,
                   comment_text TEXT NOT NULL,
                   delay_seconds INTEGER NOT NULL DEFAULT 2,
                   is_active INTEGER NOT NULL DEFAULT 1,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS voice_library (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   keyword TEXT NOT NULL COLLATE NOCASE,
                   file_path TEXT NOT NULL,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS message_archive (
                   chat_id INTEGER NOT NULL,
                   message_id INTEGER NOT NULL,
                   sender_id INTEGER NOT NULL DEFAULT 0,
                   sender_name TEXT NOT NULL DEFAULT '',
                   chat_title TEXT NOT NULL DEFAULT '',
                   message_text TEXT NOT NULL DEFAULT '',
                   media_type TEXT NOT NULL DEFAULT 'text',
                   media_path TEXT NOT NULL DEFAULT '',
                   media_name TEXT NOT NULL DEFAULT '',
                   media_size INTEGER NOT NULL DEFAULT 0,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   PRIMARY KEY (chat_id, message_id)
               )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_message_archive_message_id
               ON message_archive(message_id)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_message_archive_created_at
               ON message_archive(created_at)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS form_templates (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   name TEXT NOT NULL,
                   trigger_text TEXT NOT NULL COLLATE NOCASE UNIQUE,
                   is_active INTEGER NOT NULL DEFAULT 1,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS form_fields (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   form_id INTEGER NOT NULL,
                   question TEXT NOT NULL,
                   position INTEGER NOT NULL,
                   required INTEGER NOT NULL DEFAULT 1,
                   FOREIGN KEY(form_id) REFERENCES form_templates(id)
                       ON DELETE CASCADE,
                   UNIQUE(form_id, position)
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS form_sessions (
                   user_id INTEGER PRIMARY KEY,
                   chat_id INTEGER NOT NULL,
                   form_id INTEGER NOT NULL,
                   current_index INTEGER NOT NULL DEFAULT 0,
                   stage TEXT NOT NULL DEFAULT 'answering',
                   answers_json TEXT NOT NULL DEFAULT '[]',
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS form_submissions (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   form_id INTEGER NOT NULL,
                   form_name TEXT NOT NULL,
                   user_id INTEGER NOT NULL,
                   chat_id INTEGER NOT NULL,
                   summary_text TEXT NOT NULL,
                   answers_json TEXT NOT NULL DEFAULT '[]',
                   status TEXT NOT NULL DEFAULT 'processing',
                   customer_message_id INTEGER,
                   admin_message_id INTEGER,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_form_submissions_admin_message
               ON form_submissions(admin_message_id)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_form_submissions_customer_message
               ON form_submissions(chat_id, customer_message_id)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS private_allowlist (
                   user_id INTEGER PRIMARY KEY,
                   label TEXT NOT NULL DEFAULT '',
                   added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS private_lock_attempts (
                   user_id INTEGER PRIMARY KEY,
                   warning_count INTEGER NOT NULL DEFAULT 0,
                   last_warning_at TEXT,
                   blocked_at TEXT
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS message_versions (
                   chat_id INTEGER NOT NULL,
                   message_id INTEGER NOT NULL,
                   sender_id INTEGER NOT NULL DEFAULT 0,
                   sender_name TEXT NOT NULL DEFAULT '',
                   chat_title TEXT NOT NULL DEFAULT '',
                   message_text TEXT NOT NULL DEFAULT '',
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   PRIMARY KEY (chat_id, message_id)
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS message_edits (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   chat_id INTEGER NOT NULL,
                   message_id INTEGER NOT NULL,
                   sender_id INTEGER NOT NULL DEFAULT 0,
                   sender_name TEXT NOT NULL DEFAULT '',
                   chat_title TEXT NOT NULL DEFAULT '',
                   before_text TEXT NOT NULL DEFAULT '',
                   after_text TEXT NOT NULL DEFAULT '',
                   scope TEXT NOT NULL DEFAULT 'private',
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_message_edits_created_at
               ON message_edits(created_at)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS scheduled_once (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   target TEXT NOT NULL,
                   message_text TEXT NOT NULL,
                   send_at TEXT NOT NULL,
                   reply_to_message_id INTEGER,
                   status TEXT NOT NULL DEFAULT 'pending',
                   error_text TEXT NOT NULL DEFAULT '',
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   sent_at TEXT
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schedule_jobs (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   target TEXT NOT NULL,
                   message_type TEXT NOT NULL DEFAULT 'text',
                   message_text TEXT NOT NULL DEFAULT '',
                   media_path TEXT NOT NULL DEFAULT '',
                   caption TEXT NOT NULL DEFAULT '',
                   recurrence_type TEXT NOT NULL DEFAULT 'once',
                   recurrence_value TEXT NOT NULL DEFAULT '',
                   next_run_at TEXT NOT NULL,
                   timezone_name TEXT NOT NULL DEFAULT 'local',
                   delete_after_minutes INTEGER NOT NULL DEFAULT 0,
                   status TEXT NOT NULL DEFAULT 'active',
                   run_count INTEGER NOT NULL DEFAULT 0,
                   last_message_id INTEGER,
                   last_error TEXT NOT NULL DEFAULT '',
                   last_run_at TEXT,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_schedule_jobs_due
               ON schedule_jobs(status, next_run_at, id)"""
        )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_scheduled_once_due
               ON scheduled_once(status, send_at)"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS profile_backups (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   first_name TEXT NOT NULL DEFAULT '',
                   last_name TEXT NOT NULL DEFAULT '',
                   about TEXT NOT NULL DEFAULT '',
                   photo_path TEXT NOT NULL DEFAULT '',
                   reason TEXT NOT NULL DEFAULT '',
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS chatgpt_daily_usage (
                   usage_date TEXT PRIMARY KEY,
                   request_count INTEGER NOT NULL DEFAULT 0,
                   input_tokens INTEGER NOT NULL DEFAULT 0,
                   output_tokens INTEGER NOT NULL DEFAULT 0,
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS runtime_metrics (
                   key TEXT PRIMARY KEY,
                   value TEXT NOT NULL DEFAULT '',
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        connection.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            list(DEFAULT_SELF_SETTINGS.items()),
        )
    restrict_file_permissions(db_path)
    return db_path


def get_self_settings(data_dir: str | Path, phone: str) -> dict[str, str]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT key, value FROM settings"
        ).fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def set_self_setting(
    data_dir: str | Path,
    phone: str,
    key: str,
    value: Any,
) -> None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO settings (key, value)
               VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (str(key), str(value)),
        )


def list_secretary_replies(
    data_dir: str | Path,
    phone: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT id, pattern, response, is_active
               FROM secretary
               ORDER BY id DESC
               LIMIT ?""",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def count_secretary_replies(data_dir: str | Path, phone: str) -> int:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM secretary WHERE is_active = 1"
        ).fetchone()
    return int(row[0] or 0)


def add_secretary_reply(
    data_dir: str | Path,
    phone: str,
    pattern: str,
    response: str,
) -> int:
    normalized_pattern = str(pattern or "").strip().lower()
    normalized_response = str(response or "").strip()
    if not normalized_pattern or not normalized_response:
        raise ValueError("سؤال و پاسخ نباید خالی باشند.")
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM secretary WHERE lower(pattern) = ?",
            (normalized_pattern,),
        )
        cursor = connection.execute(
            """INSERT INTO secretary (pattern, response, is_active)
               VALUES (?, ?, 1)""",
            (normalized_pattern, normalized_response),
        )
        return int(cursor.lastrowid)


def delete_secretary_reply(
    data_dir: str | Path,
    phone: str,
    reply_id: int,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM secretary WHERE id = ?",
            (int(reply_id),),
        )
        return cursor.rowcount > 0


def create_auto_reply_rule(
    data_dir: str | Path,
    phone: str,
    triggers: Iterable[str],
    *,
    scope: str = "private",
    match_mode: str = "contains",
    priority: int = 100,
    cooldown_seconds: int = 30,
) -> int:
    normalized_scope = str(scope or "private").strip().lower()
    normalized_mode = str(match_mode or "contains").strip().lower()
    if normalized_scope not in {"private", "group", "all"}:
        raise ValueError("محدوده پاسخ خودکار معتبر نیست.")
    if normalized_mode not in {"exact", "contains", "starts_with"}:
        raise ValueError("روش تطبیق پاسخ خودکار معتبر نیست.")
    normalized_triggers: list[str] = []
    seen: set[str] = set()
    for item in triggers:
        trigger = str(item or "").strip()
        marker = trigger.casefold()
        if not trigger or marker in seen:
            continue
        if len(trigger) > 100:
            raise ValueError("طول هر عبارت محرک حداکثر ۱۰۰ نویسه است.")
        normalized_triggers.append(trigger)
        seen.add(marker)
    if not 1 <= len(normalized_triggers) <= 50:
        raise ValueError("هر قانون باید بین ۱ تا ۵۰ عبارت محرک داشته باشد.")

    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO auto_reply_rules (
                   scope, match_mode, priority, cooldown_seconds
               ) VALUES (?, ?, ?, ?)""",
            (
                normalized_scope,
                normalized_mode,
                max(0, min(int(priority), 1000)),
                max(0, min(int(cooldown_seconds), 86400)),
            ),
        )
        rule_id = int(cursor.lastrowid)
        connection.executemany(
            """INSERT INTO auto_reply_triggers (rule_id, trigger_text)
               VALUES (?, ?)""",
            [(rule_id, item) for item in normalized_triggers],
        )
    return rule_id


def add_auto_reply_response(
    data_dir: str | Path,
    phone: str,
    rule_id: int,
    *,
    response_type: str,
    content_text: str = "",
    media_path: str = "",
    caption: str = "",
) -> int:
    normalized_type = str(response_type or "text").strip().lower()
    allowed_types = {
        "text",
        "photo",
        "video",
        "voice",
        "sticker",
        "document",
        "animation",
    }
    if normalized_type not in allowed_types:
        raise ValueError("نوع پاسخ خودکار پشتیبانی نمی‌شود.")
    text = str(content_text or "").strip()
    path = str(media_path or "").strip()
    normalized_caption = str(caption or "").strip()
    if normalized_type == "text":
        if not 1 <= len(text) <= 3500:
            raise ValueError("متن پاسخ باید بین ۱ تا ۳۵۰۰ نویسه باشد.")
    elif not path:
        raise ValueError("فایل رسانه پاسخ پیدا نشد.")
    if len(normalized_caption) > 1000:
        raise ValueError("کپشن رسانه حداکثر ۱۰۰۰ نویسه است.")

    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rule = connection.execute(
            "SELECT id FROM auto_reply_rules WHERE id = ?",
            (int(rule_id),),
        ).fetchone()
        if not rule:
            raise ValueError("قانون پاسخ خودکار پیدا نشد.")
        cursor = connection.execute(
            """INSERT INTO auto_reply_responses (
                   rule_id, response_type, content_text, media_path, caption
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                int(rule_id),
                normalized_type,
                text[:3500],
                path,
                normalized_caption[:1000],
            ),
        )
        return int(cursor.lastrowid)


def list_auto_reply_rules(
    data_dir: str | Path,
    phone: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT r.id, r.scope, r.match_mode, r.priority,
                      r.cooldown_seconds, r.is_active,
                      GROUP_CONCAT(DISTINCT t.trigger_text) AS triggers,
                      COUNT(DISTINCT p.id) AS response_count
               FROM auto_reply_rules r
               LEFT JOIN auto_reply_triggers t ON t.rule_id = r.id
               LEFT JOIN auto_reply_responses p ON p.rule_id = r.id
               GROUP BY r.id
               ORDER BY r.priority DESC, r.id DESC
               LIMIT ?""",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_auto_reply_rule(
    data_dir: str | Path,
    phone: str,
    rule_id: int,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM auto_reply_responses WHERE rule_id = ?",
            (int(rule_id),),
        )
        connection.execute(
            "DELETE FROM auto_reply_triggers WHERE rule_id = ?",
            (int(rule_id),),
        )
        cursor = connection.execute(
            "DELETE FROM auto_reply_rules WHERE id = ?",
            (int(rule_id),),
        )
        return cursor.rowcount > 0


def find_auto_reply_candidates(
    data_dir: str | Path,
    phone: str,
    *,
    message_text: str,
    scope: str,
) -> list[dict[str, Any]]:
    normalized_text = str(message_text or "").strip().casefold()
    normalized_scope = str(scope or "private").strip().lower()
    if not normalized_text:
        return []
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rules = connection.execute(
            """SELECT id, match_mode, priority, cooldown_seconds
               FROM auto_reply_rules
               WHERE is_active = 1 AND scope IN (?, 'all')
               ORDER BY priority DESC, id ASC""",
            (normalized_scope,),
        ).fetchall()
        matched: list[dict[str, Any]] = []
        highest_priority: int | None = None
        for rule in rules:
            priority = int(rule["priority"] or 0)
            if highest_priority is not None and priority < highest_priority:
                break
            triggers = connection.execute(
                """SELECT trigger_text FROM auto_reply_triggers
                   WHERE rule_id = ? ORDER BY id""",
                (int(rule["id"]),),
            ).fetchall()
            mode = str(rule["match_mode"])
            is_match = False
            for trigger_row in triggers:
                trigger = str(trigger_row["trigger_text"] or "").casefold()
                if mode == "exact":
                    is_match = normalized_text == trigger
                elif mode == "starts_with":
                    is_match = normalized_text.startswith(trigger)
                else:
                    is_match = trigger in normalized_text
                if is_match:
                    break
            if not is_match:
                continue
            if highest_priority is None:
                highest_priority = priority
            responses = connection.execute(
                """SELECT id, rule_id, response_type, content_text,
                          media_path, caption
                   FROM auto_reply_responses
                   WHERE rule_id = ? ORDER BY id""",
                (int(rule["id"]),),
            ).fetchall()
            for response in responses:
                item = dict(response)
                item["cooldown_seconds"] = int(
                    rule["cooldown_seconds"] or 0
                )
                matched.append(item)
    return matched


def create_form_template(
    data_dir: str | Path,
    phone: str,
    name: str,
    trigger_text: str,
    questions: list[str],
) -> int:
    normalized_name = str(name or "").strip()
    normalized_trigger = str(trigger_text or "").strip().lower()
    normalized_questions = [
        str(question or "").strip()
        for question in questions
        if str(question or "").strip()
    ]
    if not 1 <= len(normalized_name) <= 100:
        raise ValueError("نام فرم باید بین ۱ تا ۱۰۰ نویسه باشد.")
    if not 1 <= len(normalized_trigger) <= 100:
        raise ValueError("کلمه شروع فرم باید بین ۱ تا ۱۰۰ نویسه باشد.")
    if not 1 <= len(normalized_questions) <= 8:
        raise ValueError("هر فرم باید بین ۱ تا ۸ سؤال داشته باشد.")
    if any(len(question) > 200 for question in normalized_questions):
        raise ValueError("طول هر سؤال فرم حداکثر ۲۰۰ نویسه است.")

    db_path = ensure_self_settings(data_dir, phone)
    try:
        with connect(db_path) as connection:
            cursor = connection.execute(
                """INSERT INTO form_templates (name, trigger_text, is_active)
                   VALUES (?, ?, 1)""",
                (normalized_name, normalized_trigger),
            )
            form_id = int(cursor.lastrowid)
            connection.executemany(
                """INSERT INTO form_fields (
                       form_id, question, position, required
                   ) VALUES (?, ?, ?, 1)""",
                [
                    (form_id, question, position)
                    for position, question in enumerate(
                        normalized_questions,
                        start=1,
                    )
                ],
            )
            return form_id
    except sqlite3.IntegrityError as exc:
        raise ValueError("این کلمه شروع قبلاً برای فرم دیگری ثبت شده است.") from exc


def list_form_templates(
    data_dir: str | Path,
    phone: str,
    *,
    active_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    where = "WHERE template.is_active = 1" if active_only else ""
    with connect(db_path) as connection:
        rows = connection.execute(
            f"""SELECT template.id, template.name, template.trigger_text,
                       template.is_active, template.created_at,
                       COUNT(field.id) AS field_count
                FROM form_templates AS template
                LEFT JOIN form_fields AS field
                  ON field.form_id = template.id
                {where}
                GROUP BY template.id
                ORDER BY template.id DESC
                LIMIT ?""",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_form_template(
    data_dir: str | Path,
    phone: str,
    form_id: int,
    *,
    require_active: bool = False,
) -> dict[str, Any] | None:
    db_path = ensure_self_settings(data_dir, phone)
    active_clause = " AND is_active = 1" if require_active else ""
    with connect(db_path) as connection:
        template = connection.execute(
            f"""SELECT id, name, trigger_text, is_active, created_at
                FROM form_templates
                WHERE id = ?{active_clause}""",
            (int(form_id),),
        ).fetchone()
        if not template:
            return None
        fields = connection.execute(
            """SELECT id, question, position, required
               FROM form_fields
               WHERE form_id = ?
               ORDER BY position, id""",
            (int(form_id),),
        ).fetchall()
    result = dict(template)
    result["fields"] = [dict(row) for row in fields]
    return result


def find_form_template(
    data_dir: str | Path,
    phone: str,
    text: str,
) -> dict[str, Any] | None:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return None
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        row = connection.execute(
            """SELECT id FROM form_templates
               WHERE is_active = 1
                 AND (lower(trigger_text) = ? OR lower(name) = ?)
               ORDER BY id DESC
               LIMIT 1""",
            (normalized, normalized),
        ).fetchone()
    if not row:
        return None
    return get_form_template(
        data_dir,
        phone,
        int(row["id"]),
        require_active=True,
    )


def set_form_template_active(
    data_dir: str | Path,
    phone: str,
    form_id: int,
    *,
    enabled: bool,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            "UPDATE form_templates SET is_active = ? WHERE id = ?",
            (1 if enabled else 0, int(form_id)),
        )
    return cursor.rowcount > 0


def delete_form_template(
    data_dir: str | Path,
    phone: str,
    form_id: int,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM form_sessions WHERE form_id = ?",
            (int(form_id),),
        )
        connection.execute(
            "DELETE FROM form_fields WHERE form_id = ?",
            (int(form_id),),
        )
        cursor = connection.execute(
            "DELETE FROM form_templates WHERE id = ?",
            (int(form_id),),
        )
    return cursor.rowcount > 0


def save_form_session(
    data_dir: str | Path,
    phone: str,
    *,
    user_id: int,
    chat_id: int,
    form_id: int,
    current_index: int = 0,
    stage: str = "answering",
    answers: list[str] | None = None,
) -> None:
    if stage not in {"answering", "confirming"}:
        raise ValueError("مرحله فرم معتبر نیست.")
    db_path = ensure_self_settings(data_dir, phone)
    answers_json = json.dumps(
        [str(answer) for answer in (answers or [])],
        ensure_ascii=False,
    )
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO form_sessions (
                   user_id, chat_id, form_id, current_index, stage,
                   answers_json, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                   chat_id = excluded.chat_id,
                   form_id = excluded.form_id,
                   current_index = excluded.current_index,
                   stage = excluded.stage,
                   answers_json = excluded.answers_json,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                int(user_id),
                int(chat_id),
                int(form_id),
                max(0, int(current_index)),
                stage,
                answers_json,
            ),
        )


def get_form_session(
    data_dir: str | Path,
    phone: str,
    user_id: int,
) -> dict[str, Any] | None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        row = connection.execute(
            """SELECT user_id, chat_id, form_id, current_index, stage,
                      answers_json, updated_at
               FROM form_sessions WHERE user_id = ?""",
            (int(user_id),),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["answers"] = json.loads(result.pop("answers_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        result["answers"] = []
    return result


def clear_form_session(
    data_dir: str | Path,
    phone: str,
    user_id: int,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM form_sessions WHERE user_id = ?",
            (int(user_id),),
        )
    return cursor.rowcount > 0


def create_form_submission(
    data_dir: str | Path,
    phone: str,
    *,
    form_id: int,
    form_name: str,
    user_id: int,
    chat_id: int,
    summary_text: str,
    answers: list[str],
) -> int:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO form_submissions (
                   form_id, form_name, user_id, chat_id, summary_text,
                   answers_json, status
               ) VALUES (?, ?, ?, ?, ?, ?, 'processing')""",
            (
                int(form_id),
                str(form_name or "").strip(),
                int(user_id),
                int(chat_id),
                str(summary_text or "").strip(),
                json.dumps(
                    [str(answer) for answer in answers],
                    ensure_ascii=False,
                ),
            ),
        )
        return int(cursor.lastrowid)


def attach_form_submission_messages(
    data_dir: str | Path,
    phone: str,
    submission_id: int,
    *,
    customer_message_id: int | None = None,
    admin_message_id: int | None = None,
) -> None:
    db_path = ensure_self_settings(data_dir, phone)
    updates = []
    values: list[Any] = []
    if customer_message_id is not None:
        updates.append("customer_message_id = ?")
        values.append(int(customer_message_id))
    if admin_message_id is not None:
        updates.append("admin_message_id = ?")
        values.append(int(admin_message_id))
    if not updates:
        return
    updates.append("updated_at = CURRENT_TIMESTAMP")
    values.append(int(submission_id))
    with connect(db_path) as connection:
        connection.execute(
            f"""UPDATE form_submissions
                SET {", ".join(updates)}
                WHERE id = ?""",
            values,
        )


def get_form_submission_for_message(
    data_dir: str | Path,
    phone: str,
    *,
    message_id: int,
    chat_id: int | None = None,
) -> dict[str, Any] | None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        if chat_id is None:
            row = connection.execute(
                """SELECT * FROM form_submissions
                   WHERE admin_message_id = ?
                   ORDER BY id DESC LIMIT 1""",
                (int(message_id),),
            ).fetchone()
        else:
            row = connection.execute(
                """SELECT * FROM form_submissions
                   WHERE (
                       admin_message_id = ?
                       OR (chat_id = ? AND customer_message_id = ?)
                   )
                   ORDER BY id DESC LIMIT 1""",
                (int(message_id), int(chat_id), int(message_id)),
            ).fetchone()
    return dict(row) if row else None


def update_form_submission_status(
    data_dir: str | Path,
    phone: str,
    submission_id: int,
    status: str,
) -> dict[str, Any] | None:
    allowed = {"processing", "ready", "shipped", "completed", "cancelled"}
    normalized = str(status or "").strip().lower()
    if normalized not in allowed:
        raise ValueError("وضعیت فرم معتبر نیست.")
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """UPDATE form_submissions
               SET status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (normalized, int(submission_id)),
        )
        row = connection.execute(
            "SELECT * FROM form_submissions WHERE id = ?",
            (int(submission_id),),
        ).fetchone()
    return dict(row) if row else None


def list_friends(
    data_dir: str | Path,
    phone: str,
    *,
    limit: int = 50,
) -> list[int]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT user_id FROM friends
               ORDER BY added_at DESC LIMIT ?""",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [int(row["user_id"]) for row in rows]


def set_friend(
    data_dir: str | Path,
    phone: str,
    user_id: int,
    *,
    enabled: bool,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        if enabled:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO friends (user_id) VALUES (?)""",
                (int(user_id),),
            )
        else:
            cursor = connection.execute(
                "DELETE FROM friends WHERE user_id = ?",
                (int(user_id),),
            )
    return cursor.rowcount > 0


def list_friend_affection_replies(
    data_dir: str | Path,
    phone: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT id, response, created_at
               FROM friend_affection_replies
               ORDER BY id DESC
               LIMIT ?""",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def add_friend_affection_reply(
    data_dir: str | Path,
    phone: str,
    response: str,
) -> int:
    normalized = str(response or "").strip()
    if not 1 <= len(normalized) <= 500:
        raise ValueError("متن دوست باید بین ۱ تا ۵۰۰ نویسه باشد.")
    db_path = ensure_self_settings(data_dir, phone)
    try:
        with connect(db_path) as connection:
            cursor = connection.execute(
                """INSERT INTO friend_affection_replies (response)
                   VALUES (?)""",
                (normalized,),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise ValueError("این متن قبلاً ثبت شده است.") from exc


def add_friend_affection_replies(
    data_dir: str | Path,
    phone: str,
    responses: Iterable[str],
) -> tuple[int, int]:
    """Add up to 50 friend replies in one atomic operation.

    Returns ``(inserted, skipped_duplicates)``.  Validation happens before the
    transaction so a malformed line never leaves a partially imported list.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for response in responses:
        text = str(response or "").strip()
        if not text:
            continue
        if not 1 <= len(text) <= 500:
            raise ValueError("هر متن دوست باید بین ۱ تا ۵۰۰ نویسه باشد.")
        marker = text.casefold()
        if marker not in seen:
            normalized.append(text)
            seen.add(marker)
    if not normalized:
        raise ValueError("حداقل یک متن دوست بفرستید.")
    if len(normalized) > 50:
        raise ValueError("در هر بار حداکثر ۵۰ متن دوست قابل ثبت است.")

    db_path = ensure_self_settings(data_dir, phone)
    inserted = 0
    with connect(db_path) as connection:
        for text in normalized:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO friend_affection_replies (response)
                   VALUES (?)""",
                (text,),
            )
            inserted += int(cursor.rowcount > 0)
    return inserted, len(normalized) - inserted


def delete_friend_affection_reply(
    data_dir: str | Path,
    phone: str,
    reply_id: int,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM friend_affection_replies WHERE id = ?",
            (int(reply_id),),
        )
    return cursor.rowcount > 0


def list_enemies(
    data_dir: str | Path,
    phone: str,
    *,
    limit: int = 50,
) -> list[int]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        try:
            rows = connection.execute(
                "SELECT user_id FROM enemy ORDER BY user_id LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    return [int(row["user_id"]) for row in rows]


def set_enemy(
    data_dir: str | Path,
    phone: str,
    user_id: int,
    *,
    enabled: bool,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS enemy (
                   user_id INTEGER PRIMARY KEY
               )"""
        )
        if enabled:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO enemy (user_id) VALUES (?)",
                (int(user_id),),
            )
        else:
            cursor = connection.execute(
                "DELETE FROM enemy WHERE user_id = ?",
                (int(user_id),),
            )
    return cursor.rowcount > 0


def list_enemy_hostile_replies(
    data_dir: str | Path,
    phone: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT id, response, created_at
               FROM enemy_hostile_replies
               ORDER BY id DESC
               LIMIT ?""",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def add_enemy_hostile_reply(
    data_dir: str | Path,
    phone: str,
    response: str,
) -> int:
    normalized = str(response or "").strip()
    if not 1 <= len(normalized) <= 500:
        raise ValueError("متن دشمن باید بین ۱ تا ۵۰۰ نویسه باشد.")
    db_path = ensure_self_settings(data_dir, phone)
    try:
        with connect(db_path) as connection:
            cursor = connection.execute(
                """INSERT INTO enemy_hostile_replies (response)
                   VALUES (?)""",
                (normalized,),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise ValueError("این متن قبلاً ثبت شده است.") from exc


def add_enemy_hostile_replies(
    data_dir: str | Path,
    phone: str,
    responses: Iterable[str],
) -> tuple[int, int]:
    """Add up to 50 enemy replies in one atomic operation."""
    normalized: list[str] = []
    seen: set[str] = set()
    for response in responses:
        text = str(response or "").strip()
        if not text:
            continue
        if not 1 <= len(text) <= 500:
            raise ValueError("هر متن دشمن باید بین ۱ تا ۵۰۰ نویسه باشد.")
        marker = text.casefold()
        if marker not in seen:
            normalized.append(text)
            seen.add(marker)
    if not normalized:
        raise ValueError("حداقل یک متن دشمن بفرستید.")
    if len(normalized) > 50:
        raise ValueError("در هر بار حداکثر ۵۰ متن دشمن قابل ثبت است.")

    db_path = ensure_self_settings(data_dir, phone)
    inserted = 0
    with connect(db_path) as connection:
        for text in normalized:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO enemy_hostile_replies (response)
                   VALUES (?)""",
                (text,),
            )
            inserted += int(cursor.rowcount > 0)
    return inserted, len(normalized) - inserted


def delete_enemy_hostile_reply(
    data_dir: str | Path,
    phone: str,
    reply_id: int,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM enemy_hostile_replies WHERE id = ?",
            (int(reply_id),),
        )
    return cursor.rowcount > 0


def list_word_filters(
    data_dir: str | Path,
    phone: str,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT id, phrase, action, is_active, created_at
               FROM word_filters
               ORDER BY id DESC LIMIT ?""",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    return [dict(row) for row in rows]


def add_word_filter(
    data_dir: str | Path,
    phone: str,
    phrase: str,
    action: str = "delete",
) -> int:
    normalized_phrase = str(phrase or "").strip().lower()
    normalized_action = str(action or "").strip().lower()
    allowed_actions = {"delete", "warn", "mute", "block"}
    if not 1 <= len(normalized_phrase) <= 200:
        raise ValueError("طول عبارت فیلتر باید بین ۱ تا ۲۰۰ نویسه باشد.")
    if normalized_action not in allowed_actions:
        raise ValueError("عملیات فیلتر باید delete، warn، mute یا block باشد.")
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM word_filters WHERE lower(phrase) = ?",
            (normalized_phrase,),
        )
        cursor = connection.execute(
            """INSERT INTO word_filters (phrase, action, is_active)
               VALUES (?, ?, 1)""",
            (normalized_phrase, normalized_action),
        )
        return int(cursor.lastrowid)


def delete_word_filter(
    data_dir: str | Path,
    phone: str,
    filter_id: int,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM word_filters WHERE id = ?",
            (int(filter_id),),
        )
    return cursor.rowcount > 0


def list_tracked_profiles(
    data_dir: str | Path,
    phone: str,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT user_id, label, is_active, last_checked_at, created_at
               FROM tracked_profiles
               ORDER BY created_at DESC LIMIT ?""",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_tracked_profile(
    data_dir: str | Path,
    phone: str,
    user_id: int,
    *,
    label: str = "",
) -> None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO tracked_profiles (user_id, label, is_active)
               VALUES (?, ?, 1)
               ON CONFLICT(user_id) DO UPDATE SET
                   label = excluded.label,
                   is_active = 1""",
            (int(user_id), str(label or "").strip()[:100]),
        )


def delete_tracked_profile(
    data_dir: str | Path,
    phone: str,
    user_id: int,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM tracked_profiles WHERE user_id = ?",
            (int(user_id),),
        )
    return cursor.rowcount > 0


def list_first_comment_channels(
    data_dir: str | Path,
    phone: str,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT chat_id, comment_text, delay_seconds, is_active, created_at
               FROM first_comment_channels
               ORDER BY created_at DESC LIMIT ?""",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_first_comment_channel(
    data_dir: str | Path,
    phone: str,
    chat_id: str,
    comment_text: str,
    *,
    delay_seconds: int = 2,
) -> None:
    normalized_chat = str(chat_id or "").strip()
    normalized_text = str(comment_text or "").strip()
    if not normalized_chat:
        raise ValueError("شناسه کانال نباید خالی باشد.")
    if not 1 <= len(normalized_text) <= 1000:
        raise ValueError("متن کامنت باید بین ۱ تا ۱۰۰۰ نویسه باشد.")
    bounded_delay = max(0, min(int(delay_seconds), 300))
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO first_comment_channels
                   (chat_id, comment_text, delay_seconds, is_active)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(chat_id) DO UPDATE SET
                   comment_text = excluded.comment_text,
                   delay_seconds = excluded.delay_seconds,
                   is_active = 1""",
            (normalized_chat, normalized_text, bounded_delay),
        )


def delete_first_comment_channel(
    data_dir: str | Path,
    phone: str,
    chat_id: str,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM first_comment_channels WHERE chat_id = ?",
            (str(chat_id or "").strip(),),
        )
    return cursor.rowcount > 0


def get_feature_counts(
    data_dir: str | Path,
    phone: str,
) -> dict[str, int]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        queries = {
            "friends": "SELECT COUNT(*) FROM friends",
            "friend_replies": (
                "SELECT COUNT(*) FROM friend_affection_replies"
            ),
            "enemy_replies": (
                "SELECT COUNT(*) FROM enemy_hostile_replies"
            ),
            "filters": (
                "SELECT COUNT(*) FROM word_filters WHERE is_active = 1"
            ),
            "profiles": (
                "SELECT COUNT(*) FROM tracked_profiles WHERE is_active = 1"
            ),
            "comments": (
                "SELECT COUNT(*) FROM first_comment_channels "
                "WHERE is_active = 1"
            ),
            "voices": "SELECT COUNT(*) FROM voice_library",
            "archive": "SELECT COUNT(*) FROM message_archive",
            "forms": (
                "SELECT COUNT(*) FROM form_templates WHERE is_active = 1"
            ),
            "form_submissions": "SELECT COUNT(*) FROM form_submissions",
            "private_allowlist": "SELECT COUNT(*) FROM private_allowlist",
            "message_edits": "SELECT COUNT(*) FROM message_edits",
            "scheduled_once": (
                "SELECT COUNT(*) FROM scheduled_once WHERE status = 'pending'"
            ),
            "profile_backups": "SELECT COUNT(*) FROM profile_backups",
        }
        counts = {
            key: int(connection.execute(sql).fetchone()[0] or 0)
            for key, sql in queries.items()
        }
        try:
            counts["enemies"] = int(
                connection.execute("SELECT COUNT(*) FROM enemy").fetchone()[0]
                or 0
            )
        except sqlite3.OperationalError:
            counts["enemies"] = 0
    return counts


def anti_delete_directory(data_dir: str | Path, phone: str) -> Path:
    safe_phone = str(phone).replace("+", "").strip()
    if not safe_phone or any(char in safe_phone for char in ("/", "\\", "\0")):
        raise ValueError("شماره حساب برای ساخت مسیر ضدحذف معتبر نیست.")
    # Cloud-only media mode: return the legacy path for migration checks,
    # but never create an archive directory on the host.
    return Path(data_dir) / f"anti_delete_{safe_phone}"


def archive_message(
    data_dir: str | Path,
    phone: str,
    *,
    chat_id: int,
    message_id: int,
    sender_id: int = 0,
    sender_name: str = "",
    chat_title: str = "",
    message_text: str = "",
    media_type: str = "text",
    media_path: str = "",
    media_name: str = "",
    media_size: int = 0,
) -> None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO message_archive (
                   chat_id, message_id, sender_id, sender_name, chat_title,
                   message_text, media_type, media_path, media_name, media_size
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, message_id) DO UPDATE SET
                   sender_id = excluded.sender_id,
                   sender_name = excluded.sender_name,
                   chat_title = excluded.chat_title,
                   message_text = excluded.message_text,
                   media_type = excluded.media_type,
                   media_path = excluded.media_path,
                   media_name = excluded.media_name,
                   media_size = excluded.media_size""",
            (
                int(chat_id),
                int(message_id),
                int(sender_id or 0),
                str(sender_name or "")[:300],
                str(chat_title or "")[:300],
                str(message_text or "")[:8000],
                str(media_type or "text")[:50],
                str(media_path or ""),
                str(media_name or "")[:500],
                max(0, int(media_size or 0)),
            ),
        )


def get_archived_messages(
    data_dir: str | Path,
    phone: str,
    *,
    message_id: int,
    chat_id: int | None = None,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        if chat_id is None:
            rows = connection.execute(
                """SELECT * FROM message_archive
                   WHERE message_id = ?
                   ORDER BY created_at DESC""",
                (int(message_id),),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT * FROM message_archive
                   WHERE chat_id = ? AND message_id = ?
                   ORDER BY created_at DESC""",
                (int(chat_id), int(message_id)),
            ).fetchall()
    return [dict(row) for row in rows]


def count_archived_messages(data_dir: str | Path, phone: str) -> int:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM message_archive"
        ).fetchone()
    return int(row[0] or 0)


def _remove_archive_file(
    data_dir: str | Path,
    phone: str,
    media_path: str,
) -> None:
    if not media_path:
        return
    archive_root = anti_delete_directory(data_dir, phone).resolve()
    candidate = Path(media_path)
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(archive_root):
            return
        resolved.unlink(missing_ok=True)
    except (OSError, RuntimeError):
        pass


def remove_archived_message(
    data_dir: str | Path,
    phone: str,
    *,
    chat_id: int,
    message_id: int,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        row = connection.execute(
            """SELECT media_path FROM message_archive
               WHERE chat_id = ? AND message_id = ?""",
            (int(chat_id), int(message_id)),
        ).fetchone()
        cursor = connection.execute(
            """DELETE FROM message_archive
               WHERE chat_id = ? AND message_id = ?""",
            (int(chat_id), int(message_id)),
        )
    if row:
        _remove_archive_file(data_dir, phone, str(row["media_path"] or ""))
    return cursor.rowcount > 0


def clear_message_archive(data_dir: str | Path, phone: str) -> int:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT media_path FROM message_archive"
        ).fetchall()
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM message_archive"
            ).fetchone()[0]
            or 0
        )
        connection.execute("DELETE FROM message_archive")
    for row in rows:
        _remove_archive_file(data_dir, phone, str(row["media_path"] or ""))
    archive_root = anti_delete_directory(data_dir, phone)
    try:
        for child in archive_root.iterdir():
            if child.is_file():
                child.unlink(missing_ok=True)
    except OSError:
        pass
    return count


def purge_expired_archives(
    data_dir: str | Path,
    phone: str,
    *,
    retention_days: int,
) -> int:
    bounded_days = max(1, min(int(retention_days), 30))
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT chat_id, message_id, media_path
               FROM message_archive
               WHERE created_at < datetime('now', ?)""",
            (f"-{bounded_days} days",),
        ).fetchall()
        connection.execute(
            """DELETE FROM message_archive
               WHERE created_at < datetime('now', ?)""",
            (f"-{bounded_days} days",),
        )
    for row in rows:
        _remove_archive_file(data_dir, phone, str(row["media_path"] or ""))
    return len(rows)


def list_private_allowlist(
    data_dir: str | Path,
    phone: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            """SELECT user_id, label, added_at
               FROM private_allowlist
               ORDER BY added_at DESC
               LIMIT ?""",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
    return [dict(row) for row in rows]


def private_user_is_allowed(
    data_dir: str | Path,
    phone: str,
    user_id: int,
) -> bool:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM private_allowlist WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
    return row is not None


def set_private_allowlist_user(
    data_dir: str | Path,
    phone: str,
    user_id: int,
    *,
    allowed: bool,
    label: str = "",
) -> None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        if allowed:
            connection.execute(
                """INSERT INTO private_allowlist (user_id, label)
                   VALUES (?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       label = excluded.label,
                       added_at = CURRENT_TIMESTAMP""",
                (int(user_id), str(label or "").strip()[:120]),
            )
            connection.execute(
                "DELETE FROM private_lock_attempts WHERE user_id = ?",
                (int(user_id),),
            )
        else:
            connection.execute(
                "DELETE FROM private_allowlist WHERE user_id = ?",
                (int(user_id),),
            )


def register_private_lock_attempt(
    data_dir: str | Path,
    phone: str,
    user_id: int,
) -> int:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO private_lock_attempts (
                   user_id, warning_count, last_warning_at
               ) VALUES (?, 1, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                   warning_count = private_lock_attempts.warning_count + 1,
                   last_warning_at = CURRENT_TIMESTAMP""",
            (int(user_id),),
        )
        row = connection.execute(
            """SELECT warning_count
               FROM private_lock_attempts
               WHERE user_id = ?""",
            (int(user_id),),
        ).fetchone()
    return int(row["warning_count"] or 0)


def mark_private_user_blocked(
    data_dir: str | Path,
    phone: str,
    user_id: int,
) -> None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO private_lock_attempts (
                   user_id, warning_count, blocked_at
               ) VALUES (?, 0, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                   blocked_at = CURRENT_TIMESTAMP""",
            (int(user_id),),
        )


def remember_message_version(
    data_dir: str | Path,
    phone: str,
    *,
    chat_id: int,
    message_id: int,
    sender_id: int = 0,
    sender_name: str = "",
    chat_title: str = "",
    message_text: str = "",
) -> None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO message_versions (
                   chat_id, message_id, sender_id, sender_name,
                   chat_title, message_text
               ) VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(chat_id, message_id) DO UPDATE SET
                   sender_id = excluded.sender_id,
                   sender_name = excluded.sender_name,
                   chat_title = excluded.chat_title,
                   message_text = excluded.message_text,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                int(chat_id),
                int(message_id),
                int(sender_id or 0),
                str(sender_name or "")[:200],
                str(chat_title or "")[:200],
                str(message_text or "")[:4000],
            ),
        )


def get_message_version(
    data_dir: str | Path,
    phone: str,
    *,
    chat_id: int,
    message_id: int,
) -> dict[str, Any] | None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        row = connection.execute(
            """SELECT chat_id, message_id, sender_id, sender_name,
                      chat_title, message_text, updated_at
               FROM message_versions
               WHERE chat_id = ? AND message_id = ?""",
            (int(chat_id), int(message_id)),
        ).fetchone()
    return dict(row) if row else None


def record_message_edit(
    data_dir: str | Path,
    phone: str,
    *,
    chat_id: int,
    message_id: int,
    sender_id: int = 0,
    sender_name: str = "",
    chat_title: str = "",
    before_text: str = "",
    after_text: str = "",
    scope: str = "private",
) -> int:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO message_edits (
                   chat_id, message_id, sender_id, sender_name,
                   chat_title, before_text, after_text, scope
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(chat_id),
                int(message_id),
                int(sender_id or 0),
                str(sender_name or "")[:200],
                str(chat_title or "")[:200],
                str(before_text or "")[:4000],
                str(after_text or "")[:4000],
                str(scope or "private")[:20],
            ),
        )
        return int(cursor.lastrowid)


def create_scheduled_once(
    data_dir: str | Path,
    phone: str,
    *,
    target: str,
    message_text: str,
    send_at: str,
    reply_to_message_id: int | None = None,
) -> int:
    normalized_target = str(target or "").strip()
    normalized_text = str(message_text or "").strip()
    if not normalized_target or not normalized_text or not str(send_at).strip():
        raise ValueError("مقصد، متن و زمان ارسال الزامی است.")
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO scheduled_once (
                   target, message_text, send_at, reply_to_message_id
               ) VALUES (?, ?, ?, ?)""",
            (
                normalized_target[:200],
                normalized_text[:3500],
                str(send_at).strip(),
                int(reply_to_message_id) if reply_to_message_id else None,
            ),
        )
        return int(cursor.lastrowid)


def list_scheduled_once(
    data_dir: str | Path,
    phone: str,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    query = (
        """SELECT id, target, message_text, send_at, reply_to_message_id,
                  status, error_text, created_at, sent_at
           FROM scheduled_once"""
    )
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(str(status))
    query += " ORDER BY send_at ASC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with connect(db_path) as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def list_due_scheduled_once(
    data_dir: str | Path,
    phone: str,
    *,
    now_iso: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Atomically claim due rows; stale sending rows are never auto-replayed."""
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE scheduled_once
               SET status = 'uncertain',
                   error_text = 'وضعیت ارسال پس از توقف ناگهانی نامشخص است'
               WHERE status = 'sending'
                 AND send_at <= datetime('now', '-10 minutes')"""
        )
        rows = connection.execute(
            """SELECT id, target, message_text, send_at, reply_to_message_id
               FROM scheduled_once
               WHERE status = 'pending' AND send_at <= ?
               ORDER BY send_at ASC LIMIT ?""",
            (str(now_iso), max(1, min(int(limit), 100))),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            connection.execute(
                f"UPDATE scheduled_once SET status = 'sending' "
                f"WHERE id IN ({placeholders}) AND status = 'pending'",
                ids,
            )
    return [dict(row) for row in rows]


def update_scheduled_once_status(
    data_dir: str | Path,
    phone: str,
    schedule_id: int,
    *,
    status: str,
    error_text: str = "",
) -> None:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {
        "pending", "sending", "sent", "failed", "cancelled", "uncertain"
    }:
        raise ValueError("وضعیت ارسال یک‌باره معتبر نیست.")
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """UPDATE scheduled_once
               SET status = ?, error_text = ?,
                   sent_at = CASE
                       WHEN ? = 'sent' THEN CURRENT_TIMESTAMP
                       ELSE sent_at
                   END
               WHERE id = ?""",
            (
                normalized_status,
                str(error_text or "")[:500],
                normalized_status,
                int(schedule_id),
            ),
        )


def create_schedule_job(
    data_dir: str | Path,
    phone: str,
    *,
    target: str,
    message_type: str,
    message_text: str = "",
    media_path: str = "",
    caption: str = "",
    recurrence_type: str,
    recurrence_value: str,
    next_run_at: str,
    timezone_name: str = "local",
    delete_after_minutes: int = 0,
) -> int:
    normalized_target = str(target or "").strip()
    normalized_type = str(message_type or "text").strip().lower()
    normalized_recurrence = str(recurrence_type or "once").strip().lower()
    if not normalized_target:
        raise ValueError("مقصد برنامه الزامی است.")
    if normalized_type not in {
        "text",
        "photo",
        "video",
        "voice",
        "sticker",
        "document",
        "animation",
    }:
        raise ValueError("نوع پیام برنامه پشتیبانی نمی‌شود.")
    if normalized_type == "text" and not str(message_text or "").strip():
        raise ValueError("متن برنامه نباید خالی باشد.")
    if normalized_type != "text" and not str(media_path or "").strip():
        raise ValueError("فایل رسانه برنامه پیدا نشد.")
    if normalized_recurrence not in {"once", "interval", "daily", "weekly"}:
        raise ValueError("نوع تکرار برنامه معتبر نیست.")
    if not str(next_run_at or "").strip():
        raise ValueError("زمان اجرای بعدی برنامه الزامی است.")

    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO schedule_jobs (
                   target, message_type, message_text, media_path, caption,
                   recurrence_type, recurrence_value, next_run_at,
                   timezone_name, delete_after_minutes
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                normalized_target[:200],
                normalized_type,
                str(message_text or "")[:3500],
                str(media_path or ""),
                str(caption or "")[:1000],
                normalized_recurrence,
                str(recurrence_value or "")[:200],
                str(next_run_at).strip(),
                str(timezone_name or "local")[:100],
                max(0, min(int(delete_after_minutes), 10080)),
            ),
        )
        return int(cursor.lastrowid)


def list_schedule_jobs(
    data_dir: str | Path,
    phone: str,
    *,
    statuses: Iterable[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    query = (
        """SELECT id, target, message_type, message_text, media_path, caption,
                  recurrence_type, recurrence_value, next_run_at,
                  timezone_name, delete_after_minutes, status, run_count,
                  last_message_id, last_error, last_run_at, created_at
           FROM schedule_jobs"""
    )
    params: list[Any] = []
    normalized_statuses = [
        str(item).strip().lower()
        for item in (statuses or [])
        if str(item).strip()
    ]
    if normalized_statuses:
        placeholders = ",".join("?" for _ in normalized_statuses)
        query += f" WHERE status IN ({placeholders})"
        params.extend(normalized_statuses)
    query += " ORDER BY next_run_at ASC, id ASC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with connect(db_path) as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def claim_due_schedule_jobs(
    data_dir: str | Path,
    phone: str,
    *,
    now_iso: str,
    stale_before_iso: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE schedule_jobs
               SET status = 'uncertain', updated_at = CURRENT_TIMESTAMP,
                   last_error = 'وضعیت ارسال پس از توقف ناگهانی نامشخص است'
               WHERE status = 'running'
                 AND COALESCE(last_run_at, created_at) <= ?""",
            (str(stale_before_iso),),
        )
        rows = connection.execute(
            """SELECT id, target, message_type, message_text, media_path,
                      caption, recurrence_type, recurrence_value,
                      next_run_at, timezone_name, delete_after_minutes
               FROM schedule_jobs
               WHERE status = 'active' AND next_run_at <= ?
               ORDER BY next_run_at ASC, id ASC
               LIMIT ?""",
            (str(now_iso), max(1, min(int(limit), 50))),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            connection.execute(
                f"""UPDATE schedule_jobs
                    SET status = 'running', last_run_at = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})""",
                [str(now_iso), *ids],
            )
    return [dict(row) for row in rows]


def finish_schedule_job_run(
    data_dir: str | Path,
    phone: str,
    job_id: int,
    *,
    next_run_at: str | None,
    message_id: int | None = None,
    error_text: str = "",
) -> None:
    db_path = ensure_self_settings(data_dir, phone)
    success = not str(error_text or "").strip()
    if success and next_run_at:
        status = "active"
    elif success:
        status = "completed"
    else:
        # Never automatically replay an operation whose Telegram outcome may
        # be ambiguous.  The owner can explicitly resume it from the panel.
        status = "uncertain"
    with connect(db_path) as connection:
        connection.execute(
            """UPDATE schedule_jobs
               SET status = ?, next_run_at = COALESCE(?, next_run_at),
                   run_count = run_count + ?,
                   last_message_id = COALESCE(?, last_message_id),
                   last_error = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status = 'running'""",
            (
                status,
                str(next_run_at) if next_run_at else None,
                1 if success else 0,
                int(message_id) if message_id else None,
                str(error_text or "")[:500],
                int(job_id),
            ),
        )


def set_schedule_job_status(
    data_dir: str | Path,
    phone: str,
    job_id: int,
    status: str,
) -> bool:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"active", "paused", "cancelled"}:
        raise ValueError("وضعیت برنامه معتبر نیست.")
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            """UPDATE schedule_jobs
               SET status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status NOT IN ('completed', 'cancelled')""",
            (normalized_status, int(job_id)),
        )
        return cursor.rowcount > 0


def create_profile_backup(
    data_dir: str | Path,
    phone: str,
    *,
    first_name: str,
    last_name: str = "",
    about: str = "",
    photo_path: str = "",
    reason: str = "",
) -> int:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        cursor = connection.execute(
            """INSERT INTO profile_backups (
                   first_name, last_name, about, photo_path, reason
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                str(first_name or "")[:64],
                str(last_name or "")[:64],
                str(about or "")[:140],
                str(photo_path or "")[:1000],
                str(reason or "")[:200],
            ),
        )
        return int(cursor.lastrowid)


def get_latest_profile_backup(
    data_dir: str | Path,
    phone: str,
) -> dict[str, Any] | None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        row = connection.execute(
            """SELECT id, first_name, last_name, about, photo_path,
                      reason, created_at
               FROM profile_backups
               ORDER BY id DESC
               LIMIT 1"""
        ).fetchone()
    return dict(row) if row else None


def get_chatgpt_daily_usage(
    data_dir: str | Path,
    phone: str,
    usage_date: str,
) -> dict[str, int]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        row = connection.execute(
            """SELECT request_count, input_tokens, output_tokens
               FROM chatgpt_daily_usage
               WHERE usage_date = ?""",
            (str(usage_date),),
        ).fetchone()
    if not row:
        return {"request_count": 0, "input_tokens": 0, "output_tokens": 0}
    return {
        "request_count": int(row["request_count"] or 0),
        "input_tokens": int(row["input_tokens"] or 0),
        "output_tokens": int(row["output_tokens"] or 0),
    }


def increment_chatgpt_daily_usage(
    data_dir: str | Path,
    phone: str,
    usage_date: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO chatgpt_daily_usage (
                   usage_date, request_count, input_tokens, output_tokens
               ) VALUES (?, 1, ?, ?)
               ON CONFLICT(usage_date) DO UPDATE SET
                   request_count = chatgpt_daily_usage.request_count + 1,
                   input_tokens = chatgpt_daily_usage.input_tokens
                       + excluded.input_tokens,
                   output_tokens = chatgpt_daily_usage.output_tokens
                       + excluded.output_tokens,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                str(usage_date),
                max(0, int(input_tokens)),
                max(0, int(output_tokens)),
            ),
        )


def set_runtime_metric(
    data_dir: str | Path,
    phone: str,
    key: str,
    value: Any,
) -> None:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        connection.execute(
            """INSERT INTO runtime_metrics (key, value)
               VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = CURRENT_TIMESTAMP""",
            (str(key), str(value)),
        )


def get_runtime_metrics(
    data_dir: str | Path,
    phone: str,
) -> dict[str, str]:
    db_path = ensure_self_settings(data_dir, phone)
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT key, value FROM runtime_metrics"
        ).fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int = 1_000_000_000,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if not minimum <= parsed <= maximum:
        return default
    return parsed

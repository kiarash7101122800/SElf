"""Safe, per-account feature pack for the Telegram self-bot.

The original project keeps one SQLite database per Telegram account.  This
module builds on that design and deliberately avoids features that claim
access Telegram does not provide (profile visitors, ghost reads, and similar)
or that are primarily intended for unsolicited bulk messaging.
"""

from __future__ import annotations

import ast
import asyncio
import html
import io
import json
import math
import operator
import os
import random
import re
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from telethon import events, functions, types, utils
from telethon.errors import (
    ChatAdminRequiredError,
    FloodWaitError,
    MessageNotModifiedError,
    UserNotParticipantError,
)

from advanced_features import AdvancedFeatureEngine
from control_store import (
    add_word_filter,
    anti_delete_directory,
    archive_message,
    clear_message_archive,
    connect,
    count_archived_messages,
    delete_first_comment_channel,
    delete_tracked_profile,
    delete_word_filter,
    ensure_self_settings,
    get_archived_messages,
    get_force_join_config,
    list_enemies,
    list_enemy_hostile_replies,
    list_first_comment_channels,
    list_friend_affection_replies,
    list_friends,
    list_tracked_profiles,
    list_word_filters,
    purge_expired_archives,
    remove_archived_message,
    set_enemy,
    set_friend,
    upsert_first_comment_channel,
    upsert_tracked_profile,
)

try:
    from PIL import Image, ImageDraw, ImageFont
    Image.MAX_IMAGE_PIXELS = 40_000_000
except ImportError:  # pragma: no cover - reported to the user at runtime
    Image = ImageDraw = ImageFont = None

try:
    import edge_tts
except ImportError:  # pragma: no cover - reported to the user at runtime
    edge_tts = None


PERSIAN_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

LOCK_SETTING_KEYS = {
    "لینک": "lock_links",
    "فوروارد": "lock_forwards",
    "عکس": "lock_photos",
    "ویدیو": "lock_videos",
    "ویدئو": "lock_videos",
    "گیف": "lock_gifs",
    "استیکر": "lock_stickers",
    "ویس": "lock_voice",
    "فایل": "lock_files",
    "نظرسنجی": "lock_polls",
}

STYLE_ALIASES = {
    "none": "none",
    "خاموش": "none",
    "عادی": "none",
    "bold": "bold",
    "بولد": "bold",
    "ضخیم": "bold",
    "italic": "italic",
    "ایتالیک": "italic",
    "کج": "italic",
    "code": "code",
    "کد": "code",
    "strike": "strike",
    "خط خورده": "strike",
    "underline": "underline",
    "زیرخط": "underline",
    "spoiler": "spoiler",
    "اسپویلر": "spoiler",
}

KNOWN_PLAIN_COMMANDS = {
    "help",
    "راهنما",
    "پنل",
    "panel",
    "menu",
    "منو",
    "status",
    "وضعیت",
    "heart",
    "قلب",
    "tagall",
    "تگ",
    "tagadmins",
    "تگ ادمین ها",
    "sessions",
    "نشست های فعال",
    "listfonts",
    "لیست فونت",
    "secretary",
    "منشی",
    "groups",
    "گروه ها",
    "tools",
    "ابزار",
    "settings",
    "تنظیمات",
    "forward",
    "فوروارد",
    "تنظیم دوست",
    "ثبت دوست",
    "دوست کن",
    "حذف دوست",
    "دوست حذف",
    "دوستان",
    "دشمنان",
    "استیکر",
    "لغو تاس",
    "لغو کازینو",
    "لغو دوز",
    "دوز",
    "تنظیم دشمن",
    "ثبت دشمن",
    "دشمن کن",
    "حذف دشمن",
    "دشمن حذف",
    "توقف ارسال",
    "ارسال خاموش",
}

KNOWN_PLAIN_COMMAND_PREFIXES = (
    "تنظیم تاس ",
    "تاس ",
    "تنظیم کازینو ",
    "تنظیم اسلات ",
    "کازینو ",
    "اسلات ",
    "دوز ",
    "تنظیم دوست ",
    "ثبت دوست ",
    "دوست کن ",
    "حذف دوست ",
    "دوست حذف ",
    "تنظیم دشمن ",
    "ثبت دشمن ",
    "دشمن کن ",
    "حذف دشمن ",
    "دشمن حذف ",
    "ارسال ",
)

PERSIAN_FEATURE_COMMAND_ROOTS = (
    "تنظیم تاس",
    "تنظیم کازینو",
    "تنظیم اسلات",
    "خروج از بایگانی",
    "عضویت اجباری",
    "ری‌اکت خودکار",
    "کامنت‌های اول",
    "کامنت اول ها",
    "متن به ویس",
    "فیلتر افزودن",
    "فیلتر حذف",
    "پروفایل ها",
    "پروفایل‌ها",
    "کامنت اول",
    "محبت دوست",
    "پاسخ دشمن",
    "ماشین حساب",
    "رفع بلاک",
    "رفع سکوت",
    "حالت متن",
    "متن امضا",
    "متن لوگو",
    "ویس ذخیره",
    "ویس سرچ",
    "ویس حذف",
    "ضدحذف",
    "استیکر",
    "شیر یا خط",
    "شیرخط",
    "عدد تصادفی",
    "انتخاب",
    "سنگ کاغذ قیچی",
    "تاس",
    "کازینو",
    "اسلات",
    "دوز",
    "لغو دوز",
    "دشمن",
    "امضا",
    "سین",
    "ری‌اکت",
    "روابط",
    "قفل ها",
    "قفل‌ها",
    "قفل",
    "فیلترها",
    "فیلتر",
    "سکوت",
    "آنبلاک",
    "بلاک",
    "دانلود",
    "مخفی",
    "بایگانی",
    "نمایش",
    "لوگو",
    "ترجمه",
    "حساب",
    "ویس",
    "آهنگ",
    "تایپ",
    "شمارش",
    "پروفایل",
    "قیمت",
    "ارز",
    "اسکرین",
)

CRYPTO_IDS = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "بیتکوین": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "اتریوم": "ethereum",
    "ton": "the-open-network",
    "تون": "the-open-network",
    "sol": "solana",
    "solana": "solana",
    "سولانا": "solana",
    "doge": "dogecoin",
    "دوج": "dogecoin",
    "usdt": "tether",
    "تتر": "tether",
}

FRIEND_AFFECTION_REPLIES = (
    "قربونت برم عزیز دلم 😍❤️",
    "فدات بشم من، جان دلم بگو 🥹🫶",
    "الهی قربون حرف زدنت برم 😘",
    "جون دلم، من حواسم بهت هست ❤️",
    "عزیز دلی تو، قربونت برم 🌹",
    "قربون اون دل مهربونت برم 🥰",
    "فدای تو بشم خوشگلم 😍",
    "الهی دورت بگردم من ❤️✨",
)

MAGIC_DICE_EMOTICON = "🎲"
MAGIC_CASINO_EMOTICON = "🎰"
MAGIC_DICE_ARM_TTL_SECONDS = 180
MAGIC_DICE_MAX_ATTEMPTS = 48
MAGIC_CASINO_MAX_ATTEMPTS = 96
CASUAL_CHOICES_MAX_ITEMS = 30
TIC_TAC_TOE_TTL_SECONDS = 900
TIC_TAC_TOE_WINNING_LINES = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)
TIC_TAC_TOE_EMPTY_CELLS = (
    "1️⃣",
    "2️⃣",
    "3️⃣",
    "4️⃣",
    "5️⃣",
    "6️⃣",
    "7️⃣",
    "8️⃣",
    "9️⃣",
)


class FeatureEngine:
    """Register self commands, moderation hooks, and background jobs."""

    def __init__(self, account: Any):
        self.account = account
        self.client = account.client
        self.phone = str(account.phone)
        self.owner_id = int(account.owner_id)
        self.data_dir = Path(account.account_manager_data_dir)
        self.users_db = Path(account.users_db_path)
        self.db_path = ensure_self_settings(self.data_dir, self.phone)
        self.force_join_notified_at: dict[int, float] = {}
        self.filter_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])
        self.comment_jobs: set[tuple[int, int]] = set()
        self.background_tasks: list[asyncio.Task] = []
        self.ignored_deletions: dict[tuple[int, int], float] = {}
        self.friend_affection_last_message: dict[int, int] = {}
        self.enemy_hostile_last_message: dict[int, int] = {}
        self.magic_dice_targets: dict[int, tuple[int, float]] = {}
        self.magic_casino_targets: dict[int, tuple[int, float]] = {}
        self.magic_dice_internal_chats: set[int] = set()
        self.magic_casino_internal_chats: set[int] = set()
        self.tic_tac_toe_games: dict[int, dict[str, Any]] = {}
        self.anti_delete_dir = anti_delete_directory(
            self.data_dir,
            self.phone,
        )
        self.max_in_memory_media_bytes = max(
            1, min(int(os.getenv("MAX_IN_MEMORY_MEDIA_MB", "50") or 50), 100)
        ) * 1024 * 1024
        self.advanced = AdvancedFeatureEngine(self)

    def start_background_tasks(self) -> None:
        if any(not task.done() for task in self.background_tasks):
            return
        self.background_tasks = [
            asyncio.create_task(
                self.profile_monitor_loop(), name=f"profile-monitor:{self.phone}"
            ),
            asyncio.create_task(
                self.anti_delete_cleanup_loop(), name=f"anti-delete-cleanup:{self.phone}"
            ),
            asyncio.create_task(
                self.migrate_legacy_media_to_cloud(),
                name=f"media-migration:{self.phone}",
            ),
        ]
        self.background_tasks.extend(self.advanced.start_background_tasks())

    async def stop_background_tasks(self) -> None:
        await self.advanced.stop_background_tasks()
        current = asyncio.current_task()
        tasks = [
            task for task in self.background_tasks
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.background_tasks.clear()

    @staticmethod
    def _cloud_message_id(reference: str) -> int:
        value = str(reference or "")
        if not value.startswith("tg:"):
            return 0
        try:
            return int(value.split(":", 1)[1])
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _fetch_bounded_bytes(
        url: str,
        *,
        max_bytes: int,
        timeout: int = 30,
        params: dict[str, Any] | None = None,
        required_content_prefix: str = "",
    ) -> tuple[bytes, dict[str, str]]:
        """Fetch remote content into memory with a strict streaming size cap."""
        with requests.get(
            url, params=params, timeout=timeout, stream=True,
        ) as response:
            response.raise_for_status()
            content_type = str(response.headers.get("content-type") or "").lower()
            if required_content_prefix and not content_type.startswith(
                required_content_prefix.lower()
            ):
                raise ValueError("نوع محتوای دریافتی معتبر نیست")
            declared = int(response.headers.get("content-length") or 0)
            if declared and declared > max_bytes:
                raise ValueError("حجم فایل بیشتر از سقف حافظه است")
            chunks = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                chunks.extend(chunk)
                if len(chunks) > max_bytes:
                    raise ValueError("حجم فایل بیشتر از سقف حافظه است")
            return bytes(chunks), {
                str(key).lower(): str(value) for key, value in response.headers.items()
            }

    async def save_message_to_cloud(self, message, *, caption: str = "") -> str:
        """Copy a Telegram message to Saved Messages without touching disk."""
        try:
            saved = await self.client.forward_messages("me", message)
            if isinstance(saved, (list, tuple)):
                saved = saved[0] if saved else None
            message_id = int(getattr(saved, "id", 0) or 0)
            if message_id:
                return f"tg:{message_id}"
        except Exception:
            pass

        raw_text = str(getattr(message, "raw_text", "") or "")
        if not getattr(message, "media", None):
            saved = await self.queued_send_message(
                "me", (caption + "\n\n" + raw_text).strip()[:4000],
                priority=70, parse_mode=None, silent=True,
            )
            return f"tg:{int(getattr(saved, 'id', 0) or 0)}"

        file_info = getattr(message, "file", None)
        declared_size = int(getattr(file_info, "size", 0) or 0)
        if declared_size and declared_size > self.max_in_memory_media_bytes:
            raise ValueError("حجم رسانه بیشتر از سقف ذخیره ابری است.")
        payload = await self.client.download_media(message, file=bytes)
        if not payload or len(payload) > self.max_in_memory_media_bytes:
            raise ValueError("رسانه قابل ذخیره در حافظه نیست.")
        buffer = io.BytesIO(payload)
        buffer.name = (
            str(getattr(file_info, "name", "") or "").strip()
            or f"telegram_media_{getattr(message, 'id', 'file')}.bin"
        )
        saved = await self.queued_send_file(
            "me", buffer, priority=70,
            caption=(caption or raw_text)[:1024], parse_mode=None, silent=True,
        )
        return f"tg:{int(getattr(saved, 'id', 0) or 0)}"

    async def migrate_legacy_media_to_cloud(self) -> None:
        """Move legacy local media to Saved Messages, then erase old folders.

        Transient Telegram failures are retried.  A referenced file is never
        removed before its Saved Messages copy is confirmed.  Orphaned files
        are removed only after no database row points to local media.
        """
        await asyncio.sleep(2)
        mappings = (
            ("voice_library", "id", "file_path", "🎙 انتقال بانک ویس"),
            ("message_archive", "rowid", "media_path", "🛡 انتقال آرشیو ضدحذف"),
            ("auto_reply_responses", "id", "media_path", "🔁 انتقال رسانه پاسخ خودکار"),
            ("schedule_jobs", "id", "media_path", "⏰ انتقال رسانه زمان‌بندی"),
            ("profile_backups", "id", "photo_path", "👤 انتقال بکاپ پروفایل"),
        )

        for attempt in range(3):
            failed = 0
            for table, key_column, path_column, caption in mappings:
                try:
                    with connect(self.db_path) as connection:
                        rows = connection.execute(
                            f"SELECT {key_column} AS item_id, {path_column} AS media_ref "
                            f"FROM {table} WHERE {path_column} != ''"
                        ).fetchall()
                except sqlite3.Error:
                    continue

                for row in rows:
                    reference = str(row["media_ref"] or "")
                    if reference.startswith(("tg:", "botfile:")):
                        continue
                    path = Path(reference)
                    if not path.is_file():
                        with connect(self.db_path) as connection:
                            connection.execute(
                                f"UPDATE {table} SET {path_column} = '' "
                                f"WHERE {key_column} = ?",
                                (row["item_id"],),
                            )
                        continue
                    try:
                        size = path.stat().st_size
                        if size <= 0 or size > self.max_in_memory_media_bytes:
                            path.unlink(missing_ok=True)
                            new_reference = ""
                        else:
                            payload = await asyncio.to_thread(path.read_bytes)
                            buffer = io.BytesIO(payload)
                            buffer.name = path.name or "legacy-media.bin"
                            try:
                                saved = await self.queued_send_file(
                                    "me", buffer, priority=80,
                                    caption=caption, silent=True,
                                )
                            finally:
                                buffer.close()
                            saved_id = int(getattr(saved, "id", 0) or 0)
                            if not saved_id:
                                raise RuntimeError("Saved Messages id دریافت نشد")
                            new_reference = f"tg:{saved_id}"
                            path.unlink(missing_ok=True)
                        with connect(self.db_path) as connection:
                            connection.execute(
                                f"UPDATE {table} SET {path_column} = ? "
                                f"WHERE {key_column} = ?",
                                (new_reference, row["item_id"]),
                            )
                    except Exception:
                        failed += 1
            if not failed:
                break
            if attempt < 2:
                await asyncio.sleep(10)

        local_references = 0
        for table, _key_column, path_column, _caption in mappings:
            try:
                with connect(self.db_path) as connection:
                    rows = connection.execute(
                        f"SELECT {path_column} FROM {table} "
                        f"WHERE {path_column} != ''"
                    ).fetchall()
                local_references += sum(
                    1 for row in rows
                    if not str(row[0] or "").startswith(("tg:", "botfile:"))
                )
            except sqlite3.Error:
                continue

        if local_references:
            return

        prefixes = (
            f"voices_{self.phone.replace('+', '')}",
            f"anti_delete_{self.phone.replace('+', '')}",
            f"profile_backups_{self.phone.replace('+', '')}",
            f"schedule_media_{self.phone.replace('+', '')}",
            f"autoreply_media_{self.phone.replace('+', '')}",
        )
        for name in prefixes:
            directory = self.data_dir / name
            if directory.is_dir():
                try:
                    await asyncio.to_thread(shutil.rmtree, directory)
                except OSError:
                    pass

    async def register_handlers(self) -> None:
        # Security-sensitive handlers are registered first so a locked private
        # chat is stopped before secretary, reactions, or other incoming tools.
        await self.advanced.register_handlers()

        @self.client.on(events.NewMessage(outgoing=True))
        async def magic_game_router(event):
            try:
                if not self.is_owner_command_event(event):
                    return
                handled = await self.handle_magic_game_outgoing(event)
                if handled:
                    self.account.last_activity = time.time()
            except FloodWaitError as exc:
                await self.notify_magic_game_failure(
                    getattr(event, "chat_id", 0),
                    "سرگرمی نمایشی",
                    "تلگرام به‌دلیل ارسال‌های پیاپی، اجرای نمایشی را "
                    f"{max(1, int(getattr(exc, 'seconds', 60)))} ثانیه "
                    "محدود کرد.",
                )
            except Exception as exc:
                print(
                    f"خطا در سرگرمی نمایشی برای {self.phone}: "
                    f"{type(exc).__name__}: {exc}"
                )

        @self.client.on(events.NewMessage(outgoing=True))
        async def feature_command_router(event):
            try:
                if not self.is_owner_command_event(event):
                    return
                handled = await self.handle_command(event)
                if handled:
                    self.account.last_activity = time.time()
            except FloodWaitError as exc:
                seconds = max(1, int(getattr(exc, "seconds", 60)))
                print(
                    f"محدودیت تلگرام برای قابلیت‌های تکمیلی "
                    f"{self.phone}: {seconds} ثانیه"
                )
            except Exception as exc:
                print(
                    f"خطا در قابلیت‌های تکمیلی برای {self.phone}: "
                    f"{type(exc).__name__}: {exc}"
                )

        @self.client.on(events.NewMessage(outgoing=True))
        async def outgoing_style_router(event):
            try:
                await self.apply_outgoing_style(event)
            except (FloodWaitError, MessageNotModifiedError):
                return
            except Exception as exc:
                print(
                    f"خطا در حالت متن برای {self.phone}: "
                    f"{type(exc).__name__}"
                )

        @self.client.on(events.NewMessage(incoming=True))
        async def incoming_feature_router(event):
            try:
                allowed = await self.handle_incoming(event)
                if allowed is False:
                    raise events.StopPropagation
            except FloodWaitError:
                return
            except Exception as exc:
                print(
                    f"خطا در کنترل پیام ورودی برای {self.phone}: "
                    f"{type(exc).__name__}: {exc}"
                )

        @self.client.on(events.NewMessage(incoming=True))
        async def first_comment_router(event):
            try:
                await self.handle_first_comment(event)
            except Exception as exc:
                print(
                    f"خطا در کامنت اول برای {self.phone}: "
                    f"{type(exc).__name__}: {exc}"
                )

        @self.client.on(events.MessageDeleted())
        async def anti_delete_router(event):
            try:
                await self.handle_deleted_messages(event)
            except FloodWaitError:
                return
            except Exception as exc:
                print(
                    f"خطا در ضدحذف برای {self.phone}: "
                    f"{type(exc).__name__}: {exc}"
                )

    def settings(self) -> dict[str, str]:
        return self.account.get_data()

    def save_settings(self, values: dict[str, Any]) -> None:
        current = self.settings()
        current.update({key: str(value) for key, value in values.items()})
        self.account.put_data(current)

    @staticmethod
    def normalize_digits(value: str) -> str:
        return str(value or "").translate(PERSIAN_DIGITS)

    def is_owner_command_event(self, event) -> bool:
        checker = getattr(self.account, "is_owner_outgoing_event", None)
        if callable(checker):
            return bool(checker(event))
        if getattr(getattr(event, "message", None), "from_scheduled", False):
            return False
        outgoing = getattr(event, "out", None)
        if outgoing is not None:
            return bool(outgoing)
        return getattr(event, "sender_id", None) == self.owner_id

    @classmethod
    def normalize_command_text(cls, value: str) -> str:
        text = cls.normalize_digits(value).strip()
        if not text:
            return ""
        text = text.replace("ي", "ی").replace("ك", "ک")
        if text.startswith("/"):
            return f".{text[1:]}"
        if text.startswith("."):
            return text
        lower = text.lower()
        for root in PERSIAN_FEATURE_COMMAND_ROOTS:
            if lower == root or lower.startswith(f"{root} "):
                return f".{text}"
        return text

    @classmethod
    def looks_like_command(cls, value: str) -> bool:
        raw = str(value or "").strip()
        if not raw:
            return False
        normalized = cls.normalize_command_text(raw)
        lower = normalized.lower()
        return (
            normalized.startswith(".")
            or raw.startswith("/")
            or lower in KNOWN_PLAIN_COMMANDS
            or lower.startswith(KNOWN_PLAIN_COMMAND_PREFIXES)
        )

    @staticmethod
    def on_off(value: str) -> str | None:
        normalized = str(value or "").strip().lower()
        if normalized in {"on", "روشن", "فعال"}:
            return "on"
        if normalized in {"off", "خاموش", "غیرفعال"}:
            return "off"
        return None

    async def safe_edit(self, event, text: str, **kwargs) -> None:
        try:
            await event.edit(text, **kwargs)
        except MessageNotModifiedError:
            return
        except FloodWaitError:
            # A fallback SendMessageRequest during a flood limit only extends
            # the restriction, so let the caller defer the operation.
            raise
        except Exception:
            await event.reply(text, **kwargs)

    async def queued_send_message(self, entity, message, **kwargs):
        priority = kwargs.pop("priority", 50)
        sender = getattr(self.account, "queued_send_message", None)
        if sender is not None:
            return await sender(
                entity,
                message,
                priority=priority,
                **kwargs,
            )
        return await self.client.send_message(entity, message, **kwargs)

    async def queued_send_file(self, entity, file, **kwargs):
        priority = kwargs.pop("priority", 50)
        sender = getattr(self.account, "queued_send_file", None)
        if sender is not None:
            return await sender(
                entity,
                file,
                priority=priority,
                **kwargs,
            )
        return await self.client.send_file(entity, file, **kwargs)

    async def replied_message(self, event):
        if not event.is_reply:
            return None
        return await event.get_reply_message()

    @staticmethod
    def dice_reply_to_id(message) -> int | None:
        reply_to_id = getattr(message, "reply_to_msg_id", None)
        if reply_to_id:
            return int(reply_to_id)
        reply_header = getattr(message, "reply_to", None)
        for attribute in ("reply_to_msg_id", "reply_to_top_id"):
            value = getattr(reply_header, attribute, None)
            if value:
                return int(value)
        return None

    def magic_game_state(
        self,
        emoticon: str,
    ) -> tuple[dict[int, tuple[int, float]], set[int], int, str]:
        if emoticon == MAGIC_DICE_EMOTICON:
            return (
                self.magic_dice_targets,
                self.magic_dice_internal_chats,
                MAGIC_DICE_MAX_ATTEMPTS,
                "تاس نمایشی",
            )
        if emoticon == MAGIC_CASINO_EMOTICON:
            return (
                self.magic_casino_targets,
                self.magic_casino_internal_chats,
                MAGIC_CASINO_MAX_ATTEMPTS,
                "کازینوی نمایشی",
            )
        raise ValueError("ایموجی بازی پشتیبانی نمی‌شود.")

    async def arm_magic_game(
        self,
        event,
        target: int,
        emoticon: str,
    ) -> None:
        """Arm one official Telegram game emoji in the current chat."""
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        if not chat_id:
            await self.safe_edit(event, "❌ چت فعلی قابل تشخیص نیست.")
            return
        targets, _, _, _ = self.magic_game_state(emoticon)
        targets[chat_id] = (
            int(target),
            time.monotonic() + MAGIC_DICE_ARM_TTL_SECONDS,
        )
        self.mark_own_deletion(event)
        await event.delete()

    async def arm_magic_dice(self, event, target: int) -> None:
        await self.arm_magic_game(event, target, MAGIC_DICE_EMOTICON)

    async def arm_magic_casino(self, event, target: int) -> None:
        await self.arm_magic_game(event, target, MAGIC_CASINO_EMOTICON)

    async def cancel_magic_game(self, event, emoticon: str) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        targets, _, _, _ = self.magic_game_state(emoticon)
        targets.pop(chat_id, None)
        self.mark_own_deletion(event)
        await event.delete()

    async def cancel_magic_dice(self, event) -> None:
        await self.cancel_magic_game(event, MAGIC_DICE_EMOTICON)

    async def cancel_magic_casino(self, event) -> None:
        await self.cancel_magic_game(event, MAGIC_CASINO_EMOTICON)

    async def roll_magic_game_command(
        self,
        event,
        target: int,
        emoticon: str,
    ) -> None:
        """Delete the command and leave only the requested official result."""
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        if not chat_id:
            await self.safe_edit(event, "❌ چت فعلی قابل تشخیص نیست.")
            return
        reply_to = self.dice_reply_to_id(getattr(event, "message", None))
        targets, _, _, label = self.magic_game_state(emoticon)
        targets.pop(chat_id, None)
        self.mark_own_deletion(event)
        await event.delete()
        try:
            sent = await self.send_magic_game(
                chat_id,
                int(target),
                emoticon,
                reply_to=reply_to,
            )
        except FloodWaitError as exc:
            await self.notify_magic_game_failure(
                chat_id,
                label,
                "تلگرام به‌دلیل ارسال‌های پیاپی، این عملیات را "
                f"{max(1, int(getattr(exc, 'seconds', 60)))} ثانیه "
                "محدود کرد.",
            )
            return
        except Exception as exc:
            await self.notify_magic_game_failure(
                chat_id,
                label,
                f"خطای {type(exc).__name__}",
            )
            return
        if sent is None:
            await self.notify_magic_game_failure(
                chat_id,
                label,
                "پس از چند تلاش نتیجه انتخاب‌شده ساخته نشد.",
            )

    async def roll_magic_dice_command(self, event, target: int) -> None:
        await self.roll_magic_game_command(
            event,
            target,
            MAGIC_DICE_EMOTICON,
        )

    async def roll_magic_casino_command(self, event, target: int) -> None:
        await self.roll_magic_game_command(
            event,
            target,
            MAGIC_CASINO_EMOTICON,
        )

    async def send_plain_game_command(self, event, emoticon: str) -> None:
        """Replace a plain command with one official Telegram game roll."""
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        if not chat_id:
            await self.safe_edit(event, "❌ چت فعلی قابل تشخیص نیست.")
            return
        reply_to = self.dice_reply_to_id(getattr(event, "message", None))
        self.mark_own_deletion(event)
        await event.delete()
        await self.send_official_game(
            chat_id,
            emoticon,
            reply_to=reply_to,
        )

    async def send_official_game(
        self,
        chat_id: int,
        emoticon: str,
        *,
        reply_to: int | None = None,
    ):
        """Send a dice media directly to the current peer.

        ``SendMediaRequest`` is used for real Telethon clients because it keeps
        group/supergroup destinations intact, including send-as sessions.  The
        historical ``send_file`` path remains as a compatibility fallback for
        older Telethon builds and the project's lightweight test clients.
        """
        if callable(self.client) and hasattr(self.client, "get_input_entity"):
            peer = await self.client.get_input_entity(int(chat_id))
            reply_header = (
                types.InputReplyToMessage(reply_to_msg_id=int(reply_to))
                if reply_to
                else None
            )
            result = await self.client(
                functions.messages.SendMediaRequest(
                    peer=peer,
                    media=types.InputMediaDice(emoticon=emoticon),
                    message="",
                    random_id=random.SystemRandom().randint(
                        -(2**63),
                        (2**63) - 1,
                    ),
                    reply_to=reply_header,
                )
            )
            for update in getattr(result, "updates", ()) or ():
                message = getattr(update, "message", None)
                if message is not None:
                    return message
            return result
        return await self.client.send_file(
            chat_id,
            types.InputMediaDice(emoticon=emoticon),
            reply_to=reply_to,
        )

    async def casual_random_number(
        self,
        event,
        start: int,
        end: int,
    ) -> None:
        low, high = sorted((int(start), int(end)))
        if high - low > 1_000_000:
            await self.safe_edit(event, "❌ فاصله اعداد حداکثر یک میلیون باشد.")
            return
        result = random.SystemRandom().randint(low, high)
        await self.safe_edit(
            event,
            f"🎯 عدد تصادفی بین {low} و {high}:\n\n**{result}**",
        )

    async def casual_pick(self, event, raw_items: str) -> None:
        items = [
            item.strip()
            for item in re.split(r"[|،,]", str(raw_items or ""))
            if item.strip()
        ]
        if not 2 <= len(items) <= CASUAL_CHOICES_MAX_ITEMS:
            await self.safe_edit(
                event,
                "❌ بین ۲ تا ۳۰ گزینه بنویسید و با `|` جدا کنید.",
            )
            return
        selected = random.SystemRandom().choice(items)
        await self.safe_edit(event, f"🎉 انتخاب تصادفی:\n\n**{selected}**")

    async def casual_coin(self, event) -> None:
        result = random.SystemRandom().choice(("شیر 🦁", "خط ✍️"))
        await self.safe_edit(event, f"🪙 شیر یا خط:\n\n**{result}**")

    async def casual_rps(self, event, player_choice: str) -> None:
        aliases = {
            "سنگ": "سنگ 🪨",
            "کاغذ": "کاغذ 📄",
            "قیچی": "قیچی ✂️",
        }
        player = aliases[player_choice]
        bot_key = random.SystemRandom().choice(tuple(aliases))
        bot = aliases[bot_key]
        wins = {("سنگ", "قیچی"), ("قیچی", "کاغذ"), ("کاغذ", "سنگ")}
        if bot_key == player_choice:
            result = "مساوی شد 🤝"
        elif (player_choice, bot_key) in wins:
            result = "تو بردی 🎉"
        else:
            result = "این بار من بردم 😄"
        await self.safe_edit(
            event,
            f"🎮 سنگ، کاغذ، قیچی\n\nتو: **{player}**\nمن: **{bot}**\n\n{result}",
        )

    async def handle_magic_game_outgoing(self, event) -> bool:
        """Replace one armed game emoji while keeping betting untouched."""
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        if not chat_id:
            return False

        message = getattr(event, "message", None)
        media = getattr(message, "media", None)
        if not isinstance(media, types.MessageMediaDice):
            return False

        emoticon = str(getattr(media, "emoticon", "") or "")
        if emoticon not in {MAGIC_DICE_EMOTICON, MAGIC_CASINO_EMOTICON}:
            return False
        targets, internal_chats, _, label = self.magic_game_state(emoticon)
        if chat_id in internal_chats:
            return False

        armed = targets.pop(chat_id, None)
        if not armed:
            return False
        target, expires_at = armed
        if time.monotonic() > expires_at:
            return False

        if int(getattr(media, "value", 0) or 0) == int(target):
            return True

        reply_to = self.dice_reply_to_id(message)
        self.mark_own_deletion(event)
        await event.delete()
        sent = await self.send_magic_game(
            chat_id,
            int(target),
            emoticon,
            reply_to=reply_to,
        )
        if sent is None:
            await self.notify_magic_game_failure(
                chat_id,
                label,
                "پس از چند تلاش نتیجه انتخاب‌شده ساخته نشد.",
            )
        return True

    async def handle_magic_dice_outgoing(self, event) -> bool:
        """Compatibility wrapper retained for the v2.7.1 test/API surface."""
        return await self.handle_magic_game_outgoing(event)

    async def handle_magic_casino_outgoing(self, event) -> bool:
        return await self.handle_magic_game_outgoing(event)

    async def send_magic_game(
        self,
        chat_id: int,
        target: int,
        emoticon: str,
        *,
        reply_to: int | None = None,
    ):
        """Retry an official Telegram game emoji and remove non-target rolls."""
        _, internal_chats, max_attempts, _ = self.magic_game_state(emoticon)
        internal_chats.add(int(chat_id))
        try:
            for _ in range(max_attempts):
                sent = await self.send_official_game(
                    chat_id,
                    emoticon,
                    reply_to=reply_to,
                )
                media = getattr(sent, "media", None)
                if int(getattr(media, "value", 0) or 0) == int(target):
                    return sent
                self.mark_own_deletion(sent)
                await sent.delete()
                await asyncio.sleep(0.15)
        finally:
            internal_chats.discard(int(chat_id))
        return None

    async def send_magic_dice(
        self,
        chat_id: int,
        target: int,
        *,
        reply_to: int | None = None,
    ):
        return await self.send_magic_game(
            chat_id,
            target,
            MAGIC_DICE_EMOTICON,
            reply_to=reply_to,
        )

    async def notify_magic_game_failure(
        self,
        chat_id: int,
        label: str,
        reason: str,
    ) -> None:
        """Report a failed display privately so no control text stays in chat."""
        try:
            await self.client.send_message(
                "me",
                f"⚠️ {label} اجرا نشد.\n"
                f"چت: `{int(chat_id or 0)}`\n"
                f"علت: {reason}",
            )
        except Exception:
            pass

    async def notify_magic_dice_failure(
        self,
        chat_id: int,
        reason: str,
    ) -> None:
        await self.notify_magic_game_failure(
            chat_id,
            "تاس نمایشی",
            reason,
        )

    @staticmethod
    def tic_tac_toe_name(entity: Any, fallback: str) -> str:
        first_name = str(getattr(entity, "first_name", "") or "").strip()
        last_name = str(getattr(entity, "last_name", "") or "").strip()
        title = str(getattr(entity, "title", "") or "").strip()
        username = str(getattr(entity, "username", "") or "").strip()
        name = " ".join(part for part in (first_name, last_name) if part)
        return (name or title or (f"@{username}" if username else fallback))[:64]

    @staticmethod
    def tic_tac_toe_winner(board: list[str]) -> str | None:
        for first, second, third in TIC_TAC_TOE_WINNING_LINES:
            if (
                board[first]
                and board[first] == board[second] == board[third]
            ):
                return board[first]
        return None

    @staticmethod
    def tic_tac_toe_board(board: list[str]) -> str:
        cells = [
            value if value else TIC_TAC_TOE_EMPTY_CELLS[index]
            for index, value in enumerate(board)
        ]
        return (
            f"{cells[0]} │ {cells[1]} │ {cells[2]}\n"
            "───────────\n"
            f"{cells[3]} │ {cells[4]} │ {cells[5]}\n"
            "───────────\n"
            f"{cells[6]} │ {cells[7]} │ {cells[8]}"
        )

    def tic_tac_toe_text(
        self,
        game: dict[str, Any],
        *,
        result: str = "",
    ) -> str:
        owner_id, opponent_id = game["players"]
        turn_id = int(game["turn"])
        names = game["names"]
        lines = [
            "🎮 بازی دوز مستقل سلف",
            "",
            f"❌ {names[owner_id]}",
            f"⭕ {names[opponent_id]}",
            "",
            self.tic_tac_toe_board(game["board"]),
        ]
        if result:
            lines.extend(["", result])
        else:
            mark = "❌" if turn_id == owner_id else "⭕"
            lines.extend(
                [
                    "",
                    f"نوبت {mark} {names[turn_id]} است.",
                    "حرکت: دوز ۱ تا دوز ۹",
                ]
            )
        lines.extend(
            [
                "",
                "این بازی به شرط‌بندی و موجودی متصل نیست.",
            ]
        )
        return "\n".join(lines)

    async def start_tic_tac_toe(self, event) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        if not chat_id or not getattr(event, "is_reply", False):
            await self.safe_edit(
                event,
                "❌ برای شروع دوز، روی پیام حریف ریپلای و «دوز» را ارسال کنید.",
            )
            return

        now = time.monotonic()
        current = self.tic_tac_toe_games.get(chat_id)
        if current and now <= float(current["expires_at"]):
            await self.safe_edit(
                event,
                "❌ در این چت یک بازی دوز فعال است؛ ابتدا «لغو دوز» را بفرستید.",
            )
            return
        self.tic_tac_toe_games.pop(chat_id, None)

        reply = await self.replied_message(event)
        opponent_id = int(getattr(reply, "sender_id", 0) or 0)
        if not opponent_id or opponent_id == self.owner_id:
            await self.safe_edit(event, "❌ حریف معتبر نیست.")
            return
        try:
            opponent = await reply.get_sender()
        except Exception:
            opponent = None
        if getattr(opponent, "bot", False):
            await self.safe_edit(event, "❌ بازی دوز با حساب ربات شروع نمی‌شود.")
            return
        try:
            owner = await self.client.get_me()
        except Exception:
            owner = None

        names = {
            self.owner_id: self.tic_tac_toe_name(owner, "صاحب سلف"),
            opponent_id: self.tic_tac_toe_name(opponent, f"کاربر {opponent_id}"),
        }
        game = {
            "players": (self.owner_id, opponent_id),
            "names": names,
            "board": [""] * 9,
            "turn": self.owner_id,
            "expires_at": now + TIC_TAC_TOE_TTL_SECONDS,
        }
        self.tic_tac_toe_games[chat_id] = game
        reply_to = self.dice_reply_to_id(getattr(event, "message", None))
        self.mark_own_deletion(event)
        await event.delete()
        await self.client.send_message(
            chat_id,
            self.tic_tac_toe_text(game),
            reply_to=reply_to,
            parse_mode=None,
        )

    async def cancel_tic_tac_toe(self, event) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        existed = self.tic_tac_toe_games.pop(chat_id, None) is not None
        if existed:
            self.mark_own_deletion(event)
            await event.delete()
            await self.client.send_message(
                chat_id,
                "⛔ بازی دوز لغو شد.\n"
                "این بازی به شرط‌بندی و موجودی متصل نبود.",
                parse_mode=None,
            )
            return
        await self.safe_edit(event, "❌ در این چت بازی دوز فعالی وجود ندارد.")

    async def play_tic_tac_toe(
        self,
        event,
        player_id: int,
        position: int,
    ) -> bool:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        game = self.tic_tac_toe_games.get(chat_id)
        if not game:
            return False
        if time.monotonic() > float(game["expires_at"]):
            self.tic_tac_toe_games.pop(chat_id, None)
            if player_id == self.owner_id:
                await self.safe_edit(
                    event,
                    "⌛ بازی دوز قبلی منقضی شده است؛ دوباره بازی را شروع کنید.",
                )
            return player_id == self.owner_id
        if player_id not in game["players"]:
            return False

        if int(game["turn"]) != int(player_id):
            await event.reply(
                f"⏳ هنوز نوبت {game['names'][game['turn']]} است.",
                parse_mode=None,
            )
            return True
        cell_index = int(position) - 1
        if game["board"][cell_index]:
            await event.reply("❌ این خانه قبلاً انتخاب شده است.", parse_mode=None)
            return True

        owner_id, opponent_id = game["players"]
        mark = "❌" if player_id == owner_id else "⭕"
        game["board"][cell_index] = mark
        winner = self.tic_tac_toe_winner(game["board"])
        draw = winner is None and all(game["board"])
        if winner:
            result = f"🏆 {game['names'][player_id]} با {winner} برنده شد."
            self.tic_tac_toe_games.pop(chat_id, None)
        elif draw:
            result = "🤝 بازی مساوی شد."
            self.tic_tac_toe_games.pop(chat_id, None)
        else:
            game["turn"] = (
                opponent_id if player_id == owner_id else owner_id
            )
            game["expires_at"] = time.monotonic() + TIC_TAC_TOE_TTL_SECONDS
            result = ""

        if player_id == self.owner_id:
            self.mark_own_deletion(event)
            await event.delete()
        await self.client.send_message(
            chat_id,
            self.tic_tac_toe_text(game, result=result),
            parse_mode=None,
        )
        return True

    async def handle_tic_tac_toe_incoming(self, event) -> bool:
        raw = self.normalize_digits((event.raw_text or "").strip())
        match = re.fullmatch(r"\.?(?:دوز|xo|ttt)\s+([1-9])", raw.lower())
        if not match:
            return False
        return await self.play_tic_tac_toe(
            event,
            int(getattr(event, "sender_id", 0) or 0),
            int(match.group(1)),
        )

    async def resolve_target(self, event, raw: str = "") -> tuple[int, Any]:
        reply = await self.replied_message(event)
        if reply and reply.sender_id:
            entity = await reply.get_sender()
            return int(reply.sender_id), entity

        target = self.normalize_digits(raw.strip())
        if not target:
            raise ValueError("روی پیام کاربر ریپلای کنید یا آیدی او را بنویسید.")
        entity_ref: int | str
        if target.lstrip("-").isdigit():
            entity_ref = int(target)
        else:
            entity_ref = target
        entity = await self.client.get_entity(entity_ref)
        return int(entity.id), entity

    async def handle_command(self, event) -> bool:
        raw = (event.raw_text or "").strip()
        if not raw:
            return False
        text = self.normalize_command_text(raw)
        lower = text.lower()

        if lower in {".دوز", ".xo", ".ttt"}:
            await self.start_tic_tac_toe(event)
            return True

        if lower in {".کازینو", ".اسلات", ".casino", ".slot"}:
            await self.send_plain_game_command(event, MAGIC_CASINO_EMOTICON)
            return True

        if lower in {".تاس", ".dice"}:
            await self.send_plain_game_command(event, MAGIC_DICE_EMOTICON)
            return True

        if lower in {".شیر یا خط", ".شیرخط", ".coin"}:
            await self.casual_coin(event)
            return True

        match = re.match(
            r"^\.(?:عدد تصادفی|random)\s+(-?\d+)\s+(-?\d+)$",
            lower,
        )
        if match:
            await self.casual_random_number(
                event,
                int(match.group(1)),
                int(match.group(2)),
            )
            return True

        match = re.match(r"^\.(?:انتخاب|pick)\s+(.+)$", text, re.DOTALL)
        if match:
            await self.casual_pick(event, match.group(1))
            return True

        match = re.match(r"^\.(?:سنگ کاغذ قیچی|rps)\s+(سنگ|کاغذ|قیچی)$", lower)
        if match:
            await self.casual_rps(event, match.group(1))
            return True

        match = re.match(r"^\.(?:دوز|xo|ttt)\s+([1-9])$", lower)
        if match:
            handled = await self.play_tic_tac_toe(
                event,
                self.owner_id,
                int(match.group(1)),
            )
            if not handled:
                await self.safe_edit(
                    event,
                    "❌ در این چت بازی دوز فعالی وجود ندارد.",
                )
            return True

        if lower in {
            "لغو دوز",
            ".لغو دوز",
            ".xo cancel",
            ".ttt cancel",
        }:
            await self.cancel_tic_tac_toe(event)
            return True

        match = re.match(
            r"^\.(?:magicdice|تنظیم\s+تاس)\s+([1-6])$",
            lower,
        )
        if match:
            await self.arm_magic_dice(event, int(match.group(1)))
            return True

        match = re.match(
            r"^\.(?:dice|تاس)\s+([1-6])$",
            lower,
        )
        if match:
            await self.roll_magic_dice_command(event, int(match.group(1)))
            return True

        match = re.match(
            r"^\.(?:magicslot|تنظیم\s+(?:کازینو|اسلات))\s+"
            r"(\d{1,2}|جکپات|jackpot)$",
            lower,
        )
        if match:
            value = match.group(1)
            target = 64 if value in {"جکپات", "jackpot"} else int(value)
            if not 1 <= target <= 64:
                await self.safe_edit(
                    event,
                    "❌ نتیجه کازینو باید عدد ۱ تا ۶۴ یا «جکپات» باشد.",
                )
            else:
                await self.arm_magic_casino(event, target)
            return True

        match = re.match(
            r"^\.(?:slot|casino|کازینو|اسلات)\s+"
            r"(\d{1,2}|جکپات|jackpot)$",
            lower,
        )
        if match:
            value = match.group(1)
            target = 64 if value in {"جکپات", "jackpot"} else int(value)
            if not 1 <= target <= 64:
                await self.safe_edit(
                    event,
                    "❌ نتیجه کازینو باید عدد ۱ تا ۶۴ یا «جکپات» باشد.",
                )
            else:
                await self.roll_magic_casino_command(event, target)
            return True

        if lower in {
            "لغو تاس",
            ".لغو تاس",
            ".dice cancel",
            ".magicdice cancel",
        }:
            await self.cancel_magic_dice(event)
            return True

        if lower in {
            "لغو کازینو",
            ".لغو کازینو",
            "لغو اسلات",
            ".لغو اسلات",
            ".slot cancel",
            ".casino cancel",
            ".magicslot cancel",
        }:
            await self.cancel_magic_casino(event)
            return True

        if lower in {".sticker", ".استیکر", "استیکر"}:
            await self.quote_sticker(event)
            return True

        match = re.match(
            r"^\.(?:antidelete|ضدحذف)\s+(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"anti_delete_enabled": state})
            await self.safe_edit(
                event,
                f"✅ ضدحذف پیام‌های عادی "
                f"{'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.match(
            r"^\.ضدحذف\s+(پیوی|گروه|کانال)\s+"
            r"(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            scope_keys = {
                "پیوی": "anti_delete_private",
                "گروه": "anti_delete_groups",
                "کانال": "anti_delete_channels",
            }
            state = self.on_off(match.group(2))
            self.save_settings({scope_keys[match.group(1)]: state})
            await self.safe_edit(
                event,
                f"✅ ضدحذف {match.group(1)} "
                f"{'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        if lower in {
            ".antidelete status",
            ".ضدحذف وضعیت",
            ".ضدحذف",
        }:
            settings = self.settings()
            count = count_archived_messages(self.data_dir, self.phone)
            await self.safe_edit(
                event,
                "🗑 وضعیت ضدحذف\n\n"
                f"فعال: {'بله' if settings.get('anti_delete_enabled') == 'on' else 'خیر'}\n"
                f"پیوی: {'بله' if settings.get('anti_delete_private') == 'on' else 'خیر'}\n"
                f"گروه: {'بله' if settings.get('anti_delete_groups') == 'on' else 'خیر'}\n"
                f"کانال: {'بله' if settings.get('anti_delete_channels') == 'on' else 'خیر'}\n"
                f"سقف هر رسانه: {settings.get('anti_delete_max_mb', '50')} مگابایت\n"
                f"نگهداری موقت: {settings.get('anti_delete_retention_days', '7')} روز\n"
                f"پیام‌های در انتظار: {count}",
            )
            return True

        match = re.match(
            r"^\.(?:antidelete max|ضدحذف حجم)\s+(\d+)$",
            lower,
        )
        if match:
            max_mb = max(1, min(int(match.group(1)), 200))
            self.save_settings({"anti_delete_max_mb": str(max_mb)})
            await self.safe_edit(
                event,
                f"✅ سقف ذخیره هر رسانه ضدحذف روی {max_mb} مگابایت قرار گرفت.",
            )
            return True

        match = re.match(
            r"^\.(?:antidelete retention|ضدحذف نگهداری)\s+(\d+)$",
            lower,
        )
        if match:
            days = max(1, min(int(match.group(1)), 30))
            self.save_settings({"anti_delete_retention_days": str(days)})
            await self.safe_edit(
                event,
                f"✅ نگهداری موقت ضدحذف روی {days} روز قرار گرفت.",
            )
            return True

        if lower in {
            ".antidelete clear",
            ".ضدحذف پاکسازی",
        }:
            deleted = clear_message_archive(self.data_dir, self.phone)
            await self.safe_edit(
                event,
                f"✅ آرشیو موقت ضدحذف پاک شد؛ {deleted} پیام حذف شد.",
            )
            return True

        if lower in {".friends", ".دوستان", "دوستان"}:
            await self.show_relations(event, "friends")
            return True
        if lower in {".enemies", ".دشمنان", "دشمنان"}:
            await self.show_relations(event, "enemies")
            return True

        match = re.match(
            r"^\.(?:friend|دوست)\s+(add|del|افزودن|حذف)(?:\s+(.+))?$",
            lower,
        )
        if match:
            await self.change_relation(
                event,
                "friend",
                match.group(1) in {"add", "افزودن"},
                match.group(2) or "",
            )
            return True

        match = re.match(
            r"^(?:\.?(?:تنظیم|ثبت)\s+دوست|\.?دوست\s+کن)"
            r"(?:\s+(.+))?$",
            lower,
        )
        if match:
            await self.change_relation(
                event,
                "friend",
                True,
                match.group(1) or "",
            )
            return True

        match = re.match(
            r"^(?:\.?حذف\s+دوست|\.?دوست\s+حذف)"
            r"(?:\s+(.+))?$",
            lower,
        )
        if match:
            await self.change_relation(
                event,
                "friend",
                False,
                match.group(1) or "",
            )
            return True

        match = re.match(
            r"^\.(?:friendlove|محبت دوست)\s+"
            r"(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"friend_affection_reply": state or "off"})
            await self.safe_edit(
                event,
                "✅ پاسخ صمیمی به دوستان "
                f"{'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.match(
            r"^\.(?:enemy|دشمن)\s+(add|del|افزودن|حذف)(?:\s+(.+))?$",
            lower,
        )
        if match:
            await self.change_relation(
                event,
                "enemy",
                match.group(1) in {"add", "افزودن"},
                match.group(2) or "",
            )
            return True

        match = re.match(
            r"^(?:\.?(?:تنظیم|ثبت)\s+دشمن|\.?دشمن\s+کن)"
            r"(?:\s+(.+))?$",
            lower,
        )
        if match:
            await self.change_relation(
                event,
                "enemy",
                True,
                match.group(1) or "",
            )
            return True

        match = re.match(
            r"^(?:\.?حذف\s+دشمن|\.?دشمن\s+حذف)"
            r"(?:\s+(.+))?$",
            lower,
        )
        if match:
            await self.change_relation(
                event,
                "enemy",
                False,
                match.group(1) or "",
            )
            return True

        match = re.match(
            r"^\.(?:enemytalk|پاسخ دشمن)\s+"
            r"(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"enemy_hostile_reply": state or "off"})
            await self.safe_edit(
                event,
                "✅ پاسخ خودکار به دشمنان "
                f"{'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.match(r"^\.(?:textstyle|حالت متن)\s+(.+)$", lower)
        if match:
            style = STYLE_ALIASES.get(match.group(1).strip())
            if style is None:
                await self.safe_edit(
                    event,
                    "❌ حالت معتبر: عادی، بولد، ایتالیک، کد، خط خورده، "
                    "زیرخط یا اسپویلر",
                )
            else:
                self.save_settings({"outgoing_text_style": style})
                await self.safe_edit(event, f"✅ حالت متن روی `{style}` تنظیم شد.")
            return True

        match = re.match(r"^\.(?:signature|امضا)\s+(on|off|روشن|خاموش)$", lower)
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"outgoing_signature_enabled": state})
            await self.safe_edit(
                event,
                f"✅ امضای خودکار {'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.match(r"^\.(?:signature text|متن امضا)\s+([\s\S]+)$", text)
        if match:
            signature = match.group(1).strip()
            if not 1 <= len(signature) <= 300:
                await self.safe_edit(event, "❌ متن امضا باید حداکثر ۳۰۰ نویسه باشد.")
            else:
                self.save_settings({"outgoing_signature_text": signature})
                await self.safe_edit(event, "✅ متن امضا ذخیره شد.")
            return True

        match = re.match(
            r"^\.(?:forcejoin|عضویت اجباری)\s+(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"force_join_private": state})
            await self.safe_edit(
                event,
                f"✅ عضویت اجباری پیوی {'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.match(
            r"^\.(?:seen|سین)\s+(private|group|پیوی|گروه)\s+"
            r"(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            scope = "private" if match.group(1) in {"private", "پیوی"} else "groups"
            state = self.on_off(match.group(2))
            self.save_settings({f"auto_read_{scope}": state})
            await self.safe_edit(
                event,
                f"✅ سین خودکار {scope} {'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.match(
            r"^\.(?:autoreact|ری‌اکت خودکار)\s+"
            r"(on|off|روشن|خاموش)(?:\s+(.+))?$",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            state = self.on_off(match.group(1))
            values: dict[str, str] = {"auto_reaction": state or "off"}
            if match.group(2):
                values["auto_reaction_emoji"] = match.group(2).strip()[:16]
            self.save_settings(values)
            await self.safe_edit(
                event,
                f"✅ ری‌اکت خودکار {'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.match(r"^\.(?:react|ری‌اکت)(?:\s+(.+))?$", text, re.I)
        if match:
            await self.manual_reaction(event, (match.group(1) or "❤️").strip())
            return True

        match = re.match(
            r"^\.(?:relations|روابط)\s+(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"relationship_reaction": state})
            await self.safe_edit(
                event,
                f"✅ واکنش دوست/دشمن {'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.match(
            r"^\.(?:lock|قفل)\s+(\S+)\s+(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            lock_name = match.group(1)
            key = LOCK_SETTING_KEYS.get(lock_name)
            if not key:
                await self.safe_edit(
                    event,
                    "❌ قفل معتبر: لینک، فوروارد، عکس، ویدیو، گیف، "
                    "استیکر، ویس، فایل یا نظرسنجی",
                )
            else:
                state = self.on_off(match.group(2))
                self.save_settings({key: state})
                await self.safe_edit(
                    event,
                    f"✅ قفل {lock_name} {'فعال' if state == 'on' else 'غیرفعال'} شد.",
                )
            return True

        if lower in {".locks", ".قفل ها", ".قفل‌ها"}:
            await self.show_locks(event)
            return True

        match = re.match(
            r"^\.(?:filter|فیلتر)\s+(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"word_filter_enabled": state})
            await self.safe_edit(
                event,
                f"✅ فیلتر کلمات {'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.match(
            r"^\.(?:filter add|فیلتر افزودن)\s+(.+?)(?:\|"
            r"(delete|warn|mute|block|حذف|اخطار|سکوت|بلاک))?$",
            text,
            flags=re.I | re.S,
        )
        if match:
            action_map = {
                "حذف": "delete",
                "اخطار": "warn",
                "سکوت": "mute",
                "بلاک": "block",
            }
            action = action_map.get(
                (match.group(2) or "delete").lower(),
                (match.group(2) or "delete").lower(),
            )
            try:
                item_id = add_word_filter(
                    self.data_dir,
                    self.phone,
                    match.group(1).strip(),
                    action,
                )
                self.filter_cache = (0.0, [])
                await self.safe_edit(
                    event,
                    f"✅ فیلتر #{item_id} با عملیات `{action}` افزوده شد.",
                )
            except ValueError as exc:
                await self.safe_edit(event, f"❌ {exc}")
            return True

        match = re.match(r"^\.(?:filter del|فیلتر حذف)\s+(\d+)$", lower)
        if match:
            deleted = delete_word_filter(
                self.data_dir,
                self.phone,
                int(match.group(1)),
            )
            self.filter_cache = (0.0, [])
            await self.safe_edit(
                event,
                "✅ فیلتر حذف شد." if deleted else "❌ فیلتر پیدا نشد.",
            )
            return True

        if lower in {".filters", ".فیلترها"}:
            await self.show_filters(event)
            return True

        match = re.match(r"^\.(?:mute|سکوت)(?:\s+(\d+))?$", lower)
        if match:
            await self.mute_target(event, int(match.group(1) or 10))
            return True

        if lower in {".unmute", ".رفع سکوت"}:
            await self.unmute_target(event)
            return True

        if lower in {".block", ".بلاک"}:
            await self.block_target(event, blocked=True)
            return True

        if lower in {".unblock", ".آنبلاک", ".رفع بلاک"}:
            await self.block_target(event, blocked=False)
            return True

        if lower in {".download", ".دانلود"}:
            await self.download_replied_media(event)
            return True

        if lower in {".hide", ".مخفی", ".archive", ".بایگانی"}:
            await self.archive_chat(event, folder_id=1)
            return True

        if lower in {".unhide", ".نمایش", ".unarchive", ".خروج از بایگانی"}:
            await self.archive_chat(event, folder_id=0)
            return True

        match = re.match(r"^\.(?:watermark text|متن لوگو)\s+([\s\S]+)$", text, re.I)
        if match:
            value = match.group(1).strip()
            if not 1 <= len(value) <= 100:
                await self.safe_edit(event, "❌ متن لوگو باید حداکثر ۱۰۰ نویسه باشد.")
            else:
                self.save_settings({"watermark_text": value})
                await self.safe_edit(event, "✅ متن لوگوی تصویر ذخیره شد.")
            return True

        if lower in {".watermark", ".logo", ".لوگو"}:
            await self.watermark_replied_photo(event)
            return True

        match = re.match(
            r"^\.(?:translate|ترجمه)\s+([a-zA-Z-]{2,10})(?:\s+([\s\S]+))?$",
            text,
            re.I,
        )
        if match:
            await self.translate_command(event, match.group(1), match.group(2))
            return True

        match = re.match(
            r"^(?:\.calc|\.ماشین حساب|\.حساب|حساب)\s+(.+)$",
            text,
            re.I,
        )
        if match:
            await self.calculate_command(event, match.group(1))
            return True

        match = re.match(
            r"^\.(?:tts|متن به ویس|ویس)\s+(زن|مرد|female|male)\s+([\s\S]+)$",
            text,
            re.I,
        )
        if match:
            await self.tts_command(event, match.group(1), match.group(2))
            return True

        match = re.match(r"^\.(?:voice save|ویس ذخیره)\s+(.+)$", text, re.I)
        if match:
            await self.save_voice(event, match.group(1))
            return True

        match = re.match(r"^\.(?:voice search|ویس سرچ)\s+(.+)$", text, re.I)
        if match:
            await self.search_voice(event, match.group(1))
            return True

        match = re.match(r"^\.(?:voice del|ویس حذف)\s+(\d+)$", lower)
        if match:
            await self.delete_voice(event, int(match.group(1)))
            return True

        match = re.match(
            r"^\.(?:tts|متن به ویس|ویس)\s+([\s\S]+)$",
            text,
            re.I,
        )
        if match:
            voice = (
                "مرد"
                if self.settings().get("tts_voice", "female") == "male"
                else "زن"
            )
            await self.tts_command(event, voice, match.group(1))
            return True

        match = re.match(r"^\.(?:song|آهنگ)\s+(.+)$", text, re.I)
        if match:
            await self.song_search(event, match.group(1))
            return True

        match = re.match(r"^\.(?:type|تایپ)\s+([\s\S]+)$", text, re.I)
        if match:
            await self.typing_animation(event, match.group(1))
            return True

        match = re.match(r"^\.(?:countdown|شمارش)\s+(\d+)$", lower)
        if match:
            await self.countdown(event, int(match.group(1)))
            return True

        match = re.match(
            r"^\.(?:profile|پروفایل)\s+(add|افزودن)(?:\s+(.+))?$",
            lower,
        )
        if match:
            await self.track_profile(event, match.group(2) or "")
            return True

        match = re.match(
            r"^\.(?:profile|پروفایل)\s+(del|حذف)(?:\s+(.+))?$",
            lower,
        )
        if match:
            await self.untrack_profile(event, match.group(2) or "")
            return True

        match = re.match(
            r"^\.(?:profile|پروفایل)\s+(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"profile_monitor_enabled": state})
            await self.safe_edit(
                event,
                f"✅ پایش پروفایل {'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        if lower in {".profiles", ".پروفایل ها", ".پروفایل‌ها"}:
            await self.show_tracked_profiles(event)
            return True

        match = re.match(
            r"^\.(?:firstcomment|کامنت اول)\s+"
            r"(add|افزودن)\s+([^|]+)\|([^|]+)(?:\|(\d+))?$",
            text,
            re.I | re.S,
        )
        if match:
            try:
                upsert_first_comment_channel(
                    self.data_dir,
                    self.phone,
                    match.group(2).strip(),
                    match.group(3).strip(),
                    delay_seconds=int(match.group(4) or 2),
                )
                await self.safe_edit(event, "✅ کانال کامنت اول ذخیره شد.")
            except ValueError as exc:
                await self.safe_edit(event, f"❌ {exc}")
            return True

        match = re.match(
            r"^\.(?:firstcomment|کامنت اول)\s+(del|حذف)\s+(.+)$",
            text,
            re.I,
        )
        if match:
            deleted = delete_first_comment_channel(
                self.data_dir,
                self.phone,
                match.group(2).strip(),
            )
            await self.safe_edit(
                event,
                "✅ کانال حذف شد." if deleted else "❌ کانال پیدا نشد.",
            )
            return True

        match = re.match(
            r"^\.(?:firstcomment|کامنت اول)\s+"
            r"(on|off|روشن|خاموش)$",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"first_comment_enabled": state})
            await self.safe_edit(
                event,
                f"✅ کامنت اول {'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        if lower in {".firstcomments", ".کامنت اول ها", ".کامنت‌های اول"}:
            await self.show_first_comments(event)
            return True

        match = re.match(r"^\.(?:price|قیمت)\s+(\S+)$", lower)
        if match:
            await self.crypto_price(event, match.group(1))
            return True

        match = re.match(
            r"^\.(?:currency|ارز)\s+([a-zA-Z]{3})(?:\s+([a-zA-Z]{3}))?$",
            text,
            re.I,
        )
        if match:
            await self.currency_rate(
                event,
                match.group(1).upper(),
                (match.group(2) or "IRR").upper(),
            )
            return True

        match = re.match(r"^\.(?:screen|اسکرین)\s+(https?://\S+)$", text, re.I)
        if match:
            await self.web_screenshot(event, match.group(1))
            return True

        return False

    async def show_relations(self, event, relation: str) -> None:
        if relation == "friends":
            ids = list_friends(self.data_dir, self.phone, limit=100)
            title = "دوستان"
        else:
            ids = list_enemies(self.data_dir, self.phone, limit=100)
            title = "دشمنان"
        lines = [f"👥 **{title}:**"]
        lines.extend(f"• [{user_id}](tg://user?id={user_id})" for user_id in ids)
        if not ids:
            lines.append("• لیست خالی است.")
        await self.safe_edit(event, "\n".join(lines))

    async def change_relation(
        self,
        event,
        relation: str,
        enabled: bool,
        raw_target: str,
    ) -> None:
        try:
            user_id, entity = await self.resolve_target(event, raw_target)
        except ValueError as exc:
            await self.safe_edit(event, f"❌ {exc}")
            return
        if user_id == self.owner_id:
            await self.safe_edit(event, "❌ حساب خودتان را نمی‌توان به این لیست افزود.")
            return
        if relation == "friend":
            changed = set_friend(
                self.data_dir,
                self.phone,
                user_id,
                enabled=enabled,
            )
        else:
            changed = set_enemy(
                self.data_dir,
                self.phone,
                user_id,
                enabled=enabled,
            )
        name = (
            " ".join(
                part
                for part in (
                    getattr(entity, "first_name", ""),
                    getattr(entity, "last_name", ""),
                )
                if part
            ).strip()
            or str(user_id)
        )
        verb = "افزوده شد" if enabled else "حذف شد"
        detail = ""
        if relation == "friend" and enabled:
            detail = (
                "\n\n💞 از این پس روی پیام‌های متنی این کاربر با جمله‌های "
                "صمیمی ریپلای می‌شود."
            )
        elif relation == "enemy" and enabled:
            detail = (
                "\n\n💢 این کاربر دشمن ثبت شد. متن‌های پاسخ را از "
                "پنل «دوست و دشمن» اضافه کنید."
            )
        await self.safe_edit(
            event,
            f"{'✅' if changed else 'ℹ️'} {name} {verb}.{detail}",
        )

    async def quote_sticker(self, event) -> None:
        """Create a QuotLy sticker privately and repost it without bot traces."""
        reply = await self.replied_message(event)
        if not reply:
            await self.safe_edit(
                event,
                "❌ دستور «استیکر» را روی پیام موردنظر ریپلای کنید.",
            )
            return

        bot = None
        private_message_ids: list[int] = []
        try:
            bot = await self.client.get_entity("@QuotLyBot")
            quote_message = None
            async with self.client.conversation(
                bot,
                timeout=40,
                exclusive=False,
            ) as conversation:
                forwarded = await self.client.forward_messages(
                    bot,
                    reply,
                    from_peer=event.chat_id,
                )
                if isinstance(forwarded, (list, tuple)):
                    forwarded = forwarded[0]
                private_message_ids.append(int(forwarded.id))

                request = await conversation.send_message(
                    "/q",
                    reply_to=forwarded.id,
                )
                private_message_ids.append(int(request.id))

                for _ in range(5):
                    response = await conversation.get_response(request)
                    if getattr(response, "id", None):
                        private_message_ids.append(int(response.id))
                    if self.is_sticker_message(response):
                        quote_message = response
                        break

            if quote_message is None:
                raise RuntimeError("QuotLy did not return a sticker")

            await self.client.send_file(
                event.chat_id,
                quote_message.media,
                reply_to=reply.id,
            )
            try:
                await event.delete()
            except Exception:
                try:
                    await self.client.delete_messages(
                        event.chat_id,
                        [event.id],
                        revoke=True,
                    )
                except Exception:
                    pass
        except Exception:
            await self.safe_edit(
                event,
                "❌ ساخت استیکر انجام نشد. کمی بعد دوباره تلاش کنید.",
            )
        finally:
            if bot is not None and private_message_ids:
                try:
                    await self.client.delete_messages(
                        bot,
                        list(dict.fromkeys(private_message_ids)),
                        revoke=True,
                    )
                except Exception:
                    pass

    @staticmethod
    def is_sticker_message(message) -> bool:
        if getattr(message, "sticker", None):
            return True
        document = getattr(message, "document", None)
        return bool(
            document
            and any(
                isinstance(attribute, types.DocumentAttributeSticker)
                for attribute in getattr(document, "attributes", ())
            )
        )

    async def manual_reaction(self, event, emoji: str) -> None:
        reply = await self.replied_message(event)
        if not reply:
            await self.safe_edit(event, "❌ برای ری‌اکت روی پیام ریپلای کنید.")
            return
        try:
            await self.client(
                functions.messages.SendReactionRequest(
                    peer=event.chat_id,
                    msg_id=reply.id,
                    reaction=[types.ReactionEmoji(emoticon=emoji[:16])],
                    big=False,
                    add_to_recent=True,
                )
            )
            await event.delete()
        except Exception:
            await self.safe_edit(
                event,
                "❌ این ری‌اکشن در این چت مجاز نیست یا حساب به آن دسترسی ندارد.",
            )

    async def show_locks(self, event) -> None:
        settings = self.settings()
        labels = []
        seen = set()
        for label, key in LOCK_SETTING_KEYS.items():
            if key in seen:
                continue
            seen.add(key)
            labels.append(
                f"• {label}: {'✅' if settings.get(key) == 'on' else '❌'}"
            )
        await self.safe_edit(event, "🔐 **قفل‌های عمومی گروه‌ها**\n\n" + "\n".join(labels))

    async def show_filters(self, event) -> None:
        filters_list = list_word_filters(self.data_dir, self.phone, limit=30)
        lines = ["🧹 **فیلترهای کلمات:**"]
        lines.extend(
            f"• #{item['id']} `{item['phrase']}` → {item['action']}"
            for item in filters_list
        )
        if not filters_list:
            lines.append("• هنوز فیلتری ثبت نشده است.")
        await self.safe_edit(event, "\n".join(lines))

    async def mute_target(self, event, minutes: int) -> None:
        if not event.is_group or not event.is_reply:
            await self.safe_edit(event, "❌ در گروه روی پیام کاربر ریپلای کنید.")
            return
        minutes = max(1, min(int(minutes), 10080))
        reply = await event.get_reply_message()
        until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        try:
            await self.client.edit_permissions(
                event.chat_id,
                reply.sender_id,
                until_date=until,
                send_messages=False,
            )
            await self.safe_edit(event, f"✅ کاربر {minutes} دقیقه سکوت شد.")
        except ChatAdminRequiredError:
            await self.safe_edit(event, "❌ حساب سلف در این گروه دسترسی ادمین ندارد.")

    async def unmute_target(self, event) -> None:
        if not event.is_group or not event.is_reply:
            await self.safe_edit(event, "❌ در گروه روی پیام کاربر ریپلای کنید.")
            return
        reply = await event.get_reply_message()
        try:
            await self.client.edit_permissions(
                event.chat_id,
                reply.sender_id,
                send_messages=True,
            )
            await self.safe_edit(event, "✅ سکوت کاربر برداشته شد.")
        except ChatAdminRequiredError:
            await self.safe_edit(event, "❌ حساب سلف در این گروه دسترسی ادمین ندارد.")

    async def block_target(self, event, *, blocked: bool) -> None:
        if not event.is_reply:
            await self.safe_edit(event, "❌ روی پیام کاربر ریپلای کنید.")
            return
        reply = await event.get_reply_message()
        try:
            if event.is_group:
                await self.client.edit_permissions(
                    event.chat_id,
                    reply.sender_id,
                    view_messages=not blocked,
                )
            else:
                request = (
                    functions.contacts.BlockRequest(id=reply.sender_id)
                    if blocked
                    else functions.contacts.UnblockRequest(id=reply.sender_id)
                )
                await self.client(request)
            await self.safe_edit(
                event,
                "✅ کاربر بلاک شد." if blocked else "✅ بلاک کاربر برداشته شد.",
            )
        except ChatAdminRequiredError:
            await self.safe_edit(event, "❌ حساب سلف دسترسی مدیریتی لازم را ندارد.")

    async def download_replied_media(self, event) -> None:
        reply = await self.replied_message(event)
        if not reply or not reply.media:
            await self.safe_edit(event, "❌ روی عکس، ویدیو، ویس یا فایل ریپلای کنید.")
            return
        settings = self.settings()
        max_mb = max(1, min(int(settings.get("safe_download_max_mb", "50")), 200))
        document = getattr(reply, "document", None)
        size = int(getattr(document, "size", 0) or 0)
        if size and size > max_mb * 1024 * 1024:
            await self.safe_edit(
                event,
                f"❌ حجم فایل بیشتر از سقف {max_mb} مگابایت است.",
            )
            return
        await self.safe_edit(event, "⏳ در حال دریافت رسانه...")
        media_bytes = await self.client.download_media(reply, file=bytes)
        if not media_bytes:
            await self.safe_edit(event, "❌ دریافت رسانه ناموفق بود.")
            return
        if len(media_bytes) > max_mb * 1024 * 1024:
            await self.safe_edit(
                event,
                f"❌ حجم فایل بیشتر از سقف {max_mb} مگابایت است.",
            )
            return
        suffix = self.media_suffix(reply)
        file_object = io.BytesIO(media_bytes)
        file_object.name = f"saved_{reply.id}{suffix}"
        try:
            await self.client.send_file(
                "me",
                file_object,
                caption=(
                    "📥 ذخیره رسانه عادی\n"
                    f"🆔 چت: `{event.chat_id}`\n"
                    f"🆔 پیام: `{reply.id}`"
                ),
                parse_mode="md",
            )
        finally:
            file_object.close()
        await self.safe_edit(event, "✅ رسانه در Saved Messages ذخیره شد.")

    @staticmethod
    def media_suffix(message) -> str:
        file = getattr(message, "file", None)
        ext = str(getattr(file, "ext", "") or "")
        if ext and re.fullmatch(r"\.[A-Za-z0-9]{1,8}", ext):
            return ext
        return ".bin"

    async def archive_chat(self, event, *, folder_id: int) -> None:
        peer = await self.client.get_input_entity(event.chat_id)
        await self.client(
            functions.folders.EditPeerFoldersRequest(
                folder_peers=[
                    types.InputFolderPeer(peer=peer, folder_id=int(folder_id))
                ]
            )
        )
        await self.safe_edit(
            event,
            "✅ چت بایگانی شد." if folder_id == 1 else "✅ چت از بایگانی خارج شد.",
        )

    async def watermark_replied_photo(self, event) -> None:
        if Image is None:
            await self.safe_edit(event, "❌ کتابخانه Pillow روی سرور نصب نیست.")
            return
        reply = await self.replied_message(event)
        if not reply or not reply.photo:
            await self.safe_edit(event, "❌ روی یک عکس ریپلای کنید.")
            return
        watermark = self.settings().get("watermark_text", "").strip()
        if not watermark:
            await self.safe_edit(
                event,
                "❌ ابتدا با `.متن لوگو متن دلخواه` نوشته لوگو را تنظیم کنید.",
            )
            return
        await self.safe_edit(event, "⏳ در حال افزودن لوگو...")
        source = await self.client.download_media(reply, file=bytes)
        output = await asyncio.to_thread(self.render_watermark, source, watermark)
        output.name = f"watermark_{reply.id}.jpg"
        try:
            await self.client.send_file(
                event.chat_id,
                output,
                caption=f"لوگو: {watermark}",
                reply_to=reply.id,
            )
        finally:
            output.close()
        await event.delete()

    @staticmethod
    def render_watermark(source: bytes, watermark: str) -> io.BytesIO:
        image = Image.open(io.BytesIO(source)).convert("RGBA")
        if image.width * image.height > 40_000_000:
            raise ValueError("ابعاد تصویر بیش از حد بزرگ است")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font_size = max(18, min(image.width, image.height) // 24)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        try:
            font = ImageFont.truetype(font_path, font_size)
        except OSError:
            font = ImageFont.load_default()
        margin = max(15, font_size)
        bbox = draw.textbbox((0, 0), watermark, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        x = max(margin, image.width - width - margin)
        y = max(margin, image.height - height - margin)
        padding = max(8, font_size // 3)
        draw.rounded_rectangle(
            (
                x - padding,
                y - padding,
                x + width + padding,
                y + height + padding,
            ),
            radius=padding,
            fill=(0, 0, 0, 125),
        )
        draw.text((x, y), watermark, font=font, fill=(255, 255, 255, 235))
        merged = Image.alpha_composite(image, overlay).convert("RGB")
        output = io.BytesIO()
        merged.save(output, format="JPEG", quality=92, optimize=True)
        output.seek(0)
        return output

    async def translate_command(self, event, language: str, provided: str | None) -> None:
        source = (provided or "").strip()
        if not source:
            reply = await self.replied_message(event)
            source = (reply.raw_text if reply else "").strip()
        if not source:
            await self.safe_edit(event, "❌ متن را بنویسید یا روی پیام ریپلای کنید.")
            return
        if len(source) > 4000:
            await self.safe_edit(event, "❌ متن ترجمه حداکثر ۴۰۰۰ نویسه است.")
            return
        await self.safe_edit(event, "⏳ در حال ترجمه...")
        try:
            translated = await asyncio.to_thread(
                self.google_translate,
                source,
                language.lower(),
            )
        except Exception:
            await self.safe_edit(event, "❌ سرویس ترجمه در دسترس نیست.")
            return
        await self.safe_edit(
            event,
            f"🌐 **ترجمه به {language.lower()}:**\n\n{translated}",
        )

    @staticmethod
    def google_translate(text: str, target: str) -> str:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": target,
                "dt": "t",
                "q": text,
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        translated = "".join(
            str(part[0]) for part in payload[0] if part and part[0]
        ).strip()
        if not translated:
            raise ValueError("empty translation")
        return translated

    async def calculate_command(self, event, expression: str) -> None:
        try:
            result = self.safe_calculate(expression)
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
            await self.safe_edit(event, "❌ عبارت ریاضی معتبر نیست.")
            return
        await self.safe_edit(event, f"🧮 `{expression}`\n\n= **{result:g}**")

    @classmethod
    def safe_calculate(cls, expression: str) -> float:
        normalized = cls.normalize_digits(expression).replace("^", "**")
        if len(normalized) > 200:
            raise ValueError("too long")
        tree = ast.parse(normalized, mode="eval")
        allowed_binary = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod,
            ast.Pow: operator.pow,
        }
        allowed_unary = {ast.UAdd: operator.pos, ast.USub: operator.neg}

        def evaluate(node):
            if isinstance(node, ast.Expression):
                return evaluate(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return float(node.value)
            if isinstance(node, ast.BinOp) and type(node.op) in allowed_binary:
                left = evaluate(node.left)
                right = evaluate(node.right)
                if isinstance(node.op, ast.Pow) and abs(right) > 12:
                    raise OverflowError
                value = allowed_binary[type(node.op)](left, right)
                if not math.isfinite(value) or abs(value) > 1e100:
                    raise OverflowError
                return value
            if isinstance(node, ast.UnaryOp) and type(node.op) in allowed_unary:
                return allowed_unary[type(node.op)](evaluate(node.operand))
            raise ValueError("unsupported expression")

        return float(evaluate(tree))

    async def tts_command(self, event, voice: str, text: str) -> None:
        if edge_tts is None:
            await self.safe_edit(event, "❌ کتابخانه متن‌به‌ویس روی سرور نصب نیست.")
            return
        content = text.strip()
        if not 1 <= len(content) <= 1500:
            await self.safe_edit(event, "❌ متن باید بین ۱ تا ۱۵۰۰ نویسه باشد.")
            return
        male = voice.lower() in {"مرد", "male"}
        voice_name = "fa-IR-FaridNeural" if male else "fa-IR-DilaraNeural"
        await self.safe_edit(event, "⏳ در حال ساخت ویس...")
        try:
            communicate = edge_tts.Communicate(content, voice_name)
            chunks = bytearray()
            async for item in communicate.stream():
                if item.get("type") == "audio":
                    chunks.extend(item.get("data") or b"")
                    if len(chunks) > self.max_in_memory_media_bytes:
                        raise ValueError("خروجی ویس بیش از حد بزرگ شد.")
            if not chunks:
                raise ValueError("خروجی صوتی خالی است.")
            audio = io.BytesIO(bytes(chunks))
            audio.name = "text-to-speech.mp3"
            await self.queued_send_file(
                event.chat_id, audio, voice_note=True,
                reply_to=getattr(event.message, "reply_to_msg_id", None),
                caption="🎙 متن‌به‌ویس", priority=60,
            )
            await event.delete()
        except Exception:
            await self.safe_edit(event, "❌ سرویس متن‌به‌ویس در دسترس نیست.")

    async def save_voice(self, event, keyword: str) -> None:
        reply = await self.replied_message(event)
        if not reply or not (reply.voice or reply.audio):
            await self.safe_edit(event, "❌ روی ویس یا فایل صوتی ریپلای کنید.")
            return
        normalized = keyword.strip().lower()
        if not 1 <= len(normalized) <= 100:
            await self.safe_edit(event, "❌ کلیدواژه باید حداکثر ۱۰۰ نویسه باشد.")
            return
        try:
            reference = await self.save_message_to_cloud(
                reply, caption=f"🎙 بانک ویس: {normalized}"
            )
        except Exception as exc:
            await self.safe_edit(event, f"❌ ذخیره ابری ویس ناموفق بود: {type(exc).__name__}")
            return
        with connect(self.db_path) as connection:
            cursor = connection.execute(
                """INSERT INTO voice_library (keyword, file_path) VALUES (?, ?)""",
                (normalized, reference),
            )
            voice_id = int(cursor.lastrowid)
        await self.safe_edit(event, f"✅ ویس #{voice_id} در Saved Messages ذخیره شد.")

    async def search_voice(self, event, keyword: str) -> None:
        normalized = keyword.strip().lower()
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """SELECT id, keyword, file_path FROM voice_library
                   WHERE lower(keyword) LIKE ? ORDER BY id DESC LIMIT 5""",
                (f"%{normalized}%",),
            ).fetchall()
        sent_any = False
        for row in rows:
            message_id = self._cloud_message_id(row["file_path"])
            if not message_id:
                continue
            message = await self.client.get_messages("me", ids=message_id)
            if not message:
                continue
            await self.client.forward_messages(event.chat_id, message)
            sent_any = True
        if not sent_any:
            await self.safe_edit(event, "❌ ویسی با این کلیدواژه پیدا نشد.")
            return
        await event.delete()

    async def delete_voice(self, event, voice_id: int) -> None:
        with connect(self.db_path) as connection:
            row = connection.execute(
                "SELECT file_path FROM voice_library WHERE id = ?",
                (voice_id,),
            ).fetchone()
            connection.execute("DELETE FROM voice_library WHERE id = ?", (voice_id,))
        if not row:
            await self.safe_edit(event, "❌ ویس پیدا نشد.")
            return
        message_id = self._cloud_message_id(row["file_path"])
        if message_id:
            try:
                await self.client.delete_messages("me", [message_id])
            except Exception:
                pass
        await self.safe_edit(event, "✅ ویس از بانک و Saved Messages حذف شد.")

    async def song_search(self, event, query: str) -> None:
        await self.safe_edit(event, "⏳ در حال جست‌وجوی آهنگ...")
        try:
            payload = await asyncio.to_thread(self.itunes_search, query)
        except Exception:
            await self.safe_edit(event, "❌ سرویس جست‌وجوی آهنگ در دسترس نیست.")
            return
        if not payload:
            await self.safe_edit(event, "❌ نتیجه‌ای پیدا نشد.")
            return
        title = payload.get("trackName") or "بدون عنوان"
        artist = payload.get("artistName") or "نامشخص"
        link = payload.get("trackViewUrl") or ""
        preview = payload.get("previewUrl") or ""
        result_text = f"🎵 **{title}**\n👤 {artist}"
        if link:
            result_text += f"\n🔗 [صفحه آهنگ]({link})"
        if preview:
            try:
                payload, _headers = await asyncio.to_thread(
                    self._fetch_bounded_bytes,
                    preview,
                    max_bytes=self.max_in_memory_media_bytes,
                    timeout=20,
                    required_content_prefix="audio/",
                )
                audio = io.BytesIO(payload)
                audio.name = "preview.m4a"
                try:
                    await self.client.send_file(
                        event.chat_id,
                        audio,
                        caption=result_text,
                    )
                    await event.delete()
                    return
                finally:
                    audio.close()
            except Exception:
                pass
        await self.safe_edit(event, result_text)

    @staticmethod
    def itunes_search(query: str) -> dict[str, Any] | None:
        response = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "entity": "song", "limit": 1},
            timeout=15,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        return results[0] if results else None

    async def typing_animation(self, event, text: str) -> None:
        content = text.strip()
        if not 1 <= len(content) <= 120:
            await self.safe_edit(event, "❌ متن انیمیشن باید حداکثر ۱۲۰ نویسه باشد.")
            return
        output = ""
        for character in content:
            output += character
            await event.edit(output)
            await asyncio.sleep(0.08)

    async def countdown(self, event, count: int) -> None:
        count = max(1, min(int(count), 30))
        for value in range(count, 0, -1):
            await event.edit(f"⏳ **{value}**")
            await asyncio.sleep(1)
        await event.edit("✅ **تمام!**")

    async def track_profile(self, event, raw_target: str) -> None:
        try:
            user_id, entity = await self.resolve_target(event, raw_target)
        except ValueError as exc:
            await self.safe_edit(event, f"❌ {exc}")
            return
        label = " ".join(
            part
            for part in (
                getattr(entity, "first_name", ""),
                getattr(entity, "last_name", ""),
            )
            if part
        ).strip()
        upsert_tracked_profile(
            self.data_dir,
            self.phone,
            user_id,
            label=label,
        )
        await self.safe_edit(
            event,
            f"✅ پایش تغییرات پروفایل `{user_id}` افزوده شد.\n"
            "این قابلیت فقط تغییرات نام، یوزرنیم، بیو و عکس را ثبت می‌کند؛ "
            "بازدیدکننده پروفایل قابل تشخیص نیست.",
        )

    async def untrack_profile(self, event, raw_target: str) -> None:
        try:
            user_id, _ = await self.resolve_target(event, raw_target)
        except ValueError as exc:
            await self.safe_edit(event, f"❌ {exc}")
            return
        deleted = delete_tracked_profile(self.data_dir, self.phone, user_id)
        await self.safe_edit(
            event,
            "✅ پایش حذف شد." if deleted else "❌ کاربر در فهرست پایش نبود.",
        )

    async def show_tracked_profiles(self, event) -> None:
        rows = list_tracked_profiles(self.data_dir, self.phone, limit=50)
        lines = ["👁 **پایش تغییرات پروفایل:**"]
        lines.extend(
            f"• `{row['user_id']}` — {row['label'] or 'بدون نام'}"
            for row in rows
        )
        if not rows:
            lines.append("• فهرست خالی است.")
        await self.safe_edit(event, "\n".join(lines))

    async def show_first_comments(self, event) -> None:
        rows = list_first_comment_channels(self.data_dir, self.phone, limit=50)
        lines = ["💬 **کانال‌های کامنت اول:**"]
        lines.extend(
            f"• `{row['chat_id']}` — {row['delay_seconds']} ثانیه — "
            f"{str(row['comment_text'])[:50]}"
            for row in rows
        )
        if not rows:
            lines.append("• کانالی ثبت نشده است.")
        await self.safe_edit(event, "\n".join(lines))

    async def crypto_price(self, event, symbol: str) -> None:
        coin_id = CRYPTO_IDS.get(symbol.lower())
        if not coin_id:
            await self.safe_edit(
                event,
                "❌ نماد پشتیبانی‌شده: BTC، ETH، TON، SOL، DOGE و USDT",
            )
            return
        await self.safe_edit(event, "⏳ در حال دریافت قیمت...")
        try:
            data = await asyncio.to_thread(self.fetch_crypto_price, coin_id)
        except Exception:
            await self.safe_edit(event, "❌ منبع قیمت در دسترس نیست.")
            return
        usd = float(data.get("usd", 0))
        change = float(data.get("usd_24h_change", 0))
        await self.safe_edit(
            event,
            f"💹 **{symbol.upper()}**\n"
            f"قیمت جهانی: `${usd:,.6g}`\n"
            f"تغییر ۲۴ ساعت: `{change:+.2f}%`\n"
            f"زمان: {datetime.now().strftime('%H:%M:%S')}",
        )

    @staticmethod
    def fetch_crypto_price(coin_id: str) -> dict[str, Any]:
        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json()[coin_id]

    async def currency_rate(self, event, base: str, target: str) -> None:
        await self.safe_edit(event, "⏳ در حال دریافت نرخ ارز...")
        try:
            rate, updated = await asyncio.to_thread(
                self.fetch_currency_rate,
                base,
                target,
            )
        except Exception:
            await self.safe_edit(event, "❌ نرخ این جفت‌ارز در دسترس نیست.")
            return
        note = (
            "\n⚠️ نرخ رسمی جهانی است و با نرخ آزاد بازار ایران تفاوت دارد."
            if target == "IRR" or base == "IRR"
            else ""
        )
        await self.safe_edit(
            event,
            f"💱 **{base}/{target}**\n"
            f"`1 {base} = {rate:,.6g} {target}`\n"
            f"بروزرسانی منبع: {updated}{note}",
        )

    @staticmethod
    def fetch_currency_rate(base: str, target: str) -> tuple[float, str]:
        response = requests.get(
            f"https://open.er-api.com/v6/latest/{base}",
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        rate = float(payload["rates"][target])
        return rate, str(payload.get("time_last_update_utc", "نامشخص"))

    async def web_screenshot(self, event, url: str) -> None:
        await self.safe_edit(event, "⏳ در حال تهیه تصویر صفحه وب...")
        screenshot_url = (
            "https://image.thum.io/get/width/1280/crop/900/noanimate/"
            f"{quote(url, safe=':/?=&%')}"
        )
        try:
            payload, _headers = await asyncio.to_thread(
                self._fetch_bounded_bytes,
                screenshot_url,
                max_bytes=self.max_in_memory_media_bytes,
                timeout=35,
                required_content_prefix="image/",
            )
            image = io.BytesIO(payload)
            image.name = "webpage.jpg"
            try:
                await self.client.send_file(
                    event.chat_id,
                    image,
                    caption=f"🖥 تصویر صفحه\n{url}",
                )
            finally:
                image.close()
            await event.delete()
        except Exception:
            await self.safe_edit(event, "❌ سرویس تصویر صفحه وب در دسترس نیست.")

    async def apply_outgoing_style(self, event) -> None:
        text = (event.raw_text or "").strip()
        if (
            not text
            or self.looks_like_command(text)
            or event.via_bot_id
        ):
            return
        settings = self.settings()
        style = settings.get("outgoing_text_style", "none")
        signature_enabled = settings.get("outgoing_signature_enabled") == "on"
        signature = settings.get("outgoing_signature_text", "").strip()
        if style == "none" and not (signature_enabled and signature):
            return
        content = text
        if signature_enabled and signature and signature not in content:
            content = f"{content}\n\n{signature}"
        escaped = html.escape(content)
        wrappers = {
            "bold": ("<b>", "</b>"),
            "italic": ("<i>", "</i>"),
            "code": ("<code>", "</code>"),
            "strike": ("<s>", "</s>"),
            "underline": ("<u>", "</u>"),
            "spoiler": ("<spoiler>", "</spoiler>"),
        }
        if style in wrappers:
            start, end = wrappers[style]
            escaped = f"{start}{escaped}{end}"
        await event.edit(escaped, parse_mode="html")

    @staticmethod
    def timed_media_ttl(message) -> int | None:
        media = getattr(message, "media", None)
        try:
            ttl_seconds = int(getattr(media, "ttl_seconds", 0) or 0)
        except (TypeError, ValueError):
            return None
        return ttl_seconds if ttl_seconds > 0 else None

    @staticmethod
    def anti_delete_media_type(message) -> str:
        if getattr(message, "photo", None):
            return "عکس"
        if getattr(message, "video_note", None):
            return "ویدیو مسیج"
        if getattr(message, "video", None):
            return "ویدیو"
        if getattr(message, "gif", None):
            return "گیف"
        if getattr(message, "voice", None):
            return "ویس"
        if getattr(message, "audio", None):
            return "فایل صوتی"
        if getattr(message, "sticker", None):
            return "استیکر"
        if getattr(message, "document", None):
            return "فایل"
        if getattr(message, "poll", None):
            return "نظرسنجی"
        if getattr(message, "contact", None):
            return "مخاطب"
        if getattr(message, "venue", None):
            return "مکان"
        if getattr(message, "geo", None):
            return "موقعیت مکانی"
        if getattr(message, "web_preview", None):
            return "پیش‌نمایش لینک"
        if getattr(message, "media", None):
            return "رسانه"
        return "متن"

    @staticmethod
    def anti_delete_media_description(message) -> str:
        poll = getattr(message, "poll", None)
        if poll:
            poll_data = getattr(poll, "poll", poll)
            question = getattr(poll_data, "question", "")
            question_text = str(getattr(question, "text", question) or "")
            answers = []
            for answer in getattr(poll_data, "answers", None) or []:
                answer_text = getattr(answer, "text", "")
                answers.append(
                    str(getattr(answer_text, "text", answer_text) or "")
                )
            result = f"سؤال نظرسنجی: {question_text}".strip()
            if answers:
                result += "\nگزینه‌ها: " + " | ".join(answers)
            return result

        contact = getattr(message, "contact", None)
        if contact:
            name = " ".join(
                part
                for part in (
                    str(getattr(contact, "first_name", "") or "").strip(),
                    str(getattr(contact, "last_name", "") or "").strip(),
                )
                if part
            )
            phone_number = str(
                getattr(contact, "phone_number", "") or ""
            ).strip()
            return f"مخاطب: {name or 'بدون نام'} — {phone_number or 'بدون شماره'}"

        venue = getattr(message, "venue", None)
        if venue:
            title = str(getattr(venue, "title", "") or "").strip()
            address = str(getattr(venue, "address", "") or "").strip()
            return f"مکان: {title or 'بدون عنوان'} — {address or 'بدون نشانی'}"

        geo = getattr(message, "geo", None)
        if geo:
            lat = getattr(geo, "lat", None)
            long = getattr(geo, "long", None)
            if lat is not None and long is not None:
                return f"موقعیت: {lat}, {long}"

        web_preview = getattr(message, "web_preview", None)
        if web_preview:
            title = str(getattr(web_preview, "title", "") or "").strip()
            url = str(getattr(web_preview, "url", "") or "").strip()
            return f"پیش‌نمایش: {title or 'بدون عنوان'} — {url}".strip()
        return ""

    @staticmethod
    def sensitive_account_message(event) -> bool:
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        if sender_id == 777000:
            return True
        text = (getattr(event, "raw_text", "") or "").lower()
        sensitive_phrases = (
            "login code",
            "verification code",
            "code to log in",
            "کد ورود",
            "کد تأیید",
            "کد تایید",
            "رمز ورود",
            "two-step verification",
        )
        return any(phrase in text for phrase in sensitive_phrases)

    @staticmethod
    def anti_delete_file_suffix(message) -> str:
        file_info = getattr(message, "file", None)
        candidates = (
            str(getattr(file_info, "ext", "") or ""),
            Path(str(getattr(file_info, "name", "") or "")).suffix,
        )
        for candidate in candidates:
            normalized = candidate.lower()
            if re.fullmatch(r"\.[a-z0-9]{1,10}", normalized):
                return normalized
        if getattr(message, "photo", None):
            return ".jpg"
        return ".bin"

    async def cache_incoming_for_anti_delete(self, event) -> None:
        settings = self.settings()
        if settings.get("anti_delete_enabled") != "on":
            return
        if int(getattr(event, "sender_id", 0) or 0) == self.owner_id:
            return
        if self.sensitive_account_message(event):
            return
        message = event.message
        if isinstance(message, types.MessageService):
            return
        # Self-destructing media is deliberately kept outside the ordinary
        # anti-delete cache.  The legacy timed-photo feature remains isolated
        # in self_bot.py.
        if self.timed_media_ttl(message) is not None:
            return
        if event.is_private:
            if settings.get("anti_delete_private", "on") != "on":
                return
        elif event.is_group:
            if settings.get("anti_delete_groups", "on") != "on":
                return
        elif event.is_channel:
            if settings.get("anti_delete_channels", "off") != "on":
                return
        else:
            return

        chat_id = int(getattr(event, "chat_id", 0) or 0)
        message_id = int(getattr(event, "id", 0) or 0)
        if not chat_id or not message_id:
            return
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        sender_name = str(sender_id or "نامشخص")
        chat_title = str(chat_id)
        try:
            sender = await event.get_sender()
            display_name = utils.get_display_name(sender).strip()
            username = str(getattr(sender, "username", "") or "").strip()
            sender_name = display_name or (
                f"@{username}" if username else sender_name
            )
        except Exception:
            pass
        try:
            chat = await event.get_chat()
            chat_title = (
                utils.get_display_name(chat).strip()
                or str(getattr(chat, "title", "") or "").strip()
                or chat_title
            )
        except Exception:
            pass

        media_type = self.anti_delete_media_type(message)
        message_text = (getattr(event, "raw_text", "") or "").strip()
        media_description = self.anti_delete_media_description(message)
        if media_description and media_description not in message_text:
            message_text = (
                f"{message_text}\n\n{media_description}".strip()
            )
        file_info = getattr(message, "file", None)
        media_name = str(getattr(file_info, "name", "") or "")
        media_size = int(getattr(file_info, "size", 0) or 0)
        cloud_reference = ""
        try:
            max_mb = max(1, min(int(settings.get("anti_delete_max_mb", "50")), 100))
        except (TypeError, ValueError):
            max_mb = 50
        if not media_size or media_size <= max_mb * 1024 * 1024:
            try:
                cloud_reference = await self.save_message_to_cloud(
                    message,
                    caption=(
                        f"🛡 آرشیو ضدحذف\n👤 {sender_name}\n💬 {chat_title}"
                    ),
                )
            except Exception as exc:
                print(
                    f"خطا در ذخیره ابری ضدحذف برای {self.phone}: "
                    f"{type(exc).__name__}"
                )
        archive_message(
            self.data_dir,
            self.phone,
            chat_id=chat_id,
            message_id=message_id,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_title=chat_title,
            message_text=message_text,
            media_type=media_type,
            media_path=cloud_reference,
            media_name=media_name,
            media_size=media_size,
        )

    def mark_own_deletion(self, event) -> None:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        message_id = int(getattr(event, "id", 0) or 0)
        if chat_id and message_id:
            self.ignored_deletions[(chat_id, message_id)] = time.monotonic()

    def consume_ignored_deletion(self, chat_id: int | None, message_id: int) -> bool:
        now = time.monotonic()
        self.ignored_deletions = {
            key: created
            for key, created in self.ignored_deletions.items()
            if now - created < 120
        }
        if chat_id is not None:
            key = (int(chat_id), int(message_id))
            if key in self.ignored_deletions:
                self.ignored_deletions.pop(key, None)
                return True
        for key in tuple(self.ignored_deletions):
            if key[1] == int(message_id):
                self.ignored_deletions.pop(key, None)
                return True
        return False

    async def handle_deleted_messages(self, event) -> None:
        settings = self.settings()
        if settings.get("anti_delete_enabled") != "on":
            return
        raw_chat_id = getattr(event, "chat_id", None)
        chat_id = int(raw_chat_id) if raw_chat_id is not None else None
        deleted_ids = [
            int(message_id)
            for message_id in (getattr(event, "deleted_ids", None) or [])
        ]
        for message_id in deleted_ids:
            if self.consume_ignored_deletion(chat_id, message_id):
                if chat_id is not None:
                    remove_archived_message(
                        self.data_dir,
                        self.phone,
                        chat_id=chat_id,
                        message_id=message_id,
                    )
                continue
            rows = get_archived_messages(
                self.data_dir,
                self.phone,
                message_id=message_id,
                chat_id=chat_id,
            )
            for row in rows:
                if await self.send_archived_message(row):
                    remove_archived_message(
                        self.data_dir,
                        self.phone,
                        chat_id=int(row["chat_id"]),
                        message_id=int(row["message_id"]),
                    )

    async def send_archived_message(self, row: dict[str, Any]) -> bool:
        media_type = str(row.get("media_type") or "متن")
        sender_name = str(row.get("sender_name") or "نامشخص")
        sender_id = int(row.get("sender_id") or 0)
        chat_title = str(row.get("chat_title") or row.get("chat_id") or "نامشخص")
        message_text = str(row.get("message_text") or "").strip()
        created_at = str(row.get("created_at") or "نامشخص")
        cloud_id = self._cloud_message_id(str(row.get("media_path") or ""))
        body = (
            "🗑 پیام حذف‌شده شناسایی شد\n"
            f"👤 فرستنده: {sender_name}\n"
            f"🆔 شناسه: {sender_id or 'نامشخص'}\n"
            f"💬 چت: {chat_title}\n"
            f"📦 نوع: {media_type}\n"
            f"🕒 دریافت: {created_at}"
        )
        if cloud_id:
            body += f"\n☁️ نسخه کامل قبلاً در Saved Messages ذخیره شد (پیام {cloud_id})."
        elif media_type != "متن":
            body += "\n⚠️ نسخه رسانه به‌علت محدودیت حجم ذخیره نشد."
        if message_text:
            body += f"\n\n📝 متن:\n{message_text}"
        await self.queued_send_message(
            "me", body[:4000], priority=70, parse_mode=None, silent=True
        )
        return True

    async def anti_delete_cleanup_loop(self) -> None:
        while self.account.is_running and not self.account.shutdown_requested:
            try:
                settings = self.settings()
                days = max(
                    1,
                    min(
                        int(settings.get("anti_delete_retention_days", "7")),
                        30,
                    ),
                )
                purge_expired_archives(
                    self.data_dir,
                    self.phone,
                    retention_days=days,
                )
            except Exception as exc:
                print(
                    f"خطا در پاکسازی ضدحذف برای {self.phone}: "
                    f"{type(exc).__name__}"
                )
            await asyncio.sleep(21600)

    async def handle_incoming(self, event) -> bool:
        await self.cache_incoming_for_anti_delete(event)
        if event.sender_id == self.owner_id:
            return True
        if await self.handle_tic_tac_toe_incoming(event):
            return True
        settings = self.settings()

        if (
            event.is_private
            and settings.get("force_join_private") == "on"
            and not await self.force_join_allows(event)
        ):
            return False

        if (
            event.is_private
            and settings.get("auto_read_private") == "on"
        ) or (
            event.is_group
            and settings.get("auto_read_groups") == "on"
        ):
            try:
                await self.client.send_read_acknowledge(
                    event.chat_id,
                    max_id=event.id,
                )
            except Exception:
                pass

        sender_id = int(event.sender_id or 0)
        needs_friends = (
            settings.get("relationship_reaction") == "on"
            or settings.get("friend_affection_reply", "on") == "on"
        )
        friends = (
            set(list_friends(self.data_dir, self.phone, limit=500))
            if needs_friends
            else set()
        )
        reaction = None
        enemies: set[int] = set()
        if settings.get("relationship_reaction") == "on":
            enemies = {int(item) for item in settings.get("enemy", [])}
            if sender_id in friends:
                reaction = settings.get("friend_reaction_emoji", "❤️")
            elif sender_id in enemies:
                reaction = settings.get("enemy_reaction_emoji", "👎")
        elif settings.get("enemy_hostile_reply", "on") == "on":
            enemies = {int(item) for item in settings.get("enemy", [])}
        if reaction is None and settings.get("auto_reaction") == "on":
            reaction = settings.get("auto_reaction_emoji", "❤️")
        if reaction:
            try:
                await self.client(
                    functions.messages.SendReactionRequest(
                        peer=event.chat_id,
                        msg_id=event.id,
                        reaction=[types.ReactionEmoji(emoticon=reaction[:16])],
                        big=False,
                        add_to_recent=False,
                    )
                )
            except Exception:
                pass

        if event.is_group:
            await self.moderate_group_message(event, settings)
        if (
            sender_id in friends
            and settings.get("friend_affection_reply", "on") == "on"
        ):
            await self.reply_affectionately_to_friend(event)
        elif (
            sender_id in enemies
            and settings.get("enemy_hostile_reply", "on") == "on"
        ):
            await self.reply_to_enemy(event)
        return True

    def friend_affection_was_sent(self, event) -> bool:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        message_id = int(getattr(event, "id", 0) or 0)
        return (
            chat_id != 0
            and message_id != 0
            and self.friend_affection_last_message.get(chat_id) == message_id
        )

    def enemy_hostile_was_sent(self, event) -> bool:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        message_id = int(getattr(event, "id", 0) or 0)
        return (
            chat_id != 0
            and message_id != 0
            and self.enemy_hostile_last_message.get(chat_id) == message_id
        )

    async def reply_affectionately_to_friend(self, event) -> bool:
        """به هر پیام متنی دوست یک پاسخ صمیمی و غیرتکراری بده."""
        if not (event.is_private or event.is_group):
            return False
        message_text = (event.raw_text or "").strip()
        if not message_text:
            return False
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        message_id = int(getattr(event, "id", 0) or 0)
        if not chat_id or not message_id:
            return False
        if self.friend_affection_last_message.get(chat_id) == message_id:
            return False
        try:
            sender = await event.get_sender()
            if getattr(sender, "bot", False):
                return False
            custom_replies = [
                str(item["response"])
                for item in list_friend_affection_replies(
                    self.data_dir,
                    self.phone,
                    limit=100,
                )
                if str(item.get("response") or "").strip()
            ]
            reply_pool = custom_replies or FRIEND_AFFECTION_REPLIES
            queued_sender = getattr(
                self.account,
                "queued_send_message",
                None,
            )
            if queued_sender is None:
                await event.reply(random.choice(reply_pool))
            else:
                await queued_sender(
                    event.chat_id,
                    random.choice(reply_pool),
                    reply_to=message_id,
                    priority=45,
                )
        except Exception:
            return False
        self.friend_affection_last_message[chat_id] = message_id
        return True

    async def reply_to_enemy(self, event) -> bool:
        """Reply to an enemy only from the self-admin's configured text pool."""
        if not (event.is_private or event.is_group):
            return False
        message_text = (event.raw_text or "").strip()
        if not message_text:
            return False
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        message_id = int(getattr(event, "id", 0) or 0)
        if not chat_id or not message_id:
            return False
        if self.enemy_hostile_last_message.get(chat_id) == message_id:
            return False
        try:
            sender = await event.get_sender()
            if getattr(sender, "bot", False):
                return False
            custom_replies = [
                str(item["response"])
                for item in list_enemy_hostile_replies(
                    self.data_dir,
                    self.phone,
                    limit=100,
                )
                if str(item.get("response") or "").strip()
            ]
            if not custom_replies:
                return False
            queued_sender = getattr(
                self.account,
                "queued_send_message",
                None,
            )
            if queued_sender is None:
                await event.reply(random.choice(custom_replies))
            else:
                await queued_sender(
                    event.chat_id,
                    random.choice(custom_replies),
                    reply_to=message_id,
                    priority=45,
                )
        except Exception:
            return False
        self.enemy_hostile_last_message[chat_id] = message_id
        return True

    async def force_join_allows(self, event) -> bool:
        config = get_force_join_config(self.users_db)
        if not config.get("enabled") or not config.get("configured"):
            return True
        channel = config.get("chat_id") or (
            f"@{config['username']}" if config.get("username") else ""
        )
        if not channel:
            return True
        try:
            await self.client(
                functions.channels.GetParticipantRequest(
                    channel=channel,
                    participant=event.sender_id,
                )
            )
            return True
        except UserNotParticipantError:
            pass
        except Exception:
            return True
        now = time.monotonic()
        sender_id = int(event.sender_id)
        last = self.force_join_notified_at.get(sender_id, 0)
        if now - last >= 3600:
            join_url = config.get("join_url", "")
            title = config.get("title", "کانال")
            await self.queued_send_message(
                event.chat_id,
                "🔒 برای دریافت پاسخ ابتدا عضو کانال شوید.\n"
                f"📣 {title}\n{join_url}",
                reply_to=int(event.id),
                priority=40,
            )
            self.force_join_notified_at[sender_id] = now
        return False

    async def moderate_group_message(
        self,
        event,
        settings: dict[str, Any],
    ) -> None:
        if await self.sender_is_admin(event):
            return
        message = event.message
        raw_text = (event.raw_text or "").lower()
        reason = None
        if settings.get("lock_links") == "on" and self.contains_link(message, raw_text):
            reason = "ارسال لینک"
        elif settings.get("lock_forwards") == "on" and message.fwd_from:
            reason = "فوروارد"
        elif settings.get("lock_photos") == "on" and message.photo:
            reason = "عکس"
        elif settings.get("lock_videos") == "on" and message.video:
            reason = "ویدیو"
        elif settings.get("lock_gifs") == "on" and message.gif:
            reason = "گیف"
        elif settings.get("lock_stickers") == "on" and message.sticker:
            reason = "استیکر"
        elif settings.get("lock_voice") == "on" and message.voice:
            reason = "ویس"
        elif settings.get("lock_polls") == "on" and message.poll:
            reason = "نظرسنجی"
        elif (
            settings.get("lock_files") == "on"
            and message.document
            and not (message.video or message.gif or message.sticker or message.voice)
        ):
            reason = "فایل"

        if reason:
            await self.delete_moderated_message(event, reason)
            return

        if settings.get("word_filter_enabled") != "on" or not raw_text:
            return
        now = time.monotonic()
        if now - self.filter_cache[0] >= 15:
            self.filter_cache = (
                now,
                list_word_filters(self.data_dir, self.phone, limit=200),
            )
        for item in self.filter_cache[1]:
            phrase = str(item["phrase"]).lower()
            if phrase and phrase in raw_text:
                await self.apply_filter_action(event, item)
                return

    async def sender_is_admin(self, event) -> bool:
        try:
            permissions = await self.client.get_permissions(
                event.chat_id,
                event.sender_id,
            )
            return bool(permissions.is_admin or permissions.is_creator)
        except Exception:
            return False

    @staticmethod
    def contains_link(message, raw_text: str) -> bool:
        if re.search(r"(?:https?://|www\.|t\.me/|telegram\.me/)", raw_text):
            return True
        entities = getattr(message, "entities", None) or []
        return any(
            isinstance(
                entity,
                (types.MessageEntityUrl, types.MessageEntityTextUrl),
            )
            for entity in entities
        )

    async def delete_moderated_message(self, event, reason: str) -> None:
        try:
            self.mark_own_deletion(event)
            await event.delete()
        except ChatAdminRequiredError:
            return
        try:
            notice = await self.queued_send_message(
                event.chat_id,
                f"⚠️ {reason} در این گروه قفل است.",
                priority=50,
            )
            await asyncio.sleep(4)
            await notice.delete()
        except Exception:
            pass

    async def apply_filter_action(self, event, item: dict[str, Any]) -> None:
        action = str(item.get("action") or "delete")
        try:
            self.mark_own_deletion(event)
            await event.delete()
        except ChatAdminRequiredError:
            return
        if action == "delete":
            return
        if action == "warn":
            await self.queued_send_message(
                event.chat_id,
                f"⚠️ [کاربر](tg://user?id={event.sender_id})، "
                "این عبارت در گروه مجاز نیست.",
                parse_mode="md",
                priority=50,
            )
            return
        if action == "mute":
            minutes = max(
                1,
                min(
                    int(self.settings().get("word_filter_mute_minutes", "10")),
                    10080,
                ),
            )
            try:
                await self.client.edit_permissions(
                    event.chat_id,
                    event.sender_id,
                    until_date=datetime.now(timezone.utc)
                    + timedelta(minutes=minutes),
                    send_messages=False,
                )
            except Exception:
                pass
            return
        if action == "block":
            try:
                await self.client.edit_permissions(
                    event.chat_id,
                    event.sender_id,
                    view_messages=False,
                )
            except Exception:
                pass

    async def handle_first_comment(self, event) -> None:
        if not event.is_channel or event.is_group:
            return
        settings = self.settings()
        if settings.get("first_comment_enabled") != "on":
            return
        chat_id = int(event.chat_id)
        job_key = (chat_id, int(event.id))
        if job_key in self.comment_jobs:
            return
        rows = list_first_comment_channels(self.data_dir, self.phone, limit=100)
        chat = await event.get_chat()
        username = str(getattr(chat, "username", "") or "").lower()
        matched = None
        for row in rows:
            configured = str(row["chat_id"]).strip()
            normalized = configured.lower().lstrip("@")
            if configured == str(chat_id) or (username and normalized == username):
                matched = row
                break
        if not matched:
            return
        self.comment_jobs.add(job_key)
        try:
            await asyncio.sleep(max(0, min(int(matched["delay_seconds"]), 300)))
            discussion = await self.client(
                functions.messages.GetDiscussionMessageRequest(
                    peer=event.chat_id,
                    msg_id=event.id,
                )
            )
            discussion_message = None
            for message in discussion.messages:
                peer_id = utils.get_peer_id(message.peer_id)
                if peer_id != chat_id:
                    discussion_message = message
                    break
            if discussion_message is None:
                return
            await self.queued_send_message(
                discussion_message.peer_id,
                str(matched["comment_text"]),
                reply_to=discussion_message.id,
                priority=60,
            )
        except Exception:
            return
        finally:
            self.comment_jobs.discard(job_key)

    async def profile_monitor_loop(self) -> None:
        while self.account.is_running and not self.account.shutdown_requested:
            try:
                settings = self.settings()
                if settings.get("profile_monitor_enabled") != "on":
                    await asyncio.sleep(60)
                    continue
                interval = max(
                    5,
                    min(
                        int(settings.get("profile_monitor_interval_minutes", "10")),
                        1440,
                    ),
                )
                rows = self.profile_rows_due(interval)
                for row in rows[:30]:
                    await self.check_tracked_profile(row)
                    await asyncio.sleep(1)
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(60)

    def profile_rows_due(self, interval_minutes: int) -> list[sqlite3.Row]:
        threshold = datetime.now() - timedelta(minutes=interval_minutes)
        with connect(self.db_path) as connection:
            rows = connection.execute(
                """SELECT user_id, label, snapshot_json, last_checked_at
                   FROM tracked_profiles WHERE is_active = 1
                   ORDER BY COALESCE(last_checked_at, '') ASC"""
            ).fetchall()
        due = []
        for row in rows:
            raw = row["last_checked_at"]
            if not raw:
                due.append(row)
                continue
            try:
                checked = datetime.fromisoformat(str(raw))
            except ValueError:
                due.append(row)
                continue
            if checked <= threshold:
                due.append(row)
        return due

    async def check_tracked_profile(self, row: sqlite3.Row) -> None:
        user_id = int(row["user_id"])
        try:
            full = await self.client(functions.users.GetFullUserRequest(user_id))
            user = full.users[0]
            snapshot = {
                "first_name": user.first_name or "",
                "last_name": user.last_name or "",
                "username": user.username or "",
                "bio": full.full_user.about or "",
                "photo_id": str(getattr(user.photo, "photo_id", "") or ""),
            }
        except Exception:
            with connect(self.db_path) as connection:
                connection.execute(
                    """UPDATE tracked_profiles
                       SET last_checked_at = ? WHERE user_id = ?""",
                    (datetime.now().isoformat(timespec="seconds"), user_id),
                )
            return
        try:
            old = json.loads(str(row["snapshot_json"] or "{}"))
        except json.JSONDecodeError:
            old = {}
        changes = []
        labels = {
            "first_name": "نام",
            "last_name": "نام خانوادگی",
            "username": "یوزرنیم",
            "bio": "بیو",
            "photo_id": "عکس پروفایل",
        }
        if old:
            for key, label in labels.items():
                if old.get(key, "") != snapshot.get(key, ""):
                    if key == "photo_id":
                        changes.append(f"• {label} تغییر کرد.")
                    else:
                        changes.append(
                            f"• {label}: `{old.get(key, '') or '—'}` → "
                            f"`{snapshot.get(key, '') or '—'}`"
                        )
        with connect(self.db_path) as connection:
            connection.execute(
                """UPDATE tracked_profiles
                   SET snapshot_json = ?, last_checked_at = ?
                   WHERE user_id = ?""",
                (
                    json.dumps(snapshot, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                    user_id,
                ),
            )
        if changes:
            label = row["label"] or str(user_id)
            await self.queued_send_message(
                "me",
                "👁 **تغییر پروفایل ثبت شد**\n"
                f"کاربر: [{label}](tg://user?id={user_id})\n\n"
                + "\n".join(changes),
                parse_mode="md",
                priority=70,
            )

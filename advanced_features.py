"""Advanced, opt-in account tools for the Telegram self-bot.

The module is intentionally isolated from betting and wallet code.  Every
setting is stored in the historical per-account SQLite database so upgrading
an existing installation does not replace sessions, balances, or old options.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import os
import re
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from telethon import events, functions, types, utils
from telethon.errors import ChatAdminRequiredError, FloodWaitError

from control_store import (
    claim_due_schedule_jobs,
    create_profile_backup,
    create_scheduled_once,
    get_chatgpt_daily_usage,
    get_helper_config,
    get_latest_profile_backup,
    get_message_version,
    get_runtime_metrics,
    list_due_scheduled_once,
    list_private_allowlist,
    list_scheduled_once,
    mark_private_user_blocked,
    private_user_is_allowed,
    record_message_edit,
    register_private_lock_attempt,
    remember_message_version,
    set_private_allowlist_user,
    set_runtime_metric,
    finish_schedule_job_run,
    update_scheduled_once_status,
)

try:
    import qrcode
except ImportError:  # pragma: no cover - reported at runtime
    qrcode = None

try:
    from PIL import Image, ImageDraw
    Image.MAX_IMAGE_PIXELS = 40_000_000
except ImportError:  # pragma: no cover - reported at runtime
    Image = ImageDraw = None


PERSIAN_DIGITS = str.maketrans(
    "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
    "01234567890123456789",
)

ACTION_ALIASES = {
    "تایپ": "typing",
    "typing": "typing",
    "ویس": "record-audio",
    "voice": "record-audio",
    "ویدیو": "record-video",
    "ویدئو": "record-video",
    "video": "record-video",
    "عکس": "photo",
    "photo": "photo",
    "فایل": "document",
    "file": "document",
    "استیکر": "sticker",
    "sticker": "sticker",
    "بازی": "game",
    "game": "game",
}


class AdvancedFeatureEngine:
    """Private lock, anti-edit, profile, group, and utility feature pack."""

    def __init__(self, feature_engine: Any):
        self.feature_engine = feature_engine
        self.account = feature_engine.account
        self.client = feature_engine.client
        self.phone = feature_engine.phone
        self.owner_id = feature_engine.owner_id
        self.data_dir = feature_engine.data_dir
        self.users_db = feature_engine.users_db
        self.max_in_memory_media_bytes = max(
            1, min(int(os.getenv("MAX_IN_MEMORY_MEDIA_MB", "50") or 50), 100)
        ) * 1024 * 1024
        self.background_tasks: list[asyncio.Task] = []
        self.last_analog_clock_update = 0.0
        self.last_analog_clock_enabled = False

    def settings(self) -> dict[str, str]:
        return self.feature_engine.settings()

    def save_settings(self, values: dict[str, Any]) -> None:
        self.feature_engine.save_settings(values)

    async def safe_edit(self, event, text: str, **kwargs) -> None:
        await self.feature_engine.safe_edit(event, text, **kwargs)

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

    def start_background_tasks(self) -> list[asyncio.Task]:
        if any(not task.done() for task in self.background_tasks):
            return self.background_tasks
        self.background_tasks = [
            asyncio.create_task(
                self.scheduled_once_loop(), name=f"scheduled-once:{self.phone}"
            ),
            asyncio.create_task(
                self.professional_schedule_loop(), name=f"schedule-jobs:{self.phone}"
            ),
            asyncio.create_task(
                self.analog_clock_loop(), name=f"analog-clock:{self.phone}"
            ),
        ]
        return self.background_tasks

    async def stop_background_tasks(self) -> None:
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
    def _saved_message_id(reference: str) -> int:
        value = str(reference or "")
        if not value.startswith("tg:"):
            return 0
        try:
            return int(value.split(":", 1)[1])
        except (TypeError, ValueError):
            return 0

    async def _buffer_from_media_reference(
        self, reference: str, *, default_name: str = "media.bin"
    ) -> io.BytesIO:
        ref = str(reference or "").strip()
        payload = b""
        filename = default_name
        saved_id = self._saved_message_id(ref)
        if saved_id:
            message = await self.client.get_messages("me", ids=saved_id)
            if not message:
                raise FileNotFoundError("رسانه Saved Messages پیدا نشد.")
            file_info = getattr(message, "file", None)
            declared = int(getattr(file_info, "size", 0) or 0)
            if declared and declared > self.max_in_memory_media_bytes:
                raise ValueError("حجم رسانه بیشتر از سقف حافظه است.")
            payload = await self.client.download_media(message, file=bytes)
            filename = str(getattr(file_info, "name", "") or filename)
        elif ref.startswith("botfile:"):
            file_id = ref.split(":", 1)[1]
            helper_token = str(get_helper_config(self.users_db).get("token") or "")
            if not helper_token:
                raise RuntimeError("توکن هلپر برای دریافت رسانه ثبت نشده است.")
            def fetch_bot_file() -> tuple[bytes, str]:
                metadata = requests.get(
                    f"https://api.telegram.org/bot{helper_token}/getFile",
                    params={"file_id": file_id}, timeout=20,
                )
                metadata.raise_for_status()
                result = metadata.json().get("result") or {}
                file_path = str(result.get("file_path") or "")
                size = int(result.get("file_size") or 0)
                if not file_path:
                    raise FileNotFoundError("مسیر فایل هلپر پیدا نشد.")
                if size and size > self.max_in_memory_media_bytes:
                    raise ValueError("حجم رسانه بیشتر از سقف حافظه است.")
                with requests.get(
                    f"https://api.telegram.org/file/bot{helper_token}/{file_path}",
                    timeout=60, stream=True,
                ) as response:
                    response.raise_for_status()
                    declared = int(response.headers.get("content-length") or 0)
                    if declared and declared > self.max_in_memory_media_bytes:
                        raise ValueError("حجم رسانه بیشتر از سقف حافظه است.")
                    chunks = bytearray()
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        chunks.extend(chunk)
                        if len(chunks) > self.max_in_memory_media_bytes:
                            raise ValueError("حجم رسانه بیشتر از سقف حافظه است.")
                return bytes(chunks), Path(file_path).name or default_name
            payload, filename = await asyncio.to_thread(fetch_bot_file)
        else:
            legacy = Path(ref)
            if not legacy.is_file():
                raise FileNotFoundError("مرجع رسانه پیدا نشد.")
            if legacy.stat().st_size > self.max_in_memory_media_bytes:
                raise ValueError("حجم رسانه بیشتر از سقف حافظه است.")
            payload = await asyncio.to_thread(legacy.read_bytes)
            filename = legacy.name or default_name
            try:
                legacy.unlink()
            except OSError:
                pass
        if not payload:
            raise ValueError("رسانه خالی است.")
        buffer = io.BytesIO(payload)
        buffer.name = filename
        buffer.seek(0)
        return buffer

    async def _save_bytes_to_saved_messages(
        self, payload: bytes, *, filename: str, caption: str
    ) -> str:
        if not payload or len(payload) > self.max_in_memory_media_bytes:
            return ""
        buffer = io.BytesIO(payload)
        buffer.name = filename
        saved = await self.queued_send_file(
            "me", buffer, priority=70, caption=caption[:1024], silent=True
        )
        return f"tg:{int(getattr(saved, 'id', 0) or 0)}"

    async def register_handlers(self) -> None:
        @self.client.on(events.NewMessage(outgoing=True))
        async def advanced_outgoing_router(event):
            try:
                if not self.feature_engine.is_owner_command_event(event):
                    return
                if await self.handle_command(event):
                    self.account.last_activity = time.time()
                    self.metric("last_activity", datetime.now().isoformat())
                    raise events.StopPropagation
            except events.StopPropagation:
                raise
            except FloodWaitError as exc:
                seconds = max(1, int(getattr(exc, "seconds", 60)))
                self.record_error(f"FloodWait {seconds}s")
            except Exception as exc:
                self.record_error(f"{type(exc).__name__}: {exc}")
                await self.safe_edit(
                    event,
                    f"❌ اجرای ابزار پیشرفته ناموفق بود: "
                    f"{type(exc).__name__}",
                )

        @self.client.on(events.NewMessage(incoming=True))
        async def advanced_incoming_router(event):
            try:
                await self.remember_incoming_message(event)
                if await self.enforce_private_lock(event):
                    raise events.StopPropagation
            except events.StopPropagation:
                raise
            except FloodWaitError:
                return
            except Exception as exc:
                self.record_error(f"incoming {type(exc).__name__}: {exc}")

        @self.client.on(events.MessageEdited(incoming=True))
        async def anti_edit_router(event):
            try:
                await self.handle_message_edited(event)
            except FloodWaitError as exc:
                seconds = max(1, int(getattr(exc, "seconds", 60)))
                self.record_error(f"anti-edit FloodWait {seconds}s")
            except Exception as exc:
                self.record_error(f"edit {type(exc).__name__}: {exc}")

        @self.client.on(events.ChatAction())
        async def group_greeting_router(event):
            try:
                await self.handle_chat_action(event)
            except Exception as exc:
                self.record_error(f"chat-action {type(exc).__name__}: {exc}")

    def metric(self, key: str, value: Any) -> None:
        try:
            set_runtime_metric(
                self.data_dir,
                self.phone,
                key,
                value,
            )
        except Exception:
            pass

    def record_error(self, message: str) -> None:
        print(f"خطای ابزار پیشرفته برای {self.phone}: {message}")
        self.metric("last_error", str(message)[:500])
        self.metric("last_error_at", datetime.now().isoformat())

    @staticmethod
    def normalize(value: str) -> str:
        text = str(value or "").translate(PERSIAN_DIGITS).strip()
        text = text.replace("ي", "ی").replace("ك", "ک")
        if text.startswith((".", "/")):
            text = text[1:].lstrip()
        return text

    @staticmethod
    def on_off(value: str) -> str | None:
        normalized = str(value or "").strip().lower()
        if normalized in {"on", "روشن", "فعال"}:
            return "on"
        if normalized in {"off", "خاموش", "غیرفعال"}:
            return "off"
        return None

    async def replied_message(self, event):
        if not getattr(event, "is_reply", False):
            return None
        return await event.get_reply_message()

    async def target_from_reply_or_text(
        self,
        event,
        raw_target: str = "",
    ) -> tuple[int, Any]:
        reply = await self.replied_message(event)
        if reply and getattr(reply, "sender_id", None):
            sender = await reply.get_sender()
            return int(reply.sender_id), sender
        target = self.normalize(raw_target)
        if not target:
            raise ValueError("روی پیام کاربر ریپلای کنید یا آیدی او را بنویسید.")
        ref: int | str = int(target) if target.lstrip("-").isdigit() else target
        entity = await self.client.get_entity(ref)
        return int(entity.id), entity

    @staticmethod
    def display_name(entity: Any, fallback: str = "کاربر") -> str:
        parts = [
            str(getattr(entity, "first_name", "") or "").strip(),
            str(getattr(entity, "last_name", "") or "").strip(),
        ]
        name = " ".join(item for item in parts if item).strip()
        username = str(getattr(entity, "username", "") or "").strip()
        return name or (f"@{username}" if username else fallback)

    async def remember_incoming_message(self, event) -> None:
        if self.feature_engine.sensitive_account_message(event):
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
            sender_name = self.display_name(sender, sender_name)
        except Exception:
            pass
        try:
            chat = await event.get_chat()
            chat_title = (
                str(getattr(chat, "title", "") or "").strip()
                or self.display_name(chat, chat_title)
            )
        except Exception:
            pass
        remember_message_version(
            self.data_dir,
            self.phone,
            chat_id=chat_id,
            message_id=message_id,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_title=chat_title,
            message_text=str(getattr(event, "raw_text", "") or ""),
        )

    async def enforce_private_lock(self, event) -> bool:
        settings = self.settings()
        if (
            not getattr(event, "is_private", False)
            or settings.get("private_lock_enabled") != "on"
            or int(getattr(event, "sender_id", 0) or 0) == self.owner_id
        ):
            return False
        sender_id = int(getattr(event, "sender_id", 0) or 0)
        if not sender_id:
            return False
        try:
            sender = await event.get_sender()
            if getattr(sender, "bot", False):
                return False
        except Exception:
            sender = None
        if private_user_is_allowed(
            self.data_dir,
            self.phone,
            sender_id,
        ):
            return False

        warning_limit = self._bounded_int(
            settings.get("private_lock_warning_limit", "1"),
            default=1,
            minimum=0,
            maximum=10,
        )
        attempt = register_private_lock_attempt(
            self.data_dir,
            self.phone,
            sender_id,
        )
        warn_first = settings.get("private_lock_warn_before_block", "on") == "on"
        should_block = not warn_first or attempt > warning_limit
        if should_block:
            try:
                await self.client(
                    functions.contacts.BlockRequest(id=sender_id)
                )
                mark_private_user_blocked(
                    self.data_dir,
                    self.phone,
                    sender_id,
                )
            except Exception as exc:
                self.record_error(
                    f"private-block {sender_id} {type(exc).__name__}"
                )
        else:
            warning = str(
                settings.get("private_lock_warning_text", "")
                or "⛔ پیام خصوصی این حساب بسته است."
            )
            try:
                if getattr(self.account, "queued_send_message", None) is None:
                    await event.reply(warning[:4000])
                else:
                    await self.queued_send_message(
                        event.chat_id,
                        warning[:4000],
                        reply_to=int(event.id),
                        priority=40,
                    )
            except Exception:
                pass

        if settings.get("private_lock_delete_unknown", "on") == "on":
            try:
                await event.delete()
            except Exception:
                pass
        return True

    async def handle_message_edited(self, event) -> None:
        if int(getattr(event, "sender_id", 0) or 0) == self.owner_id:
            return
        settings = self.settings()
        is_private = bool(getattr(event, "is_private", False))
        is_group = bool(getattr(event, "is_group", False))
        if (
            is_private
            and settings.get("anti_edit_private") != "on"
        ) or (
            is_group
            and settings.get("anti_edit_groups") != "on"
        ) or (not is_private and not is_group):
            return
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        message_id = int(getattr(event, "id", 0) or 0)
        previous = get_message_version(
            self.data_dir,
            self.phone,
            chat_id=chat_id,
            message_id=message_id,
        )
        after = str(getattr(event, "raw_text", "") or "")
        if not previous:
            await self.remember_incoming_message(event)
            return
        before = str(previous.get("message_text") or "")
        if before == after:
            return
        sender_name = str(previous.get("sender_name") or "نامشخص")
        chat_title = str(previous.get("chat_title") or chat_id)
        sender_id = int(previous.get("sender_id") or 0)
        scope = "private" if is_private else "group"
        record_message_edit(
            self.data_dir,
            self.phone,
            chat_id=chat_id,
            message_id=message_id,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_title=chat_title,
            before_text=before,
            after_text=after,
            scope=scope,
        )
        await self.remember_incoming_message(event)
        if settings.get("anti_edit_notify_saved", "on") != "on":
            return
        report = (
            "✏️ پیام ویرایش شد\n"
            f"👤 فرستنده: {sender_name}\n"
            f"🆔 کاربر: {sender_id or 'نامشخص'}\n"
            f"💬 چت: {chat_title}\n"
            f"🆔 پیام: {message_id}\n\n"
            f"قبل:\n{before or '—'}\n\n"
            f"بعد:\n{after or '—'}"
        )
        await self.queued_send_message(
            "me",
            report[:4000],
            parse_mode=None,
            silent=True,
            priority=70,
        )

    async def handle_chat_action(self, event) -> None:
        if not getattr(event, "is_group", False):
            return
        settings = self.settings()
        joined = bool(
            getattr(event, "user_joined", False)
            or getattr(event, "user_added", False)
        )
        left = bool(
            getattr(event, "user_left", False)
            or getattr(event, "user_kicked", False)
        )
        if (
            joined
            and settings.get("welcome_enabled") != "on"
        ) or (
            left
            and settings.get("goodbye_enabled") != "on"
        ) or (not joined and not left):
            return
        users = await event.get_users()
        if not isinstance(users, (list, tuple)):
            users = [users]
        chat = await event.get_chat()
        chat_name = str(getattr(chat, "title", "") or event.chat_id)
        template = str(
            settings.get(
                "welcome_text" if joined else "goodbye_text",
                "",
            )
        )
        for user in users[:10]:
            if not user:
                continue
            text = template.format(
                name=self.display_name(user),
                id=int(getattr(user, "id", 0) or 0),
                username=(
                    f"@{user.username}"
                    if getattr(user, "username", None)
                    else ""
                ),
                chat=chat_name,
            )
            await self.queued_send_message(
                event.chat_id,
                text[:4000],
                priority=50,
            )

    async def handle_command(self, event) -> bool:
        raw = self.normalize(getattr(event, "raw_text", ""))
        if not raw:
            return False
        lower = raw.lower()

        if lower in {"قفل پیوی", "private lock"}:
            settings = self.settings()
            allowed = list_private_allowlist(
                self.data_dir,
                self.phone,
                limit=500,
            )
            await self.safe_edit(
                event,
                "🔐 قفل پیوی\n\n"
                f"وضعیت: {self._state(settings, 'private_lock_enabled')}\n"
                f"حذف ناشناس: "
                f"{self._state(settings, 'private_lock_delete_unknown')}\n"
                f"هشدار قبل بلاک: "
                f"{self._state(settings, 'private_lock_warn_before_block')}\n"
                f"تعداد هشدار: "
                f"{settings.get('private_lock_warning_limit', '1')}\n"
                f"افراد مجاز: {len(allowed)} نفر",
            )
            return True

        match = re.fullmatch(
            r"(?:قفل پیوی|private lock)\s+(روشن|خاموش|on|off)",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"private_lock_enabled": state})
            await self.safe_edit(
                event,
                f"✅ قفل پیوی "
                f"{'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.fullmatch(
            r"(?:مجاز افزودن|allow)\s*(.*)",
            raw,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            try:
                user_id, entity = await self.target_from_reply_or_text(
                    event,
                    match.group(1),
                )
            except ValueError as exc:
                await self.safe_edit(event, f"❌ {exc}")
                return True
            set_private_allowlist_user(
                self.data_dir,
                self.phone,
                user_id,
                allowed=True,
                label=self.display_name(entity),
            )
            await self.safe_edit(event, f"✅ کاربر `{user_id}` مجاز شد.")
            return True

        match = re.fullmatch(
            r"(?:مجاز حذف|disallow)\s*(.*)",
            raw,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            try:
                user_id, _ = await self.target_from_reply_or_text(
                    event,
                    match.group(1),
                )
            except ValueError as exc:
                await self.safe_edit(event, f"❌ {exc}")
                return True
            set_private_allowlist_user(
                self.data_dir,
                self.phone,
                user_id,
                allowed=False,
            )
            await self.safe_edit(event, f"✅ کاربر `{user_id}` از مجازها حذف شد.")
            return True

        if lower in {"مجازها", "allowlist"}:
            rows = list_private_allowlist(
                self.data_dir,
                self.phone,
                limit=100,
            )
            lines = ["✅ فهرست افراد مجاز پیوی:"]
            lines.extend(
                f"• `{row['user_id']}` — {row['label'] or 'بدون نام'}"
                for row in rows
            )
            if not rows:
                lines.append("• فهرست خالی است.")
            await self.safe_edit(event, "\n".join(lines))
            return True

        match = re.fullmatch(
            r"(?:متن هشدار پیوی|pv warning)\s+([\s\S]+)",
            raw,
            re.IGNORECASE,
        )
        if match:
            warning = match.group(1).strip()
            if not 1 <= len(warning) <= 1000:
                await self.safe_edit(
                    event,
                    "❌ متن هشدار باید بین ۱ تا ۱۰۰۰ نویسه باشد.",
                )
            else:
                self.save_settings({"private_lock_warning_text": warning})
                await self.safe_edit(event, "✅ متن هشدار پیوی ذخیره شد.")
            return True

        match = re.fullmatch(
            r"(?:ضد ویرایش|anti edit)\s+(پیوی|گروه|private|group)\s+"
            r"(روشن|خاموش|on|off)",
            lower,
        )
        if match:
            key = (
                "anti_edit_private"
                if match.group(1) in {"پیوی", "private"}
                else "anti_edit_groups"
            )
            state = self.on_off(match.group(2))
            self.save_settings({key: state})
            await self.safe_edit(
                event,
                "✅ ضد ویرایش "
                f"{'پیوی' if key.endswith('private') else 'گروه'} "
                f"{'فعال' if state == 'on' else 'غیرفعال'} شد.",
            )
            return True

        match = re.fullmatch(
            r"(?:جستجو|search)\s+([\s\S]+)",
            raw,
            re.IGNORECASE,
        )
        if match:
            await self.search_messages(event, match.group(1).strip())
            return True

        if lower in {"شناسه پیام", "آیدی پیام", "message info"}:
            await self.show_message_info(event)
            return True

        if lower in {"ذخیره پیام", "save message"}:
            await self.save_message(event)
            return True

        match = re.fullmatch(
            r"(?:ارسال یکباره|ارسال یک‌باره|send once)\s+"
            r"(.+?)\s*\|\s*([\s\S]+)",
            raw,
            re.IGNORECASE,
        )
        if match:
            await self.schedule_once(
                event,
                match.group(1).strip(),
                match.group(2).strip(),
            )
            return True

        if lower in {"ارسال‌های یکباره", "ارسال های یکباره", "once list"}:
            await self.show_scheduled_once(event)
            return True

        match = re.fullmatch(
            r"(?:لغو ارسال یکباره|once cancel)\s+(\d+)",
            lower,
        )
        if match:
            update_scheduled_once_status(
                self.data_dir,
                self.phone,
                int(match.group(1)),
                status="cancelled",
            )
            await self.safe_edit(event, "✅ ارسال یک‌باره لغو شد.")
            return True

        match = re.fullmatch(
            r"(?:دانلود پیام|download message)(?:\s+([\s\S]+))?",
            raw,
            re.IGNORECASE,
        )
        if match:
            await self.download_message(event, (match.group(1) or "").strip())
            return True

        match = re.fullmatch(r"(?:نام|first name)\s+([\s\S]+)", raw, re.I)
        if match:
            await self.update_profile_text(
                event,
                "first_name",
                match.group(1).strip(),
            )
            return True

        match = re.fullmatch(
            r"(?:نام خانوادگی|last name)\s+([\s\S]*)",
            raw,
            re.IGNORECASE,
        )
        if match:
            await self.update_profile_text(
                event,
                "last_name",
                match.group(1).strip(),
            )
            return True

        match = re.fullmatch(r"(?:بیو|bio)\s+([\s\S]*)", raw, re.I)
        if match:
            await self.update_profile_text(
                event,
                "about",
                match.group(1).strip(),
            )
            return True

        if lower in {"عکس پروفایل", "profile photo"}:
            await self.update_profile_photo(event)
            return True

        if lower in {"کپی پروفایل", "copy profile"}:
            await self.copy_profile(event)
            return True

        if lower in {"بازیابی پروفایل", "restore profile"}:
            await self.restore_profile(event)
            return True

        match = re.fullmatch(
            r"(?:اکشن|action)\s+(\S+)(?:\s+(\d+))?",
            lower,
        )
        if match and match.group(1) in ACTION_ALIASES:
            await self.show_action(
                event,
                ACTION_ALIASES[match.group(1)],
                match.group(2),
            )
            return True

        if lower in {"پین", "pin"}:
            await self.pin_message(event)
            return True

        if lower in {"آنپین", "آن پین", "unpin"}:
            await self.unpin_message(event)
            return True

        match = re.fullmatch(
            r"(?:اخراج|kick)(?:\s+([\s\S]+))?",
            raw,
            re.IGNORECASE,
        )
        if match:
            await self.kick_user(event, (match.group(1) or "").strip())
            return True

        match = re.fullmatch(
            r"(?:سکوت|mute)(?:\s+(\d+))?(?:\s+([\s\S]+))?",
            raw,
            re.IGNORECASE,
        )
        if match:
            await self.mute_user(
                event,
                match.group(2) or "",
                match.group(1),
            )
            return True

        match = re.fullmatch(
            r"(?:رفع سکوت|unmute)(?:\s+([\s\S]+))?",
            raw,
            re.IGNORECASE,
        )
        if match:
            await self.unmute_user(event, (match.group(1) or "").strip())
            return True

        if lower in {"گزارش مدیران", "report admins"}:
            await self.report_to_admins(event)
            return True

        match = re.fullmatch(
            r"(?:خوش آمد|welcome)\s+(روشن|خاموش|on|off)",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"welcome_enabled": state})
            await self.safe_edit(event, "✅ تنظیم خوش‌آمد ذخیره شد.")
            return True

        match = re.fullmatch(
            r"(?:خداحافظی|goodbye)\s+(روشن|خاموش|on|off)",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            self.save_settings({"goodbye_enabled": state})
            await self.safe_edit(event, "✅ تنظیم خداحافظی ذخیره شد.")
            return True

        match = re.fullmatch(
            r"(?:متن خوش آمد|welcome text)\s+([\s\S]+)",
            raw,
            re.IGNORECASE,
        )
        if match:
            self.save_settings({"welcome_text": match.group(1).strip()[:1000]})
            await self.safe_edit(event, "✅ متن خوش‌آمد ذخیره شد.")
            return True

        match = re.fullmatch(
            r"(?:متن خداحافظی|goodbye text)\s+([\s\S]+)",
            raw,
            re.IGNORECASE,
        )
        if match:
            self.save_settings({"goodbye_text": match.group(1).strip()[:1000]})
            await self.safe_edit(event, "✅ متن خداحافظی ذخیره شد.")
            return True

        if lower in {"آمار حساب", "account stats"}:
            await self.show_account_stats(event)
            return True

        match = re.fullmatch(r"(?:کیوآر|qr)\s+([\s\S]+)", raw, re.I)
        if match:
            await self.create_qr(event, match.group(1).strip())
            return True

        match = re.fullmatch(
            r"(?:ساعت عکس|photo clock)\s+(روشن|خاموش|on|off)",
            lower,
        )
        if match:
            state = self.on_off(match.group(1))
            if state == "on":
                await self.enable_analog_clock(event)
            else:
                self.save_settings({"analog_clock_enabled": "off"})
                await self.safe_edit(event, "✅ ساعت عقربه‌ای عکس خاموش شد.")
            return True

        match = re.fullmatch(
            r"(?:فونت نام|name font)\s+([1-9]|10)",
            lower,
        )
        if match:
            self.save_settings({"timename_font": match.group(1)})
            await self.safe_edit(event, "✅ فونت ساعت نام ذخیره شد.")
            return True

        match = re.fullmatch(
            r"(?:فونت بیو|bio font)\s+([1-9]|10)",
            lower,
        )
        if match:
            self.save_settings({"timebio_font": match.group(1)})
            await self.safe_edit(event, "✅ فونت ساعت بیو ذخیره شد.")
            return True

        return False

    async def search_messages(self, event, query: str) -> None:
        if not query:
            await self.safe_edit(event, "❌ عبارت جست‌وجو خالی است.")
            return
        results = []
        async for message in self.client.iter_messages(
            event.chat_id,
            search=query,
            limit=20,
        ):
            text = str(getattr(message, "raw_text", "") or "").replace(
                "\n",
                " ",
            )
            results.append(
                f"• پیام `{message.id}` | کاربر "
                f"`{int(getattr(message, 'sender_id', 0) or 0)}`\n"
                f"  {text[:140] or '[رسانه]'}"
            )
        body = "\n\n".join(results) or "نتیجه‌ای پیدا نشد."
        await self.safe_edit(
            event,
            f"🔎 نتایج «{query[:80]}»\n"
            f"🆔 چت: `{event.chat_id}`\n\n{body}"[:4000],
        )

    async def show_message_info(self, event) -> None:
        message = await self.replied_message(event) or event
        sender_id = int(getattr(message, "sender_id", 0) or 0)
        chat_id = int(getattr(event, "chat_id", 0) or 0)
        message_id = int(getattr(message, "id", 0) or 0)
        username = ""
        try:
            sender = await message.get_sender()
            username = str(getattr(sender, "username", "") or "")
        except Exception:
            pass
        await self.safe_edit(
            event,
            "🪪 اطلاعات پیام\n\n"
            f"🆔 پیام: `{message_id}`\n"
            f"👤 کاربر: `{sender_id}`\n"
            f"💬 گروه/چت: `{chat_id}`\n"
            f"🔗 یوزرنیم: @{username or 'ندارد'}",
        )

    async def save_message(self, event) -> None:
        message = await self.replied_message(event)
        if not message:
            await self.safe_edit(event, "❌ روی پیام موردنظر ریپلای کنید.")
            return
        try:
            await self.feature_engine.save_message_to_cloud(
                message, caption="📥 ذخیره دستی پیام"
            )
        except Exception as exc:
            await self.safe_edit(
                event, f"❌ ذخیره پیام ناموفق بود: {type(exc).__name__}"
            )
            return
        await self.safe_edit(event, "✅ پیام در Saved Messages ذخیره شد.")

    async def schedule_once(
        self,
        event,
        raw_time: str,
        message_text: str,
    ) -> None:
        send_at = self.parse_local_datetime(raw_time)
        if send_at is None:
            await self.safe_edit(
                event,
                "❌ زمان معتبر نیست.\n"
                "نمونه: `ارسال یکباره 2026-07-29 18:30 | سلام`\n"
                "یا: `ارسال یکباره 18:30 | سلام`",
            )
            return
        if send_at <= datetime.now().astimezone():
            await self.safe_edit(event, "❌ زمان ارسال باید در آینده باشد.")
            return
        target = str(int(getattr(event, "chat_id", 0) or 0))
        schedule_id = create_scheduled_once(
            self.data_dir,
            self.phone,
            target=target,
            message_text=message_text,
            send_at=send_at.isoformat(timespec="seconds"),
            reply_to_message_id=(
                int(getattr(event.message, "reply_to_msg_id", 0) or 0) or None
            ),
        )
        await self.safe_edit(
            event,
            f"✅ ارسال یک‌باره #{schedule_id} ثبت شد.\n"
            f"🕒 {send_at.strftime('%Y-%m-%d %H:%M:%S')}",
        )

    @staticmethod
    def parse_local_datetime(raw_time: str) -> datetime | None:
        value = str(raw_time or "").strip()
        local_tz = datetime.now().astimezone().tzinfo
        formats = ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%H:%M")
        for fmt in formats:
            try:
                parsed = datetime.strptime(value, fmt)
            except ValueError:
                continue
            if fmt == "%H:%M":
                now = datetime.now(local_tz)
                parsed = parsed.replace(
                    year=now.year,
                    month=now.month,
                    day=now.day,
                )
                if parsed.time() <= now.time():
                    parsed += timedelta(days=1)
            return parsed.replace(tzinfo=local_tz)
        return None

    async def show_scheduled_once(self, event) -> None:
        rows = list_scheduled_once(
            self.data_dir,
            self.phone,
            status="pending",
            limit=30,
        )
        lines = ["⏰ ارسال‌های یک‌باره در انتظار:"]
        lines.extend(
            f"• #{row['id']} | {row['send_at']} | "
            f"{str(row['message_text'])[:80]}"
            for row in rows
        )
        if not rows:
            lines.append("• موردی ثبت نشده است.")
        await self.safe_edit(event, "\n".join(lines)[:4000])

    async def scheduled_once_loop(self) -> None:
        while self.account.is_running and not self.account.shutdown_requested:
            try:
                now_iso = datetime.now().astimezone().isoformat(
                    timespec="seconds"
                )
                rows = list_due_scheduled_once(
                    self.data_dir,
                    self.phone,
                    now_iso=now_iso,
                    limit=20,
                )
                for row in rows:
                    try:
                        target = self.normalize_target(row["target"])
                        kwargs = {}
                        if row.get("reply_to_message_id"):
                            kwargs["reply_to"] = int(row["reply_to_message_id"])
                        await self.queued_send_message(
                            target,
                            str(row["message_text"]),
                            priority=60,
                            **kwargs,
                        )
                        update_scheduled_once_status(
                            self.data_dir,
                            self.phone,
                            int(row["id"]),
                            status="sent",
                        )
                    except Exception as exc:
                        update_scheduled_once_status(
                            self.data_dir,
                            self.phone,
                            int(row["id"]),
                            status="failed",
                            error_text=f"{type(exc).__name__}: {exc}",
                        )
                        self.record_error(
                            f"scheduled-once #{row['id']} "
                            f"{type(exc).__name__}"
                        )
            except Exception as exc:
                self.record_error(
                    f"scheduled-loop {type(exc).__name__}: {exc}"
                )
            await asyncio.sleep(10)

    async def professional_schedule_loop(self) -> None:
        """Run persistent one-time and recurring jobs through the send queue."""
        while self.account.is_running and not self.account.shutdown_requested:
            try:
                now = datetime.now().astimezone()
                rows = claim_due_schedule_jobs(
                    self.data_dir,
                    self.phone,
                    now_iso=now.isoformat(timespec="seconds"),
                    stale_before_iso=(now - timedelta(minutes=10)).isoformat(
                        timespec="seconds"
                    ),
                    limit=10,
                )
                for row in rows:
                    try:
                        target = self.normalize_target(row["target"])
                        message_type = str(row.get("message_type") or "text")
                        if message_type == "text":
                            sent = await self.queued_send_message(
                                target,
                                str(row.get("message_text") or ""),
                                priority=60,
                            )
                        else:
                            media_path = str(row.get("media_path") or "")
                            if not media_path:
                                raise FileNotFoundError("مرجع رسانه برنامه پیدا نشد.")
                            media_buffer = await self._buffer_from_media_reference(
                                media_path, default_name=f"schedule_{row['id']}.bin"
                            )
                            kwargs: dict[str, Any] = {"priority": 60}
                            caption = str(row.get("caption") or "")
                            if caption and message_type != "sticker":
                                kwargs["caption"] = caption[:1000]
                            if message_type == "voice":
                                kwargs["voice_note"] = True
                            sent = await self.queued_send_file(
                                target,
                                media_buffer,
                                **kwargs,
                            )
                        message_id = int(getattr(sent, "id", 0) or 0)
                        next_run_at = self.next_schedule_run(row, now)
                        finish_schedule_job_run(
                            self.data_dir,
                            self.phone,
                            int(row["id"]),
                            next_run_at=next_run_at,
                            message_id=message_id or None,
                        )
                        delete_after = max(
                            0,
                            int(row.get("delete_after_minutes") or 0),
                        )
                        if delete_after and message_id:
                            self.account.start_background_task(
                                self.delete_scheduled_message_later(
                                    target, message_id, delete_after
                                ),
                                name="scheduled-delete",
                            )
                    except Exception as exc:
                        retry_at = (
                            datetime.now().astimezone() + timedelta(minutes=5)
                        ).isoformat(timespec="seconds")
                        finish_schedule_job_run(
                            self.data_dir,
                            self.phone,
                            int(row["id"]),
                            next_run_at=retry_at,
                            error_text=f"{type(exc).__name__}: {exc}",
                        )
                        self.record_error(
                            f"schedule-job #{row['id']} "
                            f"{type(exc).__name__}: {exc}"
                        )
            except Exception as exc:
                self.record_error(
                    f"professional-schedule {type(exc).__name__}: {exc}"
                )
            await asyncio.sleep(10)

    @staticmethod
    def next_schedule_run(row: dict[str, Any], now: datetime) -> str | None:
        recurrence = str(row.get("recurrence_type") or "once")
        value = str(row.get("recurrence_value") or "")
        current = datetime.fromisoformat(str(row["next_run_at"]))
        if current.tzinfo is None:
            current = current.replace(tzinfo=now.tzinfo)
        if recurrence == "once":
            return None
        if recurrence == "interval":
            minutes = max(1, min(int(value or "1"), 10080))
            candidate = current
            while candidate <= now:
                candidate += timedelta(minutes=minutes)
            return candidate.isoformat(timespec="seconds")
        if recurrence == "daily":
            hour, minute = (int(part) for part in value.split(":", 1))
            candidate = now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.isoformat(timespec="seconds")
        if recurrence == "weekly":
            payload = json.loads(value)
            weekday = max(0, min(int(payload["weekday"]), 6))
            hour, minute = (
                int(part) for part in str(payload["time"]).split(":", 1)
            )
            days_ahead = (weekday - now.weekday()) % 7
            candidate = (now + timedelta(days=days_ahead)).replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if candidate <= now:
                candidate += timedelta(days=7)
            return candidate.isoformat(timespec="seconds")
        return None

    async def delete_scheduled_message_later(
        self,
        target: int | str,
        message_id: int,
        delay_minutes: int,
    ) -> None:
        await asyncio.sleep(max(1, int(delay_minutes)) * 60)
        try:
            if self.account.send_queue is not None:
                await self.account.send_queue.execute(
                    lambda: self.client.delete_messages(target, [message_id]),
                    description=f"delete_scheduled:{target}:{message_id}",
                    priority=80,
                )
            else:
                await self.client.delete_messages(target, [message_id])
        except Exception as exc:
            self.record_error(
                f"schedule-delete {type(exc).__name__}: {exc}"
            )

    @staticmethod
    def normalize_target(value: Any) -> int | str:
        text = str(value or "").strip()
        return int(text) if text.lstrip("-").isdigit() else text

    async def download_message(self, event, raw_reference: str) -> None:
        message = await self.replied_message(event)
        if not message:
            try:
                entity, message_id = await self.resolve_message_reference(
                    event, raw_reference
                )
                message = await self.client.get_messages(entity, ids=message_id)
            except ValueError as exc:
                await self.safe_edit(event, f"❌ {exc}")
                return
        if not message:
            await self.safe_edit(event, "❌ پیام پیدا نشد یا دسترسی ندارید.")
            return
        try:
            await self.feature_engine.save_message_to_cloud(
                message, caption="📥 ذخیره پیام با لینک/آیدی"
            )
        except Exception as exc:
            await self.safe_edit(
                event, f"❌ ذخیره پیام ناموفق بود: {type(exc).__name__}"
            )
            return
        await self.safe_edit(event, "✅ پیام در Saved Messages ذخیره شد.")

    async def resolve_message_reference(
        self,
        event,
        reference: str,
    ) -> tuple[int | str, int]:
        value = str(reference or "").strip()
        if not value:
            raise ValueError("روی پیام ریپلای کنید یا لینک/آیدی پیام را بنویسید.")
        if value.isdigit():
            return int(event.chat_id), int(value)
        match = re.fullmatch(
            r"https?://t\.me/(c/)?([^/]+)/(\d+)(?:\?.*)?",
            value,
            re.IGNORECASE,
        )
        if not match:
            raise ValueError("لینک پیام یا آیدی معتبر نیست.")
        is_private_group = bool(match.group(1))
        chat_ref = match.group(2)
        if is_private_group:
            entity: int | str = int(f"-100{int(chat_ref)}")
        else:
            entity = chat_ref
        return entity, int(match.group(3))

    async def backup_profile(self, reason: str) -> int:
        me = await self.client.get_me()
        full = await self.client(functions.users.GetFullUserRequest(id=me))
        about = str(getattr(full.full_user, "about", "") or "")
        photo_reference = ""
        try:
            payload = await self.client.download_profile_photo(me, file=bytes)
            if payload:
                photo_reference = await self._save_bytes_to_saved_messages(
                    payload,
                    filename="profile-backup.jpg",
                    caption=f"👤 بکاپ پروفایل — {reason}",
                )
        except Exception:
            photo_reference = ""
        return create_profile_backup(
            self.data_dir, self.phone,
            first_name=str(getattr(me, "first_name", "") or ""),
            last_name=str(getattr(me, "last_name", "") or ""),
            about=about, photo_path=photo_reference, reason=reason,
        )

    async def update_profile_text(
        self,
        event,
        field: str,
        value: str,
    ) -> None:
        limits = {"first_name": 64, "last_name": 64, "about": 140}
        if field == "first_name" and not value:
            await self.safe_edit(event, "❌ نام نمی‌تواند خالی باشد.")
            return
        if len(value) > limits[field]:
            await self.safe_edit(
                event,
                f"❌ طول متن بیشتر از {limits[field]} نویسه است.",
            )
            return
        await self.backup_profile(f"update-{field}")
        if field == "first_name":
            self.save_settings({"profile_base_first_name": value})
        kwargs = {field: value}
        await self.client(functions.account.UpdateProfileRequest(**kwargs))
        if field == "first_name":
            self.account.last_presence_signature = None
            await self.account.apply_presence_name_emoji(force=True)
        labels = {
            "first_name": "نام",
            "last_name": "نام خانوادگی",
            "about": "بیو",
        }
        await self.safe_edit(event, f"✅ {labels[field]} بروزرسانی شد.")

    async def update_profile_photo(self, event) -> None:
        reply = await self.replied_message(event)
        if not reply or not getattr(reply, "photo", None):
            await self.safe_edit(event, "❌ روی یک عکس ریپلای کنید.")
            return
        await self.backup_profile("update-photo")
        payload = await self.client.download_media(reply, file=bytes)
        if not payload or len(payload) > self.max_in_memory_media_bytes:
            await self.safe_edit(event, "❌ عکس قابل پردازش نیست یا بیش از حد بزرگ است.")
            return
        buffer = io.BytesIO(payload)
        buffer.name = "profile.jpg"
        uploaded = await self.client.upload_file(buffer)
        await self.client(functions.photos.UploadProfilePhotoRequest(file=uploaded))
        await self.safe_edit(event, "✅ عکس پروفایل بروزرسانی شد.")

    async def copy_profile(self, event) -> None:
        reply = await self.replied_message(event)
        if not reply:
            await self.safe_edit(event, "❌ روی پیام شخص موردنظر ریپلای کنید.")
            return
        target = await reply.get_sender()
        if not target or getattr(target, "bot", False):
            await self.safe_edit(event, "❌ پروفایل این حساب قابل کپی نیست.")
            return
        full = await self.client(
            functions.users.GetFullUserRequest(id=target)
        )
        await self.backup_profile(
            f"copy-profile-{int(getattr(target, 'id', 0) or 0)}"
        )
        await self.client(
            functions.account.UpdateProfileRequest(
                first_name=str(getattr(target, "first_name", "") or "کاربر"),
                last_name=str(getattr(target, "last_name", "") or ""),
                about=str(getattr(full.full_user, "about", "") or "")[:140],
            )
        )
        try:
            payload = await self.client.download_profile_photo(target, file=bytes)
            if payload and len(payload) <= self.max_in_memory_media_bytes:
                buffer = io.BytesIO(payload)
                buffer.name = "copied-profile.jpg"
                uploaded = await self.client.upload_file(buffer)
                await self.client(
                    functions.photos.UploadProfilePhotoRequest(file=uploaded)
                )
        except Exception:
            pass
        await self.safe_edit(
            event,
            "✅ پروفایل کپی شد و نسخه قبلی برای بازیابی ذخیره شد.",
        )

    async def restore_profile(self, event) -> None:
        backup = get_latest_profile_backup(self.data_dir, self.phone)
        if not backup:
            await self.safe_edit(event, "❌ بکاپ پروفایلی وجود ندارد.")
            return
        await self.client(
            functions.account.UpdateProfileRequest(
                first_name=str(backup.get("first_name") or "کاربر"),
                last_name=str(backup.get("last_name") or ""),
                about=str(backup.get("about") or "")[:140],
            )
        )
        photo_reference = str(backup.get("photo_path") or "")
        if photo_reference:
            try:
                buffer = await self._buffer_from_media_reference(
                    photo_reference, default_name="profile-restore.jpg"
                )
                uploaded = await self.client.upload_file(buffer)
                await self.client(
                    functions.photos.UploadProfilePhotoRequest(file=uploaded)
                )
            except Exception:
                pass
        self.save_settings({"analog_clock_enabled": "off"})
        await self.safe_edit(event, "✅ آخرین بکاپ پروفایل بازگردانی شد.")

    async def show_action(
        self,
        event,
        action: str,
        raw_duration: str | None,
    ) -> None:
        settings = self.settings()
        duration = self._bounded_int(
            raw_duration or settings.get("action_default_duration", "5"),
            default=5,
            minimum=1,
            maximum=300,
        )
        self.feature_engine.mark_own_deletion(event)
        await event.delete()
        async with self.client.action(event.chat_id, action):
            await asyncio.sleep(duration)

    async def pin_message(self, event) -> None:
        reply = await self.replied_message(event)
        if not reply:
            await self.safe_edit(event, "❌ روی پیام موردنظر ریپلای کنید.")
            return
        await self.client.pin_message(event.chat_id, reply, notify=False)
        await self.safe_edit(event, "✅ پیام پین شد.")

    async def unpin_message(self, event) -> None:
        reply = await self.replied_message(event)
        if reply:
            await self.client.unpin_message(event.chat_id, reply)
        else:
            await self.client.unpin_message(event.chat_id)
        await self.safe_edit(event, "✅ پیام آن‌پین شد.")

    async def kick_user(self, event, raw_target: str) -> None:
        try:
            user_id, _ = await self.target_from_reply_or_text(event, raw_target)
            await self.client.kick_participant(event.chat_id, user_id)
            await self.safe_edit(event, f"✅ کاربر `{user_id}` اخراج شد.")
        except ChatAdminRequiredError:
            await self.safe_edit(event, "❌ برای اخراج باید مدیر گروه باشید.")
        except ValueError as exc:
            await self.safe_edit(event, f"❌ {exc}")

    async def mute_user(
        self,
        event,
        raw_target: str,
        raw_minutes: str | None,
    ) -> None:
        try:
            user_id, _ = await self.target_from_reply_or_text(event, raw_target)
            minutes = self._bounded_int(
                raw_minutes or "10",
                default=10,
                minimum=1,
                maximum=43200,
            )
            until = datetime.now().astimezone() + timedelta(minutes=minutes)
            rights = types.ChatBannedRights(
                until_date=until,
                send_messages=True,
            )
            await self.client(
                functions.channels.EditBannedRequest(
                    channel=event.chat_id,
                    participant=user_id,
                    banned_rights=rights,
                )
            )
            await self.safe_edit(
                event,
                f"✅ کاربر `{user_id}` برای {minutes} دقیقه سکوت شد.",
            )
        except ChatAdminRequiredError:
            await self.safe_edit(event, "❌ برای سکوت باید مدیر گروه باشید.")
        except ValueError as exc:
            await self.safe_edit(event, f"❌ {exc}")

    async def unmute_user(self, event, raw_target: str) -> None:
        try:
            user_id, _ = await self.target_from_reply_or_text(event, raw_target)
            rights = types.ChatBannedRights(until_date=None)
            await self.client(
                functions.channels.EditBannedRequest(
                    channel=event.chat_id,
                    participant=user_id,
                    banned_rights=rights,
                )
            )
            await self.safe_edit(event, f"✅ سکوت کاربر `{user_id}` برداشته شد.")
        except ChatAdminRequiredError:
            await self.safe_edit(event, "❌ برای رفع سکوت باید مدیر گروه باشید.")
        except ValueError as exc:
            await self.safe_edit(event, f"❌ {exc}")

    async def report_to_admins(self, event) -> None:
        reply = await self.replied_message(event)
        if not reply or not getattr(event, "is_group", False):
            await self.safe_edit(
                event,
                "❌ در گروه روی پیام موردنظر ریپلای کنید.",
            )
            return
        settings = self.settings()
        limit = self._bounded_int(
            settings.get("group_report_admin_limit", "10"),
            default=10,
            minimum=1,
            maximum=20,
        )
        admins = await self.client.get_participants(
            event.chat_id,
            filter=types.ChannelParticipantsAdmins(),
        )
        sender_id = int(getattr(reply, "sender_id", 0) or 0)
        report = (
            "🚨 گزارش پیام گروه\n"
            f"💬 گروه: {event.chat_id}\n"
            f"👤 فرستنده: {sender_id}\n"
            f"🆔 پیام: {reply.id}\n\n"
            f"{str(getattr(reply, 'raw_text', '') or '[رسانه]')[:2500]}"
        )
        delivered = 0
        for admin in admins:
            admin_id = int(getattr(admin, "id", 0) or 0)
            if (
                not admin_id
                or admin_id == self.owner_id
                or getattr(admin, "bot", False)
            ):
                continue
            try:
                await self.client.send_message(admin_id, report)
                delivered += 1
            except Exception:
                continue
            if delivered >= limit:
                break
        await self.client.send_message("me", report, silent=True)
        await self.safe_edit(
            event,
            f"✅ گزارش در Saved Messages ثبت و به "
            f"{delivered} مدیر قابل‌دسترسی ارسال شد.",
        )

    async def show_account_stats(self, event) -> None:
        private_count = group_count = channel_count = unread_count = 0
        async for dialog in self.client.iter_dialogs():
            unread_count += int(getattr(dialog, "unread_count", 0) or 0)
            if getattr(dialog, "is_user", False):
                private_count += 1
            elif getattr(dialog, "is_group", False):
                group_count += 1
            elif getattr(dialog, "is_channel", False):
                channel_count += 1
        helper = get_helper_config(self.users_db)
        helper_pid = int(helper.get("pid") or 0)
        helper_running = self._pid_running(helper_pid)
        today = datetime.now().astimezone().date().isoformat()
        usage = get_chatgpt_daily_usage(
            self.data_dir,
            self.phone,
            today,
        )
        metrics = get_runtime_metrics(self.data_dir, self.phone)
        self_running = bool(
            self.account.is_running
            and self.client
            and self.client.is_connected()
        )
        await self.safe_edit(
            event,
            "📊 آمار حساب\n\n"
            f"👤 پیوی‌ها: {private_count}\n"
            f"👥 گروه‌ها: {group_count}\n"
            f"📣 کانال‌ها: {channel_count}\n"
            f"📨 خوانده‌نشده: {unread_count}\n\n"
            f"🤖 سلف: {'🟢 فعال' if self_running else '🔴 متوقف'}\n"
            f"🧩 هلپر: {'🟢 فعال' if helper_running else '🔴 متوقف'}\n"
            f"🧠 ChatGPT امروز: {usage['request_count']} درخواست | "
            f"{usage['input_tokens'] + usage['output_tokens']} توکن\n"
            f"⚠️ آخرین خطا: "
            f"{metrics.get('last_error', 'ثبت نشده')[:180]}\n"
            f"🕒 آخرین فعالیت: "
            f"{metrics.get('last_activity', 'ثبت نشده')}",
        )

    @staticmethod
    def _pid_running(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    async def create_qr(self, event, text: str) -> None:
        if qrcode is None:
            await self.safe_edit(
                event,
                "❌ کتابخانه QR نصب نیست؛ requirements.txt را نصب کنید.",
            )
            return
        if not 1 <= len(text) <= 2000:
            await self.safe_edit(
                event,
                "❌ متن QR باید بین ۱ تا ۲۰۰۰ نویسه باشد.",
            )
            return
        image = await asyncio.to_thread(qrcode.make, text)
        buffer = io.BytesIO()
        buffer.name = "qr-code.png"
        image.save(buffer, format="PNG")
        buffer.seek(0)
        await self.client.send_file(
            event.chat_id,
            buffer,
            caption="🔳 QR Code ساخته شد.",
            reply_to=getattr(event.message, "reply_to_msg_id", None),
        )
        self.feature_engine.mark_own_deletion(event)
        await event.delete()

    async def enable_analog_clock(self, event) -> None:
        if Image is None or ImageDraw is None:
            await self.safe_edit(event, "❌ Pillow روی سرور نصب نیست.")
            return
        await self.backup_profile("analog-clock")
        me = await self.client.get_me()
        payload = await self.client.download_profile_photo(me, file=bytes)
        if not payload:
            await self.safe_edit(event, "❌ ابتدا یک عکس پروفایل برای حساب قرار دهید.")
            return
        reference = await self._save_bytes_to_saved_messages(
            payload, filename="analog-clock-base.jpg",
            caption="🕒 تصویر پایه ساعت عقربه‌ای",
        )
        self.save_settings(
            {
                "analog_clock_enabled": "on",
                "analog_clock_base_path": reference,
            }
        )
        self.last_analog_clock_update = 0
        await self.update_analog_clock()
        self.last_analog_clock_enabled = True
        await self.safe_edit(
            event,
            "✅ ساعت عقربه‌ای عکس فعال شد و پروفایل قبلی بکاپ گرفت.",
        )

    async def analog_clock_loop(self) -> None:
        while self.account.is_running and not self.account.shutdown_requested:
            try:
                settings = self.settings()
                enabled = settings.get("analog_clock_enabled") == "on"
                if enabled:
                    minutes = self._bounded_int(
                        settings.get("analog_clock_update_minutes", "5"),
                        default=5,
                        minimum=1,
                        maximum=60,
                    )
                    if (
                        not self.last_analog_clock_enabled
                        or
                        time.monotonic() - self.last_analog_clock_update
                        >= minutes * 60
                    ):
                        await self.update_analog_clock()
                elif (
                    self.last_analog_clock_enabled
                    or settings.get("analog_clock_generated_photo_id", "")
                ):
                    await self.restore_analog_clock_photo()
                self.last_analog_clock_enabled = enabled
            except FloodWaitError as exc:
                await asyncio.sleep(
                    min(300, max(1, int(getattr(exc, "seconds", 60))))
                )
            except Exception as exc:
                self.record_error(
                    f"analog-clock {type(exc).__name__}: {exc}"
                )
            await asyncio.sleep(5)

    async def current_profile_photo(self):
        try:
            photos = await self.client.get_profile_photos("me", limit=1)
            return photos[0] if photos else None
        except Exception:
            return None

    async def delete_generated_profile_photo(self, expected_id: int) -> None:
        if not expected_id:
            return
        current = await self.current_profile_photo()
        if int(getattr(current, "id", 0) or 0) != int(expected_id):
            return
        await self.client(
            functions.photos.DeletePhotosRequest(
                id=[utils.get_input_photo(current)]
            )
        )

    async def restore_analog_clock_photo(self) -> None:
        """Remove the generated clock photo so Telegram reveals the original."""
        settings = self.settings()
        previous_id = int(
            settings.get("analog_clock_generated_photo_id", "0") or 0
        )
        if previous_id:
            photos = await self.client.get_profile_photos("me", limit=3)
            for photo in photos:
                if int(getattr(photo, "id", 0) or 0) == previous_id:
                    await self.client(
                        functions.photos.DeletePhotosRequest(
                            id=[utils.get_input_photo(photo)]
                        )
                    )
                    break
        self.save_settings(
            {
                "analog_clock_generated_photo_id": "",
                "analog_clock_base_path": "",
            }
        )

    async def update_analog_clock(self) -> None:
        if Image is None or ImageDraw is None:
            return
        settings = self.settings()
        reference = str(settings.get("analog_clock_base_path", "") or "")
        if not reference:
            me = await self.client.get_me()
            payload = await self.client.download_profile_photo(me, file=bytes)
            if not payload:
                self.save_settings({"analog_clock_enabled": "off"})
                return
            reference = await self._save_bytes_to_saved_messages(
                payload, filename="analog-clock-base.jpg",
                caption="🕒 تصویر پایه ساعت عقربه‌ای",
            )
            self.save_settings({"analog_clock_base_path": reference})
        base_buffer = await self._buffer_from_media_reference(
            reference, default_name="analog-clock-base.jpg"
        )
        output_buffer = await asyncio.to_thread(
            self.draw_analog_clock_bytes,
            base_buffer.getvalue(),
            datetime.now().astimezone(),
        )
        previous_generated_id = int(
            settings.get("analog_clock_generated_photo_id", "0") or 0
        )
        uploaded = await self.client.upload_file(output_buffer)
        response = await self.client(
            functions.photos.UploadProfilePhotoRequest(file=uploaded)
        )
        generated_id = int(getattr(getattr(response, "photo", None), "id", 0) or 0)
        if previous_generated_id and previous_generated_id != generated_id:
            photos = await self.client.get_profile_photos("me", limit=3)
            for photo in photos:
                if int(getattr(photo, "id", 0) or 0) == previous_generated_id:
                    await self.client(
                        functions.photos.DeletePhotosRequest(id=[utils.get_input_photo(photo)])
                    )
                    break
        self.save_settings({"analog_clock_generated_photo_id": str(generated_id or "")})
        self.last_analog_clock_update = time.monotonic()

    @staticmethod
    def draw_analog_clock_bytes(source: bytes, now: datetime) -> io.BytesIO:
        with Image.open(io.BytesIO(source)) as original:
            image = original.convert("RGB")
        draw = ImageDraw.Draw(image, "RGBA")
        size = min(image.size)
        radius = max(40, int(size * 0.16))
        margin = max(12, int(size * 0.035))
        center = (image.width - radius - margin, image.height - radius - margin)
        draw.ellipse(
            (center[0]-radius, center[1]-radius, center[0]+radius, center[1]+radius),
            fill=(255,255,255,205), outline=(20,20,20,230),
            width=max(2, radius//18),
        )
        for index in range(12):
            angle = math.radians(index * 30 - 90)
            outer = (center[0] + math.cos(angle) * radius * 0.86, center[1] + math.sin(angle) * radius * 0.86)
            inner = (center[0] + math.cos(angle) * radius * 0.72, center[1] + math.sin(angle) * radius * 0.72)
            draw.line((inner, outer), fill=(30,30,30,230), width=max(2, radius//22))
        minute_angle = math.radians(now.minute * 6 - 90)
        hour_angle = math.radians((now.hour % 12 + now.minute / 60) * 30 - 90)
        draw.line((center, (center[0]+math.cos(hour_angle)*radius*0.46, center[1]+math.sin(hour_angle)*radius*0.46)), fill=(15,15,15,255), width=max(4, radius//10))
        draw.line((center, (center[0]+math.cos(minute_angle)*radius*0.68, center[1]+math.sin(minute_angle)*radius*0.68)), fill=(190,20,20,255), width=max(3, radius//14))
        draw.ellipse((center[0]-radius*0.06, center[1]-radius*0.06, center[0]+radius*0.06, center[1]+radius*0.06), fill=(15,15,15,255))
        output = io.BytesIO()
        output.name = "analog-clock.jpg"
        image.save(output, format="JPEG", quality=92, optimize=True)
        output.seek(0)
        return output


    @staticmethod
    def _draw_hand(
        draw,
        center: tuple[float, float],
        angle: float,
        length: float,
        color: tuple[int, int, int, int],
        width: int,
    ) -> None:
        end = (
            center[0] + math.cos(angle) * length,
            center[1] + math.sin(angle) * length,
        )
        draw.line((center, end), fill=color, width=width)

    @staticmethod
    def _state(settings: dict[str, str], key: str) -> str:
        return "✅ فعال" if settings.get(key) == "on" else "❌ غیرفعال"

    @staticmethod
    def _bounded_int(
        value: Any,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(parsed, maximum))

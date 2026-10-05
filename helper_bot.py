"""Inline helper bot used to display and control every user's self-bot panel."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import secrets
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from telegram import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InputTextMessageContent,
    Update,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from control_store import (
    add_enemy_hostile_replies,
    add_auto_reply_response,
    add_friend_affection_replies,
    add_secretary_reply,
    add_word_filter,
    clear_message_archive,
    count_secretary_replies,
    create_auto_reply_rule,
    create_schedule_job,
    create_form_template,
    delete_first_comment_channel,
    delete_enemy_hostile_reply,
    delete_auto_reply_rule,
    delete_form_template,
    delete_friend_affection_reply,
    delete_secretary_reply,
    delete_tracked_profile,
    delete_word_filter,
    get_active_user,
    get_chatgpt_daily_usage,
    get_feature_counts,
    get_form_template,
    get_helper_config,
    get_runtime_metrics,
    get_self_settings,
    list_auto_reply_rules,
    list_enemies,
    list_enemy_hostile_replies,
    list_first_comment_channels,
    list_private_allowlist,
    list_friend_affection_replies,
    list_friends,
    list_form_templates,
    list_secretary_replies,
    list_schedule_jobs,
    list_tracked_profiles,
    list_word_filters,
    set_enemy,
    set_form_template_active,
    set_friend,
    set_private_allowlist_user,
    set_app_settings,
    set_self_setting,
    set_schedule_job_status,
    upsert_first_comment_channel,
    upsert_tracked_profile,
)


TOGGLE_LABELS = {
    "online_status": "همیشه آنلاین",
    "presence_emoji_enabled": "ایموجی آنلاین/آفلاین کنار نام",
    "presence_auto_detect": "تشخیص خودکار آنلاین/آفلاین",
    "typing_action": "اکشن تایپینگ",
    "secretary": "پاسخ عمومی منشی",
    "auto_reply": "سؤال‌وجواب‌های ثبت‌شده",
    "offline_reply_enabled": "پاسخ حالت آفلاین",
    "timename": "ساعت در نام",
    "timebio": "ساعت در بیو",
    "save_timed_photos": "ذخیره عکس زمان‌دار",
    "anti_delete_enabled": "ضدحذف پیام‌های عادی",
    "anti_delete_private": "ضدحذف پیوی",
    "anti_delete_groups": "ضدحذف گروه",
    "anti_delete_channels": "ضدحذف کانال",
    "scheduled_message_enabled": "ارسال زمان‌بندی‌شده",
    "force_join_private": "عضویت اجباری پیوی",
    "auto_read_private": "سین خودکار پیوی",
    "auto_read_groups": "سین خودکار گروه",
    "auto_reaction": "ری‌اکت خودکار",
    "relationship_reaction": "واکنش دوست/دشمن",
    "friend_affection_reply": "پاسخ صمیمی به دوست",
    "enemy_hostile_reply": "پاسخ خودکار به دشمن",
    "outgoing_signature_enabled": "امضای خودکار",
    "lock_links": "قفل لینک",
    "lock_forwards": "قفل فوروارد",
    "lock_photos": "قفل عکس",
    "lock_videos": "قفل ویدیو",
    "lock_gifs": "قفل گیف",
    "lock_stickers": "قفل استیکر",
    "lock_voice": "قفل ویس",
    "lock_files": "قفل فایل",
    "lock_polls": "قفل نظرسنجی",
    "word_filter_enabled": "فیلتر کلمات",
    "profile_monitor_enabled": "پایش پروفایل",
    "first_comment_enabled": "کامنت اول",
    "form_builder_enabled": "فرم‌ساز خودکار",
    "private_lock_enabled": "قفل کامل پیوی",
    "private_lock_delete_unknown": "حذف پیام ناشناس",
    "private_lock_warn_before_block": "هشدار قبل از بلاک",
    "anti_edit_private": "ضد ویرایش پیوی",
    "anti_edit_groups": "ضد ویرایش گروه",
    "welcome_enabled": "خوش‌آمدگویی",
    "goodbye_enabled": "خداحافظی",
    "analog_clock_enabled": "ساعت عقربه‌ای عکس",
}


def render_panel_html(text: str) -> str:
    """Escape panel text and turn `command` fragments into real code spans."""
    parts = re.split(r"(`[^`\n]+`)", str(text or ""))
    rendered = []
    for part in parts:
        if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
            rendered.append(f"<code>{html.escape(part[1:-1])}</code>")
        else:
            rendered.append(html.escape(part))
    return "".join(rendered)


def fit_photo_caption(text: str, limit: int = 1000) -> str:
    """Keep photo-panel captions within Telegram's 1024 character limit."""
    value = str(text or "")
    if len(value) <= limit:
        return value
    clipped = value[: limit - 62].rstrip()
    if clipped.count("`") % 2:
        clipped = clipped.rsplit("`", 1)[0].rstrip()
    return (
        f"{clipped}\n\n"
        "… ادامه متن در این صفحه خلاصه شد؛ دکمه‌ها همچنان فعال‌اند."
    )


def glass_button(text: str, owner_id: int, action: str, *, style=None):
    api_kwargs = {"style": style} if style else None
    return InlineKeyboardButton(
        text=text,
        callback_data=f"hp:{owner_id}:{action}",
        api_kwargs=api_kwargs,
    )


def link_button(text: str, url: str, *, style=None):
    api_kwargs = {"style": style} if style else None
    return InlineKeyboardButton(
        text=text,
        url=url,
        api_kwargs=api_kwargs,
    )


def copy_button(text: str, value: str):
    return InlineKeyboardButton(
        text=text,
        copy_text=CopyTextButton(text=value),
    )


def write_runtime_status(
    status_file: str | Path | None,
    status: str,
    detail: str | None = None,
    **extra,
) -> None:
    if not status_file:
        return

    status_path = Path(status_file)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "pid": os.getpid(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        **extra,
    }
    if detail:
        payload["detail"] = str(detail)

    temporary_path = status_path.with_suffix(status_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary_path, status_path)


class HelperPanelBot:
    def __init__(
        self,
        token: str,
        data_dir: str | Path,
        status_file: str | Path | None,
    ):
        self.token = token
        self.data_dir = Path(data_dir)
        self.users_db = self.data_dir / "users.db"
        self.status_file = Path(status_file) if status_file else None
        self.application = (
            Application.builder()
            .token(token)
            .post_init(self.post_init)
            .post_shutdown(self.post_shutdown)
            .build()
        )
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(
            CommandHandler("cancel", self.cancel_schedule_input)
        )
        self.application.add_handler(InlineQueryHandler(self.inline_panel))
        self.application.add_handler(
            CallbackQueryHandler(self.panel_callback, pattern=r"^hp:")
        )
        self.application.add_handler(
            MessageHandler(
                filters.ChatType.PRIVATE & ~filters.COMMAND,
                self.receive_schedule_input,
            )
        )

    async def post_init(self, application: Application) -> None:
        helper_user = await application.bot.get_me()
        if not helper_user.username:
            raise RuntimeError("بات هلپر نام کاربری ندارد.")
        if not helper_user.supports_inline_queries:
            raise RuntimeError(
                "Inline Mode بات هلپر در BotFather فعال نشده است."
            )

        set_app_settings(
            self.users_db,
            {
                "helper_username": helper_user.username,
                "helper_bot_id": helper_user.id,
                "helper_pid": os.getpid(),
            },
        )
        write_runtime_status(
            self.status_file,
            "ready",
            username=helper_user.username,
            bot_id=helper_user.id,
        )

    async def post_shutdown(self, application: Application) -> None:
        write_runtime_status(self.status_file, "stopped")

    async def start(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        payload = context.args[0] if context.args else ""
        match = re.fullmatch(
            r"(schedtext|schedtarget|schedinterval|schedcreate|secretaryqa|"
            r"secretaryfallback|offlinetext|offlinecooldown|"
            r"onlineemoji|offlineemoji|"
            r"formcreate|formintro|filteradd|friendadd|frienddel|"
            r"friendtext|enemyadd|enemydel|enemytext|profileadd|profiledel|"
            r"pmwarning|pmallowadd|pmallowdel|welcometext|goodbyetext|"
            r"actionduration|firstcomment|"
            r"firstcommentdel|watermark|signature|reaction)_(\d+)",
            payload,
        )
        if match:
            input_type = {
                "schedtext": "schedule_text",
                "schedtarget": "schedule_target",
                "schedinterval": "schedule_interval",
                "schedcreate": "schedule_create",
                "secretaryqa": "secretary_qa",
                "secretaryfallback": "secretary_fallback",
                "offlinetext": "offline_text",
                "offlinecooldown": "offline_cooldown",
                "onlineemoji": "online_emoji",
                "offlineemoji": "offline_emoji",
                "formcreate": "form_create",
                "formintro": "form_intro",
                "filteradd": "filter_add",
                "friendadd": "friend_add",
                "frienddel": "friend_del",
                "friendtext": "friend_text",
                "enemyadd": "enemy_add",
                "enemydel": "enemy_del",
                "enemytext": "enemy_text",
                "profileadd": "profile_add",
                "profiledel": "profile_del",
                "pmwarning": "private_warning",
                "pmallowadd": "private_allow_add",
                "pmallowdel": "private_allow_del",
                "welcometext": "welcome_text",
                "goodbyetext": "goodbye_text",
                "actionduration": "action_duration",
                "firstcomment": "first_comment",
                "firstcommentdel": "first_comment_del",
                "watermark": "watermark_text",
                "signature": "signature_text",
                "reaction": "reaction_emoji",
            }[match.group(1)]
            owner_id = int(match.group(2))
            if update.effective_user.id != owner_id:
                await update.effective_message.reply_text(
                    "❌ این لینک تنظیمات متعلق به حساب شما نیست."
                )
                return
            record = self.user_record(owner_id)
            if not record:
                await update.effective_message.reply_text(
                    "❌ سلف فعال این حساب پیدا نشد."
                )
                return
            prompts = {
                "schedule_text": (
                    "📝 متن پیام زمان‌بندی‌شده را بفرستید.\n\n"
                    "نمونه: میو\n"
                    "حداکثر طول متن ۳۵۰۰ نویسه است."
                ),
                "schedule_target": (
                    "👥 آیدی گروه مقصد را بفرستید.\n\n"
                    "نمونه عمومی: @MyGroup\n"
                    "نمونه خصوصی: -1001234567890\n\n"
                    "حساب سلف باید از قبل داخل گروه عضو باشد."
                ),
                "schedule_interval": (
                    "⏱ فاصله ارسال را برحسب دقیقه بفرستید.\n\n"
                    "نمونه: 5\n"
                    "مقدار مجاز از ۱ دقیقه تا ۷ روز است."
                ),
                "schedule_create": (
                    "⏰ مرحله ۱ — مقصد برنامه\n\n"
                    "آیدی مقصد را بفرستید؛ نمونه: @MyGroup یا "
                    "-1001234567890"
                ),
                "secretary_qa": (
                    "💬 مرحله ۱ — کلمات یا سؤال‌های محرک\n\n"
                    "هر عبارت را در یک خط جدا بفرستید؛ سپس می‌توانید چند "
                    "پاسخ متنی، عکس، ویدئو، ویس، استیکر یا فایل ثبت کنید.\n\n"
                    "نمونه:\nقیمت\nقیمت چنده\nهزینه"
                ),
                "secretary_fallback": (
                    "🤖 متن پاسخ عمومی منشی را بفرستید.\n\n"
                    "این متن فقط وقتی ارسال می‌شود که پیام کاربر با هیچ "
                    "سؤال‌وجواب یا فرم فعالی مطابقت نداشته باشد."
                ),
                "offline_text": (
                    "🌙 متنی را بفرستید که در حالت آفلاین به پیام خصوصی "
                    "کاربر پاسخ داده شود.\n\n"
                    "می‌توانید از {time} و {date} داخل متن استفاده کنید."
                ),
                "offline_cooldown": (
                    "⏳ فاصله تکرار پاسخ آفلاین برای هر کاربر را به دقیقه "
                    "بفرستید.\n\n"
                    "مقدار مجاز از ۱ دقیقه تا ۷ روز است."
                ),
                "online_emoji": (
                    "🟢 ایموجی حالت آنلاین را بفرستید.\n"
                    "نمونه: 🟢 یا ✅"
                ),
                "offline_emoji": (
                    "🔴 ایموجی حالت آفلاین را بفرستید.\n"
                    "نمونه: 🔴 یا 🌙"
                ),
                "form_create": (
                    "🧾 مرحله ۱ از ۳ — نام فرم\n\n"
                    "یک نام کوتاه بفرستید؛ نمونه: سفارش کفش"
                ),
                "form_intro": (
                    "📝 متن معرفی فرم‌ها را بفرستید.\n\n"
                    "این متن وقتی کاربر برای اولین بار پیام می‌دهد و هنوز "
                    "فرمی انتخاب نکرده، همراه با فهرست فرم‌های فعال "
                    "نمایش داده می‌شود."
                ),
                "filter_add": (
                    "🧹 مرحله ۱ از ۲ — عبارت‌های فیلتر\n\n"
                    "یک یا چند عبارت را بفرستید؛ برای افزودن گروهی هر "
                    "عبارت را در یک خط جدا قرار دهید."
                ),
                "friend_add": (
                    "💚 آیدی عددی کاربری را بفرستید که دوست محسوب شود."
                ),
                "friend_del": "💚 آیدی عددی دوست را برای حذف بفرستید.",
                "friend_text": (
                    "💞 متن‌های پاسخ به دوست را بفرستید.\n\n"
                    "برای افزودن گروهی، هر متن را در یک خط جدا بنویسید. "
                    "در هر بار تا ۵۰ متن و برای هر متن حداکثر ۵۰۰ نویسه "
                    "مجاز است."
                ),
                "enemy_add": (
                    "💢 آیدی عددی کاربری را بفرستید که دشمن محسوب شود."
                ),
                "enemy_del": "💢 آیدی عددی دشمن را برای حذف بفرستید.",
                "enemy_text": (
                    "💢 متن‌های پاسخ به دشمن را بفرستید.\n\n"
                    "برای افزودن گروهی، هر متن را در یک خط جدا بنویسید. "
                    "در هر بار تا ۵۰ متن و برای هر متن حداکثر ۵۰۰ نویسه "
                    "مجاز است."
                ),
                "profile_add": (
                    "👁 آیدی عددی کاربر را برای پایش تغییر نام، یوزرنیم، "
                    "بیو و عکس بفرستید."
                ),
                "profile_del": (
                    "👁 آیدی عددی کاربر را برای توقف پایش بفرستید."
                ),
                "private_warning": (
                    "🔐 متن هشدار قفل پیوی را بفرستید.\n\n"
                    "این متن پیش از بلاک برای کاربر ناشناس ارسال می‌شود."
                ),
                "private_allow_add": (
                    "✅ آیدی عددی کاربری را بفرستید که اجازه پیام خصوصی دارد."
                ),
                "private_allow_del": (
                    "➖ آیدی عددی کاربر مجاز را برای حذف بفرستید."
                ),
                "welcome_text": (
                    "👋 متن خوش‌آمد را بفرستید.\n\n"
                    "متغیرها: {name}، {id}، {username} و {chat}"
                ),
                "goodbye_text": (
                    "👋 متن خداحافظی را بفرستید.\n\n"
                    "متغیرها: {name}، {id}، {username} و {chat}"
                ),
                "action_duration": (
                    "🎭 مدت پیش‌فرض اکشن نمایشی را به ثانیه بفرستید.\n"
                    "مقدار مجاز: ۱ تا ۳۰۰ ثانیه"
                ),
                "first_comment": (
                    "💬 مرحله ۱ از ۳ — کانال کامنت اول\n\n"
                    "آیدی کانال را بفرستید؛ نمونه: @MyChannel"
                ),
                "first_comment_del": (
                    "🗑 شناسه کانال ثبت‌شده را دقیقاً مانند @MyChannel "
                    "یا آیدی عددی بفرستید."
                ),
                "watermark_text": (
                    "🖼 متن لوگوی روی عکس را بفرستید؛ حداکثر ۱۰۰ نویسه."
                ),
                "signature_text": (
                    "✍️ متن امضای انتهای پیام‌ها را بفرستید؛ "
                    "حداکثر ۳۰۰ نویسه."
                ),
                "reaction_emoji": (
                    "❤️ ایموجی ری‌اکت خودکار را بفرستید.\n"
                    "نمونه: ❤️ یا 👍"
                ),
            }
            context.user_data["panel_input"] = {
                "owner_id": owner_id,
                "input_type": input_type,
                "stage": 0,
                "values": {},
            }
            await update.effective_message.reply_text(prompts[input_type])
            return

        context.user_data.pop("schedule_input", None)
        context.user_data.pop("panel_input", None)
        await update.effective_message.reply_text(
            "🤖 این بات، هلپر پنل سلف است.\n\n"
            "برای نمایش پنل، با حسابی که سلف آن فعال شده عبارت «پنل» "
            "را در چت موردنظر ارسال کنید."
        )

    async def cancel_schedule_input(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        pending = (
            context.user_data.pop("panel_input", None)
            or context.user_data.pop("schedule_input", None)
        )
        if pending:
            await update.effective_message.reply_text(
                "✅ تنظیم نیمه‌کاره لغو شد."
            )
        else:
            await update.effective_message.reply_text(
                "تنظیم نیمه‌کاره‌ای وجود ندارد."
            )

    async def receive_schedule_input(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        pending = (
            context.user_data.get("panel_input")
            or context.user_data.get("schedule_input")
        )
        if not pending:
            return

        owner_id = int(pending.get("owner_id") or 0)
        if update.effective_user.id != owner_id:
            context.user_data.pop("schedule_input", None)
            context.user_data.pop("panel_input", None)
            return
        record = self.user_record(owner_id)
        if not record:
            context.user_data.pop("schedule_input", None)
            context.user_data.pop("panel_input", None)
            await update.effective_message.reply_text(
                "❌ سلف این حساب دیگر فعال نیست."
            )
            return

        raw = (
            update.effective_message.text
            or update.effective_message.caption
            or ""
        ).strip()
        input_type = str(pending.get("input_type") or "")
        stage = int(pending.get("stage") or 0)
        values = pending.setdefault("values", {})
        try:
            if input_type in {"text", "schedule_text"}:
                if not raw or len(raw) > 3500:
                    raise ValueError(
                        "متن باید بین ۱ تا ۳۵۰۰ نویسه باشد."
                    )
                key = "scheduled_message_text"
                normalized = raw
                notice = "✅ متن پیام زمان‌بندی‌شده ذخیره شد."
                target_page = "schedule"
            elif input_type in {"target", "schedule_target"}:
                normalized = self.normalize_schedule_target(raw)
                key = "scheduled_message_target"
                notice = f"✅ مقصد روی {normalized} ذخیره شد."
                target_page = "schedule"
            elif input_type in {"interval", "schedule_interval"}:
                translated = raw.translate(
                    str.maketrans(
                        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                        "01234567890123456789",
                    )
                )
                if not translated.isdigit():
                    raise ValueError("فاصله ارسال باید یک عدد صحیح باشد.")
                interval = int(translated)
                if not 1 <= interval <= 10080:
                    raise ValueError(
                        "فاصله ارسال باید بین ۱ تا ۱۰۰۸۰ دقیقه باشد."
                    )
                key = "scheduled_message_interval_minutes"
                normalized = str(interval)
                notice = f"✅ فاصله ارسال روی {interval} دقیقه ذخیره شد."
                target_page = "schedule"
            elif input_type == "schedule_create":
                if stage == 0:
                    values["target"] = self.normalize_schedule_target(raw)
                    pending["stage"] = 1
                    await update.effective_message.reply_text(
                        "⏰ مرحله ۲ — محتوای پیام\n\n"
                        "متن، عکس، ویدئو، ویس، استیکر یا فایل را بفرستید."
                    )
                    return
                if stage == 1:
                    payload = await self.extract_panel_payload(
                        update.effective_message,
                        str(record["phone"]),
                        "schedule",
                    )
                    values.update(payload)
                    pending["stage"] = 2
                    await update.effective_message.reply_text(
                        "⏰ مرحله ۳ — نوع زمان‌بندی\n\n"
                        "یکی از عددهای زیر را بفرستید:\n"
                        "1 — یک‌باره\n"
                        "2 — تکرار با فاصله دقیقه‌ای\n"
                        "3 — هر روز در ساعت مشخص\n"
                        "4 — هر هفته در روز و ساعت مشخص"
                    )
                    return
                if stage == 2:
                    kind_map = {
                        "1": "once",
                        "2": "interval",
                        "3": "daily",
                        "4": "weekly",
                    }
                    recurrence = kind_map.get(
                        raw.translate(
                            str.maketrans(
                                "۰۱۲۳۴۵۶۷۸۹",
                                "0123456789",
                            )
                        )
                    )
                    if not recurrence:
                        raise ValueError("نوع زمان‌بندی باید یکی از ۱ تا ۴ باشد.")
                    values["recurrence_type"] = recurrence
                    pending["stage"] = 3
                    prompt = {
                        "once": (
                            "تاریخ و ساعت را بفرستید.\n"
                            "نمونه: 2026-08-01 18:30"
                        ),
                        "interval": (
                            "فاصله تکرار را به دقیقه بفرستید.\n"
                            "مقدار مجاز: ۱ تا ۱۰۰۸۰"
                        ),
                        "daily": "ساعت روزانه را بفرستید؛ نمونه: 18:30",
                        "weekly": (
                            "شماره روز هفته را بفرستید:\n"
                            "۰ دوشنبه، ۱ سه‌شنبه، ۲ چهارشنبه، "
                            "۳ پنجشنبه، ۴ جمعه، ۵ شنبه، ۶ یکشنبه"
                        ),
                    }[recurrence]
                    await update.effective_message.reply_text(prompt)
                    return
                recurrence = str(values.get("recurrence_type") or "")
                now = datetime.now().astimezone()
                if stage == 3 and recurrence == "weekly":
                    translated = raw.translate(
                        str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
                    )
                    if translated not in {str(item) for item in range(7)}:
                        raise ValueError("شماره روز هفته باید از ۰ تا ۶ باشد.")
                    values["weekday"] = int(translated)
                    pending["stage"] = 4
                    await update.effective_message.reply_text(
                        "ساعت ارسال هفتگی را بفرستید؛ نمونه: 18:30"
                    )
                    return
                if stage in {3, 4}:
                    if recurrence == "once":
                        try:
                            parsed = datetime.strptime(
                                raw.translate(
                                    str.maketrans(
                                        "۰۱۲۳۴۵۶۷۸۹",
                                        "0123456789",
                                    )
                                ),
                                "%Y-%m-%d %H:%M",
                            ).replace(tzinfo=now.tzinfo)
                        except ValueError as exc:
                            raise ValueError(
                                "زمان باید مانند 2026-08-01 18:30 باشد."
                            ) from exc
                        if parsed <= now:
                            raise ValueError("زمان اجرا باید در آینده باشد.")
                        recurrence_value = ""
                        next_run = parsed
                    elif recurrence == "interval":
                        translated = raw.translate(
                            str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
                        )
                        if not translated.isdigit():
                            raise ValueError("فاصله باید عدد صحیح باشد.")
                        minutes = int(translated)
                        if not 1 <= minutes <= 10080:
                            raise ValueError(
                                "فاصله باید بین ۱ تا ۱۰۰۸۰ دقیقه باشد."
                            )
                        recurrence_value = str(minutes)
                        next_run = now + timedelta(minutes=minutes)
                    else:
                        hour, minute = self.parse_clock(raw)
                        candidate = now.replace(
                            hour=hour,
                            minute=minute,
                            second=0,
                            microsecond=0,
                        )
                        if recurrence == "daily":
                            if candidate <= now:
                                candidate += timedelta(days=1)
                            recurrence_value = f"{hour:02d}:{minute:02d}"
                        else:
                            weekday = int(values["weekday"])
                            days_ahead = (weekday - now.weekday()) % 7
                            candidate = (
                                now + timedelta(days=days_ahead)
                            ).replace(
                                hour=hour,
                                minute=minute,
                                second=0,
                                microsecond=0,
                            )
                            if candidate <= now:
                                candidate += timedelta(days=7)
                            recurrence_value = json.dumps(
                                {
                                    "weekday": weekday,
                                    "time": f"{hour:02d}:{minute:02d}",
                                },
                                ensure_ascii=False,
                            )
                        next_run = candidate
                    values["recurrence_value"] = recurrence_value
                    values["next_run_at"] = next_run.isoformat(
                        timespec="seconds"
                    )
                    pending["stage"] = 5
                    await update.effective_message.reply_text(
                        "⏰ مرحله آخر — حذف خودکار\n\n"
                        "اگر پیام بعد از مدتی حذف شود، تعداد دقیقه را بفرستید؛ "
                        "برای غیرفعال‌بودن عدد ۰ را بفرستید."
                    )
                    return
                translated = raw.translate(
                    str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
                )
                if not translated.isdigit():
                    raise ValueError("زمان حذف باید عدد صحیح باشد.")
                delete_after = int(translated)
                if not 0 <= delete_after <= 10080:
                    raise ValueError("زمان حذف باید بین ۰ تا ۱۰۰۸۰ دقیقه باشد.")
                job_id = create_schedule_job(
                    self.data_dir,
                    str(record["phone"]),
                    target=str(values["target"]),
                    message_type=str(values["message_type"]),
                    message_text=str(values.get("message_text") or ""),
                    media_path=str(values.get("media_path") or ""),
                    caption=str(values.get("caption") or ""),
                    recurrence_type=str(values["recurrence_type"]),
                    recurrence_value=str(values["recurrence_value"]),
                    next_run_at=str(values["next_run_at"]),
                    timezone_name=str(now.tzinfo or "local"),
                    delete_after_minutes=delete_after,
                )
                key = None
                normalized = None
                notice = f"✅ برنامه حرفه‌ای #{job_id} ثبت شد."
                target_page = "schedule"
            elif input_type == "secretary_qa":
                if stage == 0:
                    triggers = [
                        line.strip()
                        for line in raw.splitlines()
                        if line.strip()
                    ]
                    if len(triggers) == 1 and "/" in triggers[0]:
                        triggers = [
                            item.strip()
                            for item in triggers[0].split("/")
                            if item.strip()
                        ]
                    unique_triggers = []
                    seen = set()
                    for trigger in triggers:
                        marker = trigger.casefold()
                        if marker not in seen:
                            unique_triggers.append(trigger)
                            seen.add(marker)
                    if not unique_triggers:
                        raise ValueError("حداقل یک کلمه یا سؤال بفرستید.")
                    if len(unique_triggers) > 50:
                        raise ValueError(
                            "در هر بار حداکثر ۵۰ عبارت قابل ثبت است."
                        )
                    if any(len(item) > 100 for item in unique_triggers):
                        raise ValueError(
                            "هر عبارت باید حداکثر ۱۰۰ نویسه باشد."
                        )
                    values["triggers"] = unique_triggers
                    values["trigger_count"] = len(unique_triggers)
                    pending["stage"] = 1
                    await update.effective_message.reply_text(
                        "💬 مرحله ۲ — پاسخ اول\n\n"
                        f"برای {len(unique_triggers)} عبارت، یک متن، عکس، "
                        "ویدئو، ویس، استیکر یا فایل بفرستید. بعد از ثبت، "
                        "می‌توانید پاسخ‌های بیشتری اضافه کنید."
                    )
                    return
                payload = await self.extract_panel_payload(
                    update.effective_message,
                    str(record["phone"]),
                    "auto_reply",
                )
                rule_id = int(values.get("rule_id") or 0)
                if not rule_id:
                    rule_id = create_auto_reply_rule(
                        self.data_dir,
                        str(record["phone"]),
                        values.get("triggers") or [],
                        scope="private",
                        match_mode="contains",
                        cooldown_seconds=30,
                    )
                    values["rule_id"] = rule_id
                add_auto_reply_response(
                    self.data_dir,
                    str(record["phone"]),
                    rule_id,
                    response_type=str(payload["message_type"]),
                    content_text=str(payload.get("message_text") or ""),
                    media_path=str(payload.get("media_path") or ""),
                    caption=str(payload.get("caption") or ""),
                )
                set_self_setting(
                    self.data_dir,
                    str(record["phone"]),
                    "auto_reply",
                    "on",
                )
                pending["stage"] = 2
                values["response_count"] = (
                    int(values.get("response_count") or 0) + 1
                )
                await update.effective_message.reply_text(
                    f"✅ پاسخ شماره {values['response_count']} ذخیره شد.\n\n"
                    "پاسخ بعدی را بفرستید یا روی «پایان» بزنید.",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                glass_button(
                                    "✅ پایان و فعال‌سازی",
                                    owner_id,
                                    "autoreply.done",
                                    style="success",
                                )
                            ],
                            [
                                glass_button(
                                    "❌ لغو ادامه افزودن",
                                    owner_id,
                                    "autoreply.cancel",
                                    style="danger",
                                )
                            ],
                        ]
                    ),
                )
                return
            elif input_type == "secretary_fallback":
                if not 1 <= len(raw) <= 3500:
                    raise ValueError(
                        "متن منشی باید بین ۱ تا ۳۵۰۰ نویسه باشد."
                    )
                key = "secretary_fallback_text"
                normalized = raw
                notice = "✅ متن پاسخ عمومی منشی ذخیره شد."
                target_page = "secretary"
            elif input_type == "offline_text":
                if not 1 <= len(raw) <= 3500:
                    raise ValueError(
                        "متن آفلاین باید بین ۱ تا ۳۵۰۰ نویسه باشد."
                    )
                key = "offline_reply_text"
                normalized = raw
                notice = "✅ متن پاسخ حالت آفلاین ذخیره شد."
                target_page = "secretary"
            elif input_type == "offline_cooldown":
                translated = raw.translate(
                    str.maketrans(
                        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                        "01234567890123456789",
                    )
                )
                if not translated.isdigit():
                    raise ValueError("فاصله تکرار باید یک عدد صحیح باشد.")
                interval = int(translated)
                if not 1 <= interval <= 10080:
                    raise ValueError(
                        "فاصله تکرار باید بین ۱ تا ۱۰۰۸۰ دقیقه باشد."
                    )
                key = "offline_reply_cooldown_minutes"
                normalized = str(interval)
                notice = (
                    f"✅ فاصله تکرار پاسخ آفلاین روی {interval} دقیقه "
                    "ذخیره شد."
                )
                target_page = "secretary"
            elif input_type in {"online_emoji", "offline_emoji"}:
                if not 1 <= len(raw) <= 16 or any(
                    character.isspace() for character in raw
                ):
                    raise ValueError("یک ایموجی کوتاه و بدون فاصله بفرستید.")
                key = (
                    "online_name_emoji"
                    if input_type == "online_emoji"
                    else "offline_name_emoji"
                )
                normalized = raw
                notice = (
                    "✅ ایموجی آنلاین ذخیره شد."
                    if input_type == "online_emoji"
                    else "✅ ایموجی آفلاین ذخیره شد."
                )
                target_page = "profile_management"
            elif input_type == "form_create":
                if stage == 0:
                    if not 1 <= len(raw) <= 100:
                        raise ValueError(
                            "نام فرم باید بین ۱ تا ۱۰۰ نویسه باشد."
                        )
                    values["name"] = raw
                    pending["stage"] = 1
                    await update.effective_message.reply_text(
                        "🧾 مرحله ۲ از ۳ — کلمه شروع فرم\n\n"
                        "عبارتی را بفرستید که کاربر با ارسال آن فرم را "
                        "باز کند؛ نمونه: کفش"
                    )
                    return
                if stage == 1:
                    if not 1 <= len(raw) <= 100:
                        raise ValueError(
                            "کلمه شروع باید بین ۱ تا ۱۰۰ نویسه باشد."
                        )
                    values["trigger"] = raw
                    pending["stage"] = 2
                    await update.effective_message.reply_text(
                        "🧾 مرحله ۳ از ۳ — سؤال‌های فرم\n\n"
                        "هر سؤال را در یک خط جدا بفرستید. حداقل ۱ و "
                        "حداکثر ۸ سؤال مجاز است."
                    )
                    return
                questions = [
                    line.strip() for line in raw.splitlines() if line.strip()
                ]
                if not 1 <= len(questions) <= 8:
                    raise ValueError(
                        "تعداد سؤال‌ها باید بین ۱ تا ۸ مورد باشد."
                    )
                form_id = create_form_template(
                    self.data_dir,
                    str(record["phone"]),
                    str(values.get("name") or ""),
                    str(values.get("trigger") or ""),
                    questions,
                )
                set_self_setting(
                    self.data_dir,
                    str(record["phone"]),
                    "form_builder_enabled",
                    "on",
                )
                key = None
                normalized = None
                notice = (
                    f"✅ فرم #{form_id} ذخیره و فرم‌ساز خودکار فعال شد."
                )
                target_page = "forms"
            elif input_type == "form_intro":
                if not 1 <= len(raw) <= 2000:
                    raise ValueError(
                        "متن معرفی فرم باید بین ۱ تا ۲۰۰۰ نویسه باشد."
                    )
                key = "form_intro_text"
                normalized = raw
                notice = "✅ متن معرفی فرم‌ها ذخیره شد."
                target_page = "forms"
            elif input_type == "private_warning":
                if not 1 <= len(raw) <= 1000:
                    raise ValueError(
                        "متن هشدار باید بین ۱ تا ۱۰۰۰ نویسه باشد."
                    )
                key = "private_lock_warning_text"
                normalized = raw
                notice = "✅ متن هشدار قفل پیوی ذخیره شد."
                target_page = "security"
            elif input_type in {
                "private_allow_add",
                "private_allow_del",
            }:
                translated = raw.translate(
                    str.maketrans(
                        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                        "01234567890123456789",
                    )
                )
                if not translated.isdigit() or int(translated) <= 0:
                    raise ValueError("آیدی کاربر باید یک عدد مثبت باشد.")
                target_id = int(translated)
                set_private_allowlist_user(
                    self.data_dir,
                    str(record["phone"]),
                    target_id,
                    allowed=input_type == "private_allow_add",
                )
                key = None
                normalized = None
                notice = (
                    "✅ کاربر به فهرست مجاز افزوده شد."
                    if input_type == "private_allow_add"
                    else "✅ کاربر از فهرست مجاز حذف شد."
                )
                target_page = "security"
            elif input_type in {"welcome_text", "goodbye_text"}:
                if not 1 <= len(raw) <= 1000:
                    raise ValueError(
                        "متن باید بین ۱ تا ۱۰۰۰ نویسه باشد."
                    )
                try:
                    raw.format(
                        name="نام",
                        id=1,
                        username="@user",
                        chat="گروه",
                    )
                except (KeyError, ValueError) as exc:
                    raise ValueError(
                        "متغیر متن معتبر نیست؛ فقط {name}، {id}، "
                        "{username} و {chat} مجازند."
                    ) from exc
                key = (
                    "welcome_text"
                    if input_type == "welcome_text"
                    else "goodbye_text"
                )
                normalized = raw
                notice = (
                    "✅ متن خوش‌آمد ذخیره شد."
                    if input_type == "welcome_text"
                    else "✅ متن خداحافظی ذخیره شد."
                )
                target_page = "groups_new"
            elif input_type == "action_duration":
                translated = raw.translate(
                    str.maketrans(
                        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                        "01234567890123456789",
                    )
                )
                if not translated.isdigit() or not 1 <= int(translated) <= 300:
                    raise ValueError(
                        "مدت اکشن باید عددی بین ۱ تا ۳۰۰ ثانیه باشد."
                    )
                key = "action_default_duration"
                normalized = translated
                notice = "✅ مدت پیش‌فرض اکشن ذخیره شد."
                target_page = "messages"
            elif input_type == "filter_add":
                if stage == 0:
                    phrases = [
                        line.strip().lower()
                        for line in raw.splitlines()
                        if line.strip()
                    ]
                    phrases = list(dict.fromkeys(phrases))
                    if not phrases:
                        raise ValueError("حداقل یک عبارت فیلتر بفرستید.")
                    if len(phrases) > 50:
                        raise ValueError(
                            "در هر بار حداکثر ۵۰ فیلتر قابل ثبت است."
                        )
                    if any(len(item) > 200 for item in phrases):
                        raise ValueError(
                            "هر عبارت فیلتر باید حداکثر ۲۰۰ نویسه باشد."
                        )
                    values["phrases"] = phrases
                    pending["stage"] = 1
                    await update.effective_message.reply_text(
                        "🧹 مرحله ۲ از ۲ — عملیات فیلتر\n\n"
                        "یکی از این موارد را بفرستید:\n"
                        "delete یا حذف\nwarn یا اخطار\nmute یا سکوت\n"
                        "block یا بلاک"
                    )
                    return
                action = raw
                action = {
                    "حذف": "delete",
                    "اخطار": "warn",
                    "سکوت": "mute",
                    "بلاک": "block",
                }.get(action.lower(), action.lower())
                filter_ids = [
                    add_word_filter(
                        self.data_dir,
                        str(record["phone"]),
                        phrase,
                        action,
                    )
                    for phrase in values.get("phrases", [])
                ]
                key = None
                normalized = None
                notice = f"✅ {len(filter_ids)} فیلتر ذخیره شد."
                target_page = "moderation"
            elif input_type == "friend_text":
                inserted, skipped = add_friend_affection_replies(
                    self.data_dir,
                    str(record["phone"]),
                    raw.splitlines(),
                )
                key = None
                normalized = None
                notice = (
                    f"✅ {inserted} متن دوست ذخیره شد."
                    + (
                        f" {skipped} متن تکراری نادیده گرفته شد."
                        if skipped
                        else ""
                    )
                )
                target_page = "friend_replies"
            elif input_type == "enemy_text":
                inserted, skipped = add_enemy_hostile_replies(
                    self.data_dir,
                    str(record["phone"]),
                    raw.splitlines(),
                )
                key = None
                normalized = None
                notice = (
                    f"✅ {inserted} متن دشمن ذخیره شد."
                    + (
                        f" {skipped} متن تکراری نادیده گرفته شد."
                        if skipped
                        else ""
                    )
                )
                target_page = "enemy_replies"
            elif input_type in {
                "friend_add",
                "friend_del",
                "enemy_add",
                "enemy_del",
                "profile_add",
                "profile_del",
            }:
                translated = raw.translate(
                    str.maketrans(
                        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                        "01234567890123456789",
                    )
                )
                raw_ids = [
                    item.strip()
                    for item in re.split(r"[\s,،]+", translated)
                    if item.strip()
                ]
                if (
                    not raw_ids
                    or any(not item.isdigit() or int(item) <= 0 for item in raw_ids)
                ):
                    raise ValueError(
                        "آیدی‌ها باید عدد مثبت و هرکدام در یک خط باشند."
                    )
                if len(raw_ids) > 50:
                    raise ValueError(
                        "در هر بار حداکثر ۵۰ آیدی قابل ثبت است."
                    )
                target_ids = list(dict.fromkeys(map(int, raw_ids)))
                phone = str(record["phone"])
                if input_type == "friend_add":
                    for target_id in target_ids:
                        set_friend(
                            self.data_dir,
                            phone,
                            target_id,
                            enabled=True,
                        )
                    notice = f"✅ {len(target_ids)} کاربر به دوستان افزوده شد."
                    target_page = "relationships"
                elif input_type == "friend_del":
                    for target_id in target_ids:
                        set_friend(
                            self.data_dir,
                            phone,
                            target_id,
                            enabled=False,
                        )
                    notice = f"✅ {len(target_ids)} کاربر از دوستان حذف شد."
                    target_page = "relationships"
                elif input_type == "enemy_add":
                    for target_id in target_ids:
                        set_enemy(
                            self.data_dir,
                            phone,
                            target_id,
                            enabled=True,
                        )
                    notice = f"✅ {len(target_ids)} کاربر به دشمنان افزوده شد."
                    target_page = "relationships"
                elif input_type == "enemy_del":
                    for target_id in target_ids:
                        set_enemy(
                            self.data_dir,
                            phone,
                            target_id,
                            enabled=False,
                        )
                    notice = f"✅ {len(target_ids)} کاربر از دشمنان حذف شد."
                    target_page = "relationships"
                elif input_type == "profile_add":
                    if len(target_ids) != 1:
                        raise ValueError(
                            "برای پایش پروفایل در هر بار فقط یک آیدی بفرستید."
                        )
                    upsert_tracked_profile(
                        self.data_dir,
                        phone,
                        target_ids[0],
                    )
                    notice = "✅ کاربر به فهرست پایش افزوده شد."
                    target_page = "profiles"
                else:
                    if len(target_ids) != 1:
                        raise ValueError(
                            "برای حذف پایش در هر بار فقط یک آیدی بفرستید."
                        )
                    delete_tracked_profile(
                        self.data_dir,
                        phone,
                        target_ids[0],
                    )
                    notice = "✅ کاربر از فهرست پایش حذف شد."
                    target_page = "profiles"
                key = None
                normalized = None
            elif input_type == "first_comment":
                if stage == 0:
                    if not raw or len(raw) > 200:
                        raise ValueError("آیدی کانال معتبر نیست.")
                    values["chat_id"] = raw
                    pending["stage"] = 1
                    await update.effective_message.reply_text(
                        "💬 مرحله ۲ از ۳ — متن کامنت\n\n"
                        "متنی را بفرستید که زیر پست کانال ارسال شود."
                    )
                    return
                if stage == 1:
                    if not 1 <= len(raw) <= 1000:
                        raise ValueError(
                            "متن کامنت باید بین ۱ تا ۱۰۰۰ نویسه باشد."
                        )
                    values["comment_text"] = raw
                    pending["stage"] = 2
                    await update.effective_message.reply_text(
                        "💬 مرحله ۳ از ۳ — تأخیر\n\n"
                        "تأخیر ارسال را به ثانیه و فقط عددی بفرستید؛ "
                        "نمونه: 2"
                    )
                    return
                translated = raw.translate(
                        str.maketrans(
                            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                            "01234567890123456789",
                        )
                    )
                if not translated.isdigit():
                    raise ValueError("تأخیر باید عدد صحیح باشد.")
                delay = int(translated)
                upsert_first_comment_channel(
                    self.data_dir,
                    str(record["phone"]),
                    str(values.get("chat_id") or ""),
                    str(values.get("comment_text") or ""),
                    delay_seconds=delay,
                )
                key = None
                normalized = None
                notice = "✅ کانال کامنت اول ذخیره شد."
                target_page = "automation"
            elif input_type == "first_comment_del":
                delete_first_comment_channel(
                    self.data_dir,
                    str(record["phone"]),
                    raw,
                )
                key = None
                normalized = None
                notice = "✅ کانال از فهرست کامنت اول حذف شد."
                target_page = "automation"
            elif input_type == "watermark_text":
                if not 1 <= len(raw) <= 100:
                    raise ValueError(
                        "متن لوگو باید بین ۱ تا ۱۰۰ نویسه باشد."
                    )
                key = "watermark_text"
                normalized = raw
                notice = "✅ متن لوگوی تصویر ذخیره شد."
                target_page = "appearance"
            elif input_type == "signature_text":
                if not 1 <= len(raw) <= 300:
                    raise ValueError(
                        "متن امضا باید بین ۱ تا ۳۰۰ نویسه باشد."
                    )
                key = "outgoing_signature_text"
                normalized = raw
                notice = "✅ متن امضای خودکار ذخیره شد."
                target_page = "appearance"
            elif input_type == "reaction_emoji":
                if not 1 <= len(raw) <= 16 or any(
                    character.isspace() for character in raw
                ):
                    raise ValueError("یک ایموجی معتبر بفرستید.")
                key = "auto_reaction_emoji"
                normalized = raw
                notice = "✅ ایموجی ری‌اکت خودکار ذخیره شد."
                target_page = "automation"
            else:
                raise ValueError("نوع تنظیم ناشناخته است.")
        except ValueError as exc:
            await update.effective_message.reply_text(
                f"❌ {exc}\n\nمقدار درست را دوباره بفرستید."
            )
            return

        if key is not None:
            set_self_setting(
                self.data_dir,
                str(record["phone"]),
                key,
                normalized,
            )
        context.user_data.pop("schedule_input", None)
        context.user_data.pop("panel_input", None)
        text, keyboard = self.build_page(
            owner_id,
            record,
            target_page,
            notice,
        )
        await update.effective_message.reply_text(
            text,
            reply_markup=keyboard,
        )

    @staticmethod
    def normalize_schedule_target(value: str) -> str:
        raw = (value or "").strip()
        raw = re.sub(
            r"^https?://(?:www\.)?t\.me/",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip("/")
        normalized_digits = raw.translate(
            str.maketrans(
                "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                "01234567890123456789",
            )
        )
        if re.fullmatch(r"-?\d{5,20}", normalized_digits):
            return normalized_digits
        username = raw.lstrip("@")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", username):
            raise ValueError(
                "مقصد باید @username یا آیدی عددی معتبر گروه باشد."
            )
        return f"@{username}"

    @staticmethod
    def parse_clock(value: str) -> tuple[int, int]:
        normalized = str(value or "").strip().translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        )
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", normalized)
        if not match:
            raise ValueError("ساعت باید مانند 18:30 باشد.")
        hour = int(match.group(1))
        minute = int(match.group(2))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("ساعت واردشده معتبر نیست.")
        return hour, minute

    async def extract_panel_payload(
        self,
        message,
        phone: str,
        category: str,
    ) -> dict[str, str]:
        """Persist helper-uploaded media and return a Telethon-ready payload."""
        text = str(message.text or "").strip()
        caption = str(message.caption or "").strip()
        if text:
            if len(text) > 3500:
                raise ValueError("متن حداکثر ۳۵۰۰ نویسه است.")
            return {
                "message_type": "text",
                "message_text": text,
                "media_path": "",
                "caption": "",
            }

        attachment = None
        message_type = ""
        extension = ".bin"
        file_size = 0
        if message.photo:
            attachment = message.photo[-1]
            message_type = "photo"
            extension = ".jpg"
            file_size = int(getattr(attachment, "file_size", 0) or 0)
        elif message.video:
            attachment = message.video
            message_type = "video"
            extension = ".mp4"
            file_size = int(message.video.file_size or 0)
        elif message.voice:
            attachment = message.voice
            message_type = "voice"
            extension = ".ogg"
            file_size = int(message.voice.file_size or 0)
        elif message.sticker:
            attachment = message.sticker
            message_type = "sticker"
            extension = (
                ".webm"
                if getattr(message.sticker, "is_video", False)
                else ".tgs"
                if getattr(message.sticker, "is_animated", False)
                else ".webp"
            )
            file_size = int(message.sticker.file_size or 0)
        elif message.animation:
            attachment = message.animation
            message_type = "animation"
            extension = ".mp4"
            file_size = int(message.animation.file_size or 0)
        elif message.document:
            attachment = message.document
            message_type = "document"
            suffix = Path(message.document.file_name or "").suffix
            extension = suffix[:12] if suffix else ".bin"
            file_size = int(message.document.file_size or 0)
        else:
            raise ValueError(
                "متن، عکس، ویدئو، ویس، استیکر یا فایل بفرستید."
            )
        if file_size > 50 * 1024 * 1024:
            raise ValueError("حجم رسانه حداکثر ۵۰ مگابایت است.")
        # Keep only Telegram's cloud file id.  The self-bot downloads it
        # into memory at send time; no customer media is written to disk.
        file_id = str(getattr(attachment, "file_id", "") or "").strip()
        if not file_id:
            raise ValueError("شناسه ابری فایل از تلگرام دریافت نشد.")
        return {
            "message_type": message_type,
            "message_text": "",
            "media_path": f"botfile:{file_id}",
            "caption": caption[:1000],
        }

    def user_record(self, user_id: int):
        record = get_active_user(self.users_db, user_id)
        if not record or not int(record.get("is_active") or 0):
            return None
        return record

    @staticmethod
    async def panel_profile_photo_id(bot, user_id: int) -> str:
        """Return the newest Bot API profile-photo file id when accessible."""
        try:
            photos = await bot.get_user_profile_photos(user_id, limit=1)
            if not photos.photos:
                return ""
            sizes = photos.photos[0]
            if not sizes:
                return ""
            return sizes[-1].file_id
        except Exception:
            return ""

    @staticmethod
    def process_is_running(
        pid,
        expected_script: str = "self_bot.py",
    ) -> bool:
        if not pid:
            return False
        try:
            process = psutil.Process(int(pid))
            command = " ".join(process.cmdline())
            return (
                process.is_running()
                and process.status() != psutil.STATUS_ZOMBIE
                and expected_script in command
            )
        except (psutil.Error, OSError, TypeError, ValueError):
            return False

    async def inline_panel(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        inline_query = update.inline_query
        requested = (inline_query.query or "").strip().lower()
        if requested not in {"panel", "پنل", "menu", "منو"}:
            await inline_query.answer([], cache_time=0, is_personal=True)
            return

        owner_id = inline_query.from_user.id
        record = self.user_record(owner_id)
        if not record:
            result = InlineQueryResultArticle(
                id=f"inactive-{owner_id}",
                title="سلف فعالی برای این حساب پیدا نشد",
                description="ابتدا سلف را از ربات اصلی فعال کنید.",
                input_message_content=InputTextMessageContent(
                    "❌ سلف فعالی برای این حساب ثبت نشده است."
                ),
            )
            await inline_query.answer(
                [result],
                cache_time=0,
                is_personal=True,
            )
            return

        record = dict(record)
        record["panel_first_name"] = inline_query.from_user.first_name or ""
        record["panel_last_name"] = inline_query.from_user.last_name or ""
        record["panel_username"] = inline_query.from_user.username or ""
        text, keyboard = self.build_page(owner_id, record, "home")
        profile_photo_id = await self.panel_profile_photo_id(
            context.bot,
            owner_id,
        )
        if profile_photo_id:
            result = InlineQueryResultCachedPhoto(
                id=f"panel-photo-{owner_id}-{secrets.token_hex(4)}",
                photo_file_id=profile_photo_id,
                title="🎛 نمایش پنل سلف",
                description="پنل دکمه‌ای مدیریت حساب",
                caption=render_panel_html(fit_photo_caption(text)),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            result = InlineQueryResultArticle(
                id=f"panel-{owner_id}-{secrets.token_hex(4)}",
                title="🎛 نمایش پنل سلف",
                description="پنل دکمه‌ای مدیریت حساب",
                input_message_content=InputTextMessageContent(
                    render_panel_html(text),
                    parse_mode="HTML",
                ),
                reply_markup=keyboard,
            )
        await inline_query.answer(
            [result],
            cache_time=0,
            is_personal=True,
        )

    async def panel_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        try:
            _, owner_text, action = query.data.split(":", 2)
            owner_id = int(owner_text)
        except (AttributeError, TypeError, ValueError):
            await query.answer("داده دکمه معتبر نیست.", show_alert=True)
            return

        if query.from_user.id != owner_id:
            await query.answer(
                "این پنل فقط برای صاحب سلف قابل استفاده است.",
                show_alert=True,
            )
            return

        record = self.user_record(owner_id)
        if not record:
            await query.answer(
                "سلف این حساب فعال نیست.",
                show_alert=True,
            )
            return

        phone = str(record["phone"])
        notice = None
        page = action

        if action.startswith("toggle."):
            setting = action.removeprefix("toggle.")
            if setting not in TOGGLE_LABELS:
                await query.answer("تنظیم ناشناخته است.", show_alert=True)
                return
            settings = get_self_settings(self.data_dir, phone)
            new_value = "off" if settings.get(setting) == "on" else "on"
            if (
                setting == "scheduled_message_enabled"
                and new_value == "on"
                and (
                    not settings.get("scheduled_message_target", "").strip()
                    or not settings.get("scheduled_message_text", "").strip()
                )
            ):
                await query.answer(
                    "ابتدا متن پیام و گروه مقصد را تنظیم کنید.",
                    show_alert=True,
                )
                return
            set_self_setting(self.data_dir, phone, setting, new_value)
            state_text = "فعال" if new_value == "on" else "غیرفعال"
            notice = f"✅ {TOGGLE_LABELS[setting]} {state_text} شد."
            if (
                setting in {
                    "presence_emoji_enabled",
                    "presence_auto_detect",
                }
                and new_value == "on"
                and (
                    setting == "presence_auto_detect"
                    or settings.get("presence_auto_detect", "on") == "on"
                )
                and settings.get("online_status") == "on"
            ):
                set_self_setting(
                    self.data_dir,
                    phone,
                    "online_status",
                    "off",
                )
                notice += (
                    "\n✅ «همیشه آنلاین» خاموش شد تا وضعیت واقعی "
                    "آنلاین/آفلاین قابل تشخیص باشد."
                )
            page = self.page_for_setting(setting)

        elif action == "duration":
            settings = get_self_settings(self.data_dir, phone)
            durations = ["5", "10", "20", "30", "60"]
            current = settings.get("typing_duration", "5")
            try:
                next_value = durations[(durations.index(current) + 1) % len(durations)]
            except ValueError:
                next_value = durations[0]
            set_self_setting(
                self.data_dir,
                phone,
                "typing_duration",
                next_value,
            )
            notice = f"✅ مدت تایپینگ روی {next_value} ثانیه قرار گرفت."
            page = "secretary"

        elif action == "font":
            settings = get_self_settings(self.data_dir, phone)
            try:
                current_font = int(settings.get("font", "1"))
            except ValueError:
                current_font = 1
            next_font = 1 if current_font >= 10 else current_font + 1
            set_self_setting(self.data_dir, phone, "font", next_font)
            set_self_setting(
                self.data_dir,
                phone,
                "timename_font",
                next_font,
            )
            set_self_setting(
                self.data_dir,
                phone,
                "timebio_font",
                next_font,
            )
            notice = f"✅ فونت ساعت روی مدل {next_font} قرار گرفت."
            page = "appearance"

        elif action in {"namefont", "biofont"}:
            settings = get_self_settings(self.data_dir, phone)
            key = "timename_font" if action == "namefont" else "timebio_font"
            try:
                current_font = int(
                    settings.get(key, settings.get("font", "1"))
                )
            except ValueError:
                current_font = 1
            next_font = 1 if current_font >= 10 else current_font + 1
            set_self_setting(
                self.data_dir,
                phone,
                key,
                next_font,
            )
            notice = (
                f"✅ فونت ساعت "
                f"{'نام' if action == 'namefont' else 'بیو'} "
                f"روی مدل {next_font} قرار گرفت."
            )
            page = "profile_management"

        elif action == "language":
            settings = get_self_settings(self.data_dir, phone)
            next_language = (
                "en"
                if settings.get("panel_language", "fa") == "fa"
                else "fa"
            )
            set_self_setting(
                self.data_dir,
                phone,
                "panel_language",
                next_language,
            )
            notice = (
                "✅ Panel language changed to English."
                if next_language == "en"
                else "✅ زبان پنل فارسی شد."
            )
            page = "home"

        elif action == "textstyle":
            settings = get_self_settings(self.data_dir, phone)
            styles = [
                "none",
                "bold",
                "italic",
                "code",
                "strike",
                "underline",
                "spoiler",
            ]
            current = settings.get("outgoing_text_style", "none")
            try:
                next_value = styles[
                    (styles.index(current) + 1) % len(styles)
                ]
            except ValueError:
                next_value = styles[0]
            set_self_setting(
                self.data_dir,
                phone,
                "outgoing_text_style",
                next_value,
            )
            notice = f"✅ حالت متن روی {next_value} قرار گرفت."
            page = "appearance"

        elif action == "filteraction":
            settings = get_self_settings(self.data_dir, phone)
            actions = ["delete", "warn", "mute", "block"]
            current = settings.get("word_filter_action", "delete")
            try:
                next_value = actions[
                    (actions.index(current) + 1) % len(actions)
                ]
            except ValueError:
                next_value = actions[0]
            set_self_setting(
                self.data_dir,
                phone,
                "word_filter_action",
                next_value,
            )
            notice = f"✅ عملیات پیش‌فرض فیلتر روی {next_value} قرار گرفت."
            page = "moderation"

        elif action == "profileinterval":
            settings = get_self_settings(self.data_dir, phone)
            intervals = ["5", "10", "30", "60", "180", "360"]
            current = settings.get(
                "profile_monitor_interval_minutes",
                "10",
            )
            try:
                next_value = intervals[
                    (intervals.index(current) + 1) % len(intervals)
                ]
            except ValueError:
                next_value = intervals[0]
            set_self_setting(
                self.data_dir,
                phone,
                "profile_monitor_interval_minutes",
                next_value,
            )
            notice = f"✅ فاصله پایش روی {next_value} دقیقه قرار گرفت."
            page = "profiles"

        elif action == "ttsvoice":
            settings = get_self_settings(self.data_dir, phone)
            next_value = (
                "male"
                if settings.get("tts_voice", "female") == "female"
                else "female"
            )
            set_self_setting(
                self.data_dir,
                phone,
                "tts_voice",
                next_value,
            )
            notice = (
                "✅ صدای پیش‌فرض متن‌به‌ویس روی "
                f"{'مرد' if next_value == 'male' else 'زن'} قرار گرفت."
            )
            page = "tools"

        elif action == "antidelete.max":
            settings = get_self_settings(self.data_dir, phone)
            limits = ["10", "25", "50", "100", "200"]
            current = settings.get("anti_delete_max_mb", "50")
            try:
                next_value = limits[
                    (limits.index(current) + 1) % len(limits)
                ]
            except ValueError:
                next_value = limits[0]
            set_self_setting(
                self.data_dir,
                phone,
                "anti_delete_max_mb",
                next_value,
            )
            notice = f"✅ سقف هر رسانه روی {next_value} مگابایت قرار گرفت."
            page = "general"

        elif action == "antidelete.retention":
            settings = get_self_settings(self.data_dir, phone)
            periods = ["1", "3", "7", "14", "30"]
            current = settings.get("anti_delete_retention_days", "7")
            try:
                next_value = periods[
                    (periods.index(current) + 1) % len(periods)
                ]
            except ValueError:
                next_value = periods[0]
            set_self_setting(
                self.data_dir,
                phone,
                "anti_delete_retention_days",
                next_value,
            )
            notice = f"✅ نگهداری موقت روی {next_value} روز قرار گرفت."
            page = "general"

        elif action == "antidelete.clear":
            page = "antidelete_confirm"

        elif action == "antidelete.clear.confirm":
            deleted = clear_message_archive(self.data_dir, phone)
            notice = f"✅ آرشیو موقت پاک شد؛ {deleted} پیام حذف شد."
            page = "general"

        elif action.startswith("form.toggle."):
            try:
                form_id = int(action.rsplit(".", 1)[1])
            except ValueError:
                await query.answer("شناسه فرم معتبر نیست.", show_alert=True)
                return
            form = get_form_template(
                self.data_dir,
                phone,
                form_id,
            )
            if not form:
                await query.answer("فرم پیدا نشد.", show_alert=True)
                return
            enabled = not bool(int(form.get("is_active") or 0))
            set_form_template_active(
                self.data_dir,
                phone,
                form_id,
                enabled=enabled,
            )
            notice = (
                f"✅ فرم «{form['name']}» "
                f"{'فعال' if enabled else 'غیرفعال'} شد."
            )
            page = "forms"

        elif action.startswith("form.delete."):
            try:
                form_id = int(action.rsplit(".", 1)[1])
            except ValueError:
                await query.answer("شناسه فرم معتبر نیست.", show_alert=True)
                return
            deleted = delete_form_template(
                self.data_dir,
                phone,
                form_id,
            )
            notice = (
                "✅ فرم و سؤال‌هایش حذف شد."
                if deleted
                else "این فرم قبلاً حذف شده است."
            )
            page = "forms"

        elif action.startswith("reply.delete."):
            try:
                reply_id = int(action.rsplit(".", 1)[1])
            except ValueError:
                await query.answer(
                    "شناسه پاسخ منشی معتبر نیست.",
                    show_alert=True,
                )
                return
            deleted = delete_secretary_reply(
                self.data_dir,
                phone,
                reply_id,
            )
            notice = (
                "✅ سؤال و پاسخ منشی حذف شد."
                if deleted
                else "این سؤال و پاسخ قبلاً حذف شده است."
            )
            page = "secretary"

        elif action.startswith("friendreply.delete."):
            try:
                reply_id = int(action.rsplit(".", 1)[1])
            except ValueError:
                await query.answer(
                    "شناسه متن دوست معتبر نیست.",
                    show_alert=True,
                )
                return
            deleted = delete_friend_affection_reply(
                self.data_dir,
                phone,
                reply_id,
            )
            notice = (
                "✅ متن پاسخ دوست حذف شد."
                if deleted
                else "این متن قبلاً حذف شده است."
            )
            page = "friend_replies"

        elif action.startswith("enemyreply.delete."):
            try:
                reply_id = int(action.rsplit(".", 1)[1])
            except ValueError:
                await query.answer(
                    "شناسه متن دشمن معتبر نیست.",
                    show_alert=True,
                )
                return
            deleted = delete_enemy_hostile_reply(
                self.data_dir,
                phone,
                reply_id,
            )
            notice = (
                "✅ متن پاسخ دشمن حذف شد."
                if deleted
                else "این متن قبلاً حذف شده است."
            )
            page = "enemy_replies"

        elif action.startswith("filter.delete."):
            try:
                filter_id = int(action.rsplit(".", 1)[1])
            except ValueError:
                await query.answer(
                    "شناسه فیلتر معتبر نیست.",
                    show_alert=True,
                )
                return
            deleted = delete_word_filter(
                self.data_dir,
                phone,
                filter_id,
            )
            notice = (
                "✅ فیلتر حذف شد."
                if deleted
                else "این فیلتر قبلاً حذف شده است."
            )
            page = "moderation"

        elif action.startswith("profile.delete."):
            try:
                target_id = int(action.rsplit(".", 1)[1])
            except ValueError:
                await query.answer(
                    "آیدی کاربر معتبر نیست.",
                    show_alert=True,
                )
                return
            deleted = delete_tracked_profile(
                self.data_dir,
                phone,
                target_id,
            )
            notice = (
                "✅ پایش کاربر حذف شد."
                if deleted
                else "این کاربر قبلاً حذف شده است."
            )
            page = "profiles"

        elif action in {"autoreply.done", "autoreply.cancel"}:
            pending = context.user_data.get("panel_input") or {}
            response_count = int(
                (pending.get("values") or {}).get("response_count") or 0
            )
            if (
                pending.get("input_type") == "secretary_qa"
                and response_count > 0
            ):
                context.user_data.pop("panel_input", None)
                notice = (
                    f"✅ قانون چندپاسخی با {response_count} پاسخ فعال شد."
                    if action == "autoreply.done"
                    else "✅ افزودن پاسخ بیشتر متوقف شد؛ پاسخ‌های ذخیره‌شده فعال‌اند."
                )
            else:
                notice = "تنظیم پاسخ نیمه‌کاره‌ای وجود ندارد."
            page = "secretary"

        elif action.startswith("autoreply.delete."):
            try:
                rule_id = int(action.rsplit(".", 1)[1])
            except ValueError:
                await query.answer(
                    "شناسه پاسخ خودکار معتبر نیست.",
                    show_alert=True,
                )
                return
            deleted = delete_auto_reply_rule(
                self.data_dir,
                phone,
                rule_id,
            )
            notice = (
                "✅ قانون پاسخ چندپاسخی حذف شد."
                if deleted
                else "این قانون قبلاً حذف شده است."
            )
            page = "secretary"

        elif action.startswith("schedule."):
            parts = action.split(".")
            if len(parts) != 3:
                await query.answer("دستور برنامه معتبر نیست.", show_alert=True)
                return
            command, job_text = parts[1], parts[2]
            try:
                job_id = int(job_text)
            except ValueError:
                await query.answer("شناسه برنامه معتبر نیست.", show_alert=True)
                return
            status_map = {
                "pause": "paused",
                "resume": "active",
                "cancel": "cancelled",
            }
            if command not in status_map:
                await query.answer("عملیات برنامه معتبر نیست.", show_alert=True)
                return
            changed = set_schedule_job_status(
                self.data_dir,
                phone,
                job_id,
                status_map[command],
            )
            labels = {
                "pause": "متوقف",
                "resume": "فعال",
                "cancel": "لغو",
            }
            notice = (
                f"✅ برنامه #{job_id} {labels[command]} شد."
                if changed
                else "وضعیت این برنامه قابل تغییر نیست."
            )
            page = "schedule"

        valid_pages = {
            "home",
            "general",
            "secretary",
            "forms",
            "appearance",
            "schedule",
            "moderation",
            "automation",
            "relationships",
            "friend_replies",
            "enemy_replies",
            "profiles",
            "tools",
            "status",
            "security",
            "messages",
            "profile_management",
            "groups_new",
            "stats_new",
            "extras",
            "legacy",
            "guide",
            "guide_people",
            "guide_groups",
            "guide_tools",
            "guide_automation",
            "option_guide",
            "option_security",
            "option_presence",
            "option_messages",
            "option_groups",
            "option_storage",
            "antidelete_confirm",
        }
        if page not in valid_pages:
            await query.answer("صفحه پنل معتبر نیست.", show_alert=True)
            return

        text, keyboard = self.build_page(owner_id, record, page, notice)
        await query.answer()
        rendered_text = render_panel_html(text)
        try:
            await query.edit_message_text(
                text=rendered_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except BadRequest as exc:
            error_text = str(exc).lower()
            if "message is not modified" in error_text:
                return
            try:
                await query.edit_message_caption(
                    caption=render_panel_html(fit_photo_caption(text)),
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            except BadRequest as caption_exc:
                if "message is not modified" not in str(caption_exc).lower():
                    raise

    @staticmethod
    def page_for_setting(setting: str) -> str:
        if setting in {
            "online_status",
            "save_timed_photos",
            "anti_delete_enabled",
            "anti_delete_private",
            "anti_delete_groups",
            "anti_delete_channels",
            "force_join_private",
            "auto_read_private",
            "auto_read_groups",
        }:
            return "general"
        if setting.startswith("private_lock_") or setting.startswith(
            "anti_edit_"
        ):
            return "security"
        if setting in {"welcome_enabled", "goodbye_enabled"}:
            return "groups_new"
        if setting == "analog_clock_enabled":
            return "profile_management"
        if setting == "presence_emoji_enabled":
            return "profile_management"
        if setting == "presence_auto_detect":
            return "profile_management"
        if setting == "scheduled_message_enabled":
            return "schedule"
        if setting in {
            "typing_action",
            "secretary",
            "auto_reply",
            "offline_reply_enabled",
        }:
            return "secretary"
        if setting == "form_builder_enabled":
            return "forms"
        if setting.startswith("lock_") or setting == "word_filter_enabled":
            return "moderation"
        if setting in {
            "auto_reaction",
            "relationship_reaction",
            "first_comment_enabled",
        }:
            return "automation"
        if setting in {
            "friend_affection_reply",
            "enemy_hostile_reply",
        }:
            return "relationships"
        if setting == "profile_monitor_enabled":
            return "profiles"
        return "appearance"

    def build_page(
        self,
        owner_id: int,
        record,
        page: str,
        notice: str | None = None,
    ):
        phone = str(record["phone"])
        settings = get_self_settings(self.data_dir, phone)
        generated_at = datetime.now().strftime("%H:%M:%S")
        notice_text = f"{notice}\n\n" if notice else ""

        if page == "home":
            running = self.process_is_running(record.get("self_pid"))
            language = settings.get("panel_language", "fa")
            english = language == "en"
            display_name = " ".join(
                part
                for part in (
                    str(record.get("panel_first_name") or record.get("first_name") or "").strip(),
                    str(record.get("panel_last_name") or record.get("last_name") or "").strip(),
                )
                if part
            ) or ("User" if english else "کاربر")
            panel_username = str(
                record.get("panel_username") or record.get("username") or ""
            ).strip().lstrip("@")
            status = (
                ("🟢 Running" if running else "🔴 Stopped")
                if english
                else ("🟢 آنلاین" if running else "🔴 پردازش متوقف")
            )
            counts = get_feature_counts(self.data_dir, phone)
            if english:
                text = (
                    f"{notice_text}"
                    "✨ Self-bot Control Panel\n"
                    "━━━━━━━━━━━━━━\n\n"
                    f"👤 {display_name}\n"
                    f"🆔 Self-maker ID: `{owner_id}`\n"
                    f"🔗 Username: @{panel_username or 'not-set'}\n\n"
                    f"🤖 Self-bot: {status}\n"
                    f"🔐 Private lock: "
                    f"{self.state_en(settings, 'private_lock_enabled')}\n"
                    f"✏️ Anti-edit PM: "
                    f"{self.state_en(settings, 'anti_edit_private')} | "
                    f"Groups: {self.state_en(settings, 'anti_edit_groups')}\n"
                    f"👋 Welcome: "
                    f"{self.state_en(settings, 'welcome_enabled')}\n\n"
                    "📊 Quick summary\n"
                    f"• Allowed users: {counts['private_allowlist']}\n"
                    f"• Pending one-time sends: {counts['scheduled_once']}\n"
                    f"• Recorded edits: {counts['message_edits']}\n"
                    f"• Anti-delete archive: {counts['archive']}\n\n"
                    f"🕒 Updated: {generated_at}"
                )
            else:
                text = (
                    f"{notice_text}"
                    "✨ پنل مدیریت سلف\n"
                    "━━━━━━━━━━━━━━\n\n"
                    f"👤 {display_name}\n"
                    f"🆔 آیدی سلف‌ساز: `{owner_id}`\n"
                    f"🔗 نام کاربری: @{panel_username or 'ثبت‌نشده'}\n\n"
                    f"🤖 وضعیت سلف: {status}\n"
                    f"🔐 قفل پیوی: "
                    f"{self.state(settings, 'private_lock_enabled')}\n"
                    f"✏️ ضد ویرایش پیوی: "
                    f"{self.state(settings, 'anti_edit_private')} | "
                    f"گروه: {self.state(settings, 'anti_edit_groups')}\n"
                    f"👋 خوش‌آمد: "
                    f"{self.state(settings, 'welcome_enabled')}\n\n"
                    "📊 خلاصه حساب\n"
                    f"• افراد مجاز پیوی: {counts['private_allowlist']} نفر\n"
                    f"• ارسال یک‌باره در انتظار: {counts['scheduled_once']}\n"
                    f"• ویرایش‌های ثبت‌شده: {counts['message_edits']}\n"
                    f"• آرشیو ضدحذف: {counts['archive']} پیام\n\n"
                    f"🕒 بروزرسانی: {generated_at}"
                )
            labels = (
                {
                    "status": "📊 Status",
                    "messages": "💬 Messages",
                    "security": "🔐 Security",
                    "profile": "👤 Profile",
                    "groups": "👥 Groups",
                    "more": "⚙️ More settings",
                }
                if english
                else {
                    "status": "📊 وضعیت و آمار",
                    "messages": "💬 پیام‌ها و منشی",
                    "security": "🔐 امنیت",
                    "profile": "👤 پروفایل",
                    "groups": "👥 گروه‌ها",
                    "more": "⚙️ سایر تنظیمات",
                }
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        glass_button(
                            labels["status"],
                            owner_id,
                            "stats_new",
                            style="success",
                        ),
                        glass_button(
                            labels["messages"],
                            owner_id,
                            "messages",
                            style="primary",
                        ),
                    ],
                    [
                        glass_button(
                            labels["security"],
                            owner_id,
                            "security",
                            style="danger",
                        ),
                        glass_button(
                            labels["profile"],
                            owner_id,
                            "profile_management",
                            style="primary",
                        ),
                    ],
                    [
                        glass_button(
                            labels["groups"],
                            owner_id,
                            "groups_new",
                            style="success",
                        ),
                        glass_button(
                            labels["more"],
                            owner_id,
                            "legacy",
                            style="primary",
                        ),
                    ],
                ]
            )
            return text, keyboard

        if page == "security":
            english = settings.get("panel_language", "fa") == "en"
            allowed = list_private_allowlist(
                self.data_dir,
                phone,
                limit=100,
            )
            warning = settings.get(
                "private_lock_warning_text",
                "",
            ).replace("\n", " ")[:220]
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            if english:
                text = (
                    f"{notice_text}"
                    "🔐 Security and anti-edit\n\n"
                    f"Private lock: "
                    f"{self.state_en(settings, 'private_lock_enabled')}\n"
                    f"Delete unknown messages: "
                    f"{self.state_en(settings, 'private_lock_delete_unknown')}\n"
                    f"Warn before block: "
                    f"{self.state_en(settings, 'private_lock_warn_before_block')}\n"
                    f"Allowed users: {len(allowed)}\n"
                    f"Anti-edit in private chats: "
                    f"{self.state_en(settings, 'anti_edit_private')}\n"
                    f"Anti-edit in groups: "
                    f"{self.state_en(settings, 'anti_edit_groups')}\n\n"
                    f"Warning text: {warning or 'Not configured'}"
                )
            else:
                text = (
                    f"{notice_text}"
                    "🔐 امنیت و ضد ویرایش\n\n"
                    f"قفل کامل پیوی: "
                    f"{self.state(settings, 'private_lock_enabled')}\n"
                    f"حذف پیام ناشناس: "
                    f"{self.state(settings, 'private_lock_delete_unknown')}\n"
                    f"هشدار قبل بلاک: "
                    f"{self.state(settings, 'private_lock_warn_before_block')}\n"
                    f"افراد مجاز: {len(allowed)} نفر\n"
                    f"ضد ویرایش پیوی: "
                    f"{self.state(settings, 'anti_edit_private')}\n"
                    f"ضد ویرایش گروه: "
                    f"{self.state(settings, 'anti_edit_groups')}\n\n"
                    f"متن هشدار: {warning or 'تنظیم نشده'}"
                )
            rows = [
                [
                    self.toggle_button_locale(
                        owner_id,
                        "private_lock_enabled",
                        settings,
                        english,
                    )
                ],
                [
                    self.toggle_button_locale(
                        owner_id,
                        "private_lock_delete_unknown",
                        settings,
                        english,
                    ),
                    self.toggle_button_locale(
                        owner_id,
                        "private_lock_warn_before_block",
                        settings,
                        english,
                    ),
                ],
                [
                    self.toggle_button_locale(
                        owner_id,
                        "anti_edit_private",
                        settings,
                        english,
                    ),
                    self.toggle_button_locale(
                        owner_id,
                        "anti_edit_groups",
                        settings,
                        english,
                    ),
                ],
            ]
            if setup_base:
                rows.extend(
                    [
                        [
                            link_button(
                                "✍️ Warning text"
                                if english
                                else "✍️ متن هشدار",
                                f"{setup_base}pmwarning_{owner_id}",
                                style="primary",
                            )
                        ],
                        [
                            link_button(
                                "➕ Allow user"
                                if english
                                else "➕ افزودن فرد مجاز",
                                f"{setup_base}pmallowadd_{owner_id}",
                                style="success",
                            ),
                            link_button(
                                "➖ Remove user"
                                if english
                                else "➖ حذف فرد مجاز",
                                f"{setup_base}pmallowdel_{owner_id}",
                                style="danger",
                            ),
                        ],
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔙 Back" if english else "🔙 بازگشت",
                        owner_id,
                        "home",
                        style="primary",
                    )
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "messages":
            english = settings.get("panel_language", "fa") == "en"
            duration = settings.get("action_default_duration", "5")
            pending = get_feature_counts(
                self.data_dir,
                phone,
            )["scheduled_once"]
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            text = (
                f"{notice_text}"
                + (
                    "💬 Messages and display actions\n\n"
                    "Commands:\n"
                    "• `search text` — search current chat\n"
                    "• `message info` — show message/user/chat IDs\n"
                    "• `save message` — save the replied message\n"
                    "• `download message <link or id>`\n"
                    "• `send once 18:30 | text`\n"
                    "• `action typing 10`, voice, video, photo, file, "
                    "sticker, or game\n\n"
                    f"Default action duration: {duration}s\n"
                    f"Pending one-time sends: {pending}"
                    if english
                    else
                    "💬 پیام‌ها و اکشن‌های نمایشی\n\n"
                    "دستورها:\n"
                    "• `جستجو متن` — جست‌وجو در چت فعلی\n"
                    "• `شناسه پیام` — آیدی پیام، کاربر و گروه\n"
                    "• `ذخیره پیام` — ذخیره پیام ریپلای‌شده\n"
                    "• `دانلود پیام لینک یا آیدی`\n"
                    "• `ارسال یکباره 18:30 | متن`\n"
                    "• `اکشن تایپ 10`؛ ویس، ویدیو، عکس، فایل، "
                    "استیکر یا بازی\n\n"
                    f"مدت پیش‌فرض اکشن: {duration} ثانیه\n"
                    f"ارسال‌های یک‌باره در انتظار: {pending}"
                )
            )
            rows = []
            if setup_base:
                rows.append(
                    [
                        link_button(
                            "🎭 Action duration"
                            if english
                            else "🎭 مدت پیش‌فرض اکشن",
                            f"{setup_base}actionduration_{owner_id}",
                            style="primary",
                        )
                    ]
                )
            rows.extend(
                [
                    [
                        glass_button(
                            "⏰ Scheduled messages"
                            if english
                            else "⏰ ارسال‌های زمان‌بندی‌شده",
                            owner_id,
                            "schedule",
                            style="success",
                        ),
                        glass_button(
                            "🗑 Anti-delete"
                            if english
                            else "🗑 ضدحذف",
                            owner_id,
                            "general",
                            style="primary",
                        ),
                    ],
                    [
                        glass_button(
                            "🔙 Back" if english else "🔙 بازگشت",
                            owner_id,
                            "home",
                            style="primary",
                        )
                    ],
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "profile_management":
            english = settings.get("panel_language", "fa") == "en"
            name_font = settings.get(
                "timename_font",
                settings.get("font", "1"),
            )
            bio_font = settings.get(
                "timebio_font",
                settings.get("font", "1"),
            )
            online_emoji = settings.get("online_name_emoji", "🟢")
            offline_emoji = settings.get("offline_name_emoji", "🔴")
            detected_state = settings.get("presence_last_state", "unknown")
            detected_labels = {
                "online": "🟢 آنلاین",
                "offline": "🔴 آفلاین",
                "unknown": "⚪ هنوز تشخیص داده نشده",
            }
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            text = (
                f"{notice_text}"
                + (
                    "👤 Profile management\n\n"
                    "Commands:\n"
                    "• `first name New name`\n"
                    "• `last name New family name`\n"
                    "• `bio New bio`\n"
                    "• Reply to a photo: `profile photo`\n"
                    "• Reply to a user: `copy profile`\n"
                    "• `restore profile`\n"
                    "• `photo clock on/off`\n\n"
                    f"Name clock font: {name_font}\n"
                    f"Bio clock font: {bio_font}\n"
                    f"Online emoji: {online_emoji} | "
                    f"Offline emoji: {offline_emoji}\n"
                    "Automatic detection follows Telegram presence when "
                    "Always Online is disabled."
                    if english
                    else
                    "👤 مدیریت پروفایل\n\n"
                    "دستورها:\n"
                    "• `نام متن جدید`\n"
                    "• `نام خانوادگی متن جدید`\n"
                    "• `بیو متن جدید`\n"
                    "• روی عکس: `عکس پروفایل`\n"
                    "• روی پیام فرد: `کپی پروفایل`\n"
                    "• `بازیابی پروفایل`\n"
                    "• `ساعت عکس روشن/خاموش`\n\n"
                    f"فونت ساعت نام: {name_font}\n"
                    f"فونت ساعت بیو: {bio_font}\n"
                    f"ایموجی آنلاین: {online_emoji} | "
                    f"ایموجی آفلاین: {offline_emoji}\n"
                    f"وضعیت تشخیص‌داده‌شده: "
                    f"{detected_labels.get(detected_state, detected_state)}\n\n"
                    "تشخیص خودکار، وضعیت واقعی تلگرام را بررسی می‌کند. "
                    "اگر «همیشه آنلاین» روشن باشد، وضعیت همیشه آنلاین است."
                )
            )
            rows = [
                [
                    self.toggle_button_locale(
                        owner_id,
                        "timename",
                        settings,
                        english,
                    ),
                    self.toggle_button_locale(
                        owner_id,
                        "timebio",
                        settings,
                        english,
                    ),
                ],
                [
                    self.toggle_button_locale(
                        owner_id,
                        "analog_clock_enabled",
                        settings,
                        english,
                    )
                ],
                [
                    self.toggle_button_locale(
                        owner_id,
                        "presence_emoji_enabled",
                        settings,
                        english,
                    )
                ],
                [
                    self.toggle_button_locale(
                        owner_id,
                        "presence_auto_detect",
                        settings,
                        english,
                    )
                ],
                [
                    glass_button(
                        (
                            f"🔤 Name font: {name_font}"
                            if english
                            else f"🔤 فونت نام: {name_font}"
                        ),
                        owner_id,
                        "namefont",
                        style="primary",
                    ),
                    glass_button(
                        (
                            f"🔤 Bio font: {bio_font}"
                            if english
                            else f"🔤 فونت بیو: {bio_font}"
                        ),
                        owner_id,
                        "biofont",
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "🎨 Text appearance"
                        if english
                        else "🎨 ظاهر متن و پروفایل",
                        owner_id,
                        "appearance",
                        style="primary",
                    ),
                    glass_button(
                        "👁 Profile monitor"
                        if english
                        else "👁 پایش پروفایل",
                        owner_id,
                        "profiles",
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "🔙 Back" if english else "🔙 بازگشت",
                        owner_id,
                        "home",
                        style="primary",
                    )
                ],
            ]
            if setup_base:
                rows.insert(
                    -1,
                    [
                        link_button(
                            "🟢 Online emoji"
                            if english
                            else "🟢 ایموجی آنلاین",
                            f"{setup_base}onlineemoji_{owner_id}",
                            style="primary",
                        ),
                        link_button(
                            "🔴 Offline emoji"
                            if english
                            else "🔴 ایموجی آفلاین",
                            f"{setup_base}offlineemoji_{owner_id}",
                            style="primary",
                        ),
                    ],
                )
            return text, InlineKeyboardMarkup(rows)

        if page == "groups_new":
            english = settings.get("panel_language", "fa") == "en"
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            text = (
                f"{notice_text}"
                + (
                    "👥 Group management\n\n"
                    "Available commands:\n"
                    "• Reply: `pin`, `unpin`, `kick`, `mute 10`, `unmute`\n"
                    "• Reply: `report admins`\n"
                    "• Word and link filters are available in moderation.\n"
                    "Welcome variables: {name}, {id}, {username}, {chat}"
                    if english
                    else
                    "👥 مدیریت گروه\n\n"
                    "دستورهای قابل استفاده:\n"
                    "• با ریپلای: `پین`، `آنپین`، `اخراج`، "
                    "`سکوت 10` و `رفع سکوت`\n"
                    "• با ریپلای: `گزارش مدیران`\n"
                    "• فیلتر کلمات و لینک در بخش قفل و فیلتر قرار دارد.\n"
                    "متغیرهای خوش‌آمد: {name}، {id}، {username} و {chat}"
                )
            )
            rows = [
                [
                    self.toggle_button_locale(
                        owner_id,
                        "welcome_enabled",
                        settings,
                        english,
                    ),
                    self.toggle_button_locale(
                        owner_id,
                        "goodbye_enabled",
                        settings,
                        english,
                    ),
                ]
            ]
            if setup_base:
                rows.append(
                    [
                        link_button(
                            "👋 Welcome text"
                            if english
                            else "👋 متن خوش‌آمد",
                            f"{setup_base}welcometext_{owner_id}",
                            style="primary",
                        ),
                        link_button(
                            "👋 Goodbye text"
                            if english
                            else "👋 متن خداحافظی",
                            f"{setup_base}goodbyetext_{owner_id}",
                            style="primary",
                        ),
                    ]
                )
            rows.extend(
                [
                    [
                        glass_button(
                            "🛡 Filters and locks"
                            if english
                            else "🛡 قفل و فیلتر",
                            owner_id,
                            "moderation",
                            style="danger",
                        ),
                        glass_button(
                            "⚡ Automation"
                            if english
                            else "⚡ اتوماسیون",
                            owner_id,
                            "automation",
                            style="success",
                        ),
                    ],
                    [
                        glass_button(
                            "🔙 Back" if english else "🔙 بازگشت",
                            owner_id,
                            "home",
                            style="primary",
                        )
                    ],
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "stats_new":
            english = settings.get("panel_language", "fa") == "en"
            counts = get_feature_counts(self.data_dir, phone)
            metrics = get_runtime_metrics(self.data_dir, phone)
            usage = get_chatgpt_daily_usage(
                self.data_dir,
                phone,
                datetime.now().date().isoformat(),
            )
            running = self.process_is_running(record.get("self_pid"))
            helper = get_helper_config(self.users_db)
            helper_running = self.process_is_running(
                helper.get("pid"),
                "helper_bot.py",
            )
            text = (
                f"{notice_text}"
                + (
                    "📊 Account statistics\n\n"
                    f"Self-bot: {'🟢 Running' if running else '🔴 Stopped'}\n"
                    f"Helper: {'🟢 Running' if helper_running else '🔴 Stopped'}\n"
                    f"Allowed PM users: {counts['private_allowlist']}\n"
                    f"Recorded edits: {counts['message_edits']}\n"
                    f"Pending one-time sends: {counts['scheduled_once']}\n"
                    f"Profile backups: {counts['profile_backups']}\n"
                    f"ChatGPT today: {usage['request_count']} requests, "
                    f"{usage['input_tokens'] + usage['output_tokens']} tokens\n"
                    f"Last error: {metrics.get('last_error', 'None')[:180]}\n\n"
                    "Send `account stats` in Telegram for live PM, group, "
                    "channel, and unread-message counts."
                    if english
                    else
                    "📊 آمار حساب\n\n"
                    f"سلف: {'🟢 فعال' if running else '🔴 متوقف'}\n"
                    f"هلپر: {'🟢 فعال' if helper_running else '🔴 متوقف'}\n"
                    f"افراد مجاز پیوی: {counts['private_allowlist']}\n"
                    f"ویرایش‌های ثبت‌شده: {counts['message_edits']}\n"
                    f"ارسال یک‌باره در انتظار: {counts['scheduled_once']}\n"
                    f"بکاپ‌های پروفایل: {counts['profile_backups']}\n"
                    f"ChatGPT امروز: {usage['request_count']} درخواست، "
                    f"{usage['input_tokens'] + usage['output_tokens']} توکن\n"
                    f"آخرین خطا: "
                    f"{metrics.get('last_error', 'ثبت نشده')[:180]}\n\n"
                    "برای شمارش زنده پیوی، گروه، کانال و خوانده‌نشده‌ها "
                    "داخل تلگرام دستور `آمار حساب` را بفرستید."
                )
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        glass_button(
                            "🔄 Refresh" if english else "🔄 بروزرسانی",
                            owner_id,
                            "stats_new",
                            style="success",
                        ),
                        glass_button(
                            "🩺 Process status"
                            if english
                            else "🩺 وضعیت پردازش",
                            owner_id,
                            "status",
                            style="primary",
                        ),
                    ],
                    [
                        glass_button(
                            "🔙 Back" if english else "🔙 بازگشت",
                            owner_id,
                            "home",
                            style="primary",
                        )
                    ],
                ]
            )
            return text, keyboard

        if page == "extras":
            english = settings.get("panel_language", "fa") == "en"
            text = (
                f"{notice_text}"
                + (
                    "🧰 Extra tools\n\n"
                    "• `qr your text`\n"
                    "• `photo clock on/off`\n"
                    "• `dice`, `casino`, coin flip, random number, picker, "
                    "rock-paper-scissors, and tic-tac-toe\n"
                    "• Media, translation, TTS, prices, and calculator remain "
                    "available on the tools page."
                    if english
                    else
                    "🧰 ابزارهای جانبی\n\n"
                    "• `کیوآر متن دلخواه`\n"
                    "• `ساعت عکس روشن/خاموش`\n"
                    "• تاس، کازینو، شیر یا خط، عدد تصادفی، انتخاب، "
                    "سنگ‌کاغذقیچی و دوز\n"
                    "• رسانه، ترجمه، متن‌به‌ویس، قیمت و ماشین‌حساب در "
                    "صفحه ابزار و رسانه در دسترس‌اند."
                )
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        glass_button(
                            "🧰 Media tools"
                            if english
                            else "🧰 ابزار و رسانه",
                            owner_id,
                            "tools",
                            style="primary",
                        ),
                        glass_button(
                            "📚 Tool commands"
                            if english
                            else "📚 دستورهای ابزار",
                            owner_id,
                            "guide_tools",
                            style="primary",
                        ),
                    ],
                    [
                        glass_button(
                            "⏰ Schedules"
                            if english
                            else "⏰ زمان‌بندی",
                            owner_id,
                            "schedule",
                            style="success",
                        ),
                        glass_button(
                            "⚡ Automation"
                            if english
                            else "⚡ اتوماسیون",
                            owner_id,
                            "automation",
                            style="success",
                        ),
                    ],
                    [
                        glass_button(
                            "🔙 Back" if english else "🔙 بازگشت",
                            owner_id,
                            "home",
                            style="primary",
                        )
                    ],
                ]
            )
            return text, keyboard

        if page == "legacy":
            english = settings.get("panel_language", "fa") == "en"
            text = (
                f"{notice_text}"
                + (
                    "⚙️ Complete settings\n\n"
                    "All existing secretary, form, relationships, "
                    "anti-delete, automation, scheduling, and media settings "
                    "remain available here."
                    if english
                    else
                    "⚙️ تنظیمات تکمیلی\n\n"
                    "تمام تنظیمات منشی، فرم‌ساز، دوست و دشمن، ضدحذف، "
                    "اتوماسیون، زمان‌بندی و رسانه بدون حذف یا تغییر اینجا "
                    "در دسترس هستند."
                )
            )
            names = (
                [
                    ("⚙️ General", "general"),
                    ("🤖 Secretary", "secretary"),
                    ("🧾 Forms", "forms"),
                    ("🎨 Appearance", "appearance"),
                    ("💚 Relationships", "relationships"),
                    ("⚡ Automation", "automation"),
                    ("⏰ Schedule", "schedule"),
                    ("👁 Profile monitor", "profiles"),
                    ("🧰 Tools", "tools"),
                    ("📚 Command guide", "guide"),
                    ("❓ Options guide", "option_guide"),
                    ("🌐 Language", "language"),
                ]
                if english
                else [
                    ("⚙️ عمومی", "general"),
                    ("🤖 منشی", "secretary"),
                    ("🧾 فرم‌ساز", "forms"),
                    ("🎨 ظاهر", "appearance"),
                    ("💚 دوست و دشمن", "relationships"),
                    ("⚡ اتوماسیون", "automation"),
                    ("⏰ زمان‌بندی", "schedule"),
                    ("👁 پایش پروفایل", "profiles"),
                    ("🧰 ابزار", "tools"),
                    ("📚 راهنمای دستورات", "guide"),
                    ("❓ توضیح گزینه‌ها", "option_guide"),
                    ("🌐 تغییر زبان", "language"),
                ]
            )
            rows = []
            for index in range(0, len(names), 2):
                rows.append(
                    [
                        glass_button(
                            label,
                            owner_id,
                            destination,
                            style="primary",
                        )
                        for label, destination in names[index : index + 2]
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔙 Back" if english else "🔙 بازگشت",
                        owner_id,
                        "home",
                        style="primary",
                    )
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "general":
            counts = get_feature_counts(self.data_dir, phone)
            text = (
                f"{notice_text}"
                "⚙️ تنظیمات عمومی\n\n"
                f"همیشه آنلاین: {self.state(settings, 'online_status')}\n"
                f"عضویت اجباری پیوی: "
                f"{self.state(settings, 'force_join_private')}\n"
                f"سین خودکار پیوی: {self.state(settings, 'auto_read_private')}\n"
                f"سین خودکار گروه: {self.state(settings, 'auto_read_groups')}\n"
                f"ذخیره عکس زمان‌دار: "
                f"{self.state(settings, 'save_timed_photos')}\n"
                f"ضدحذف پیام‌های عادی: "
                f"{self.state(settings, 'anti_delete_enabled')}\n"
                f"پیوی: {self.state(settings, 'anti_delete_private')} | "
                f"گروه: {self.state(settings, 'anti_delete_groups')} | "
                f"کانال: {self.state(settings, 'anti_delete_channels')}\n"
                f"سقف هر رسانه: "
                f"{settings.get('anti_delete_max_mb', '50')} مگابایت\n"
                f"نگهداری موقت: "
                f"{settings.get('anti_delete_retention_days', '7')} روز\n"
                f"پیام‌های در انتظار: {counts['archive']}\n\n"
                "با لمس دکمه، تنظیم همان لحظه در دیتابیس سلف ثبت می‌شود."
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        self.toggle_button(
                            owner_id,
                            "online_status",
                            settings,
                        )
                    ],
                    [
                        self.toggle_button(
                            owner_id,
                            "save_timed_photos",
                            settings,
                        )
                    ],
                    [
                        self.toggle_button(
                            owner_id,
                            "anti_delete_enabled",
                            settings,
                        )
                    ],
                    [
                        self.toggle_button(
                            owner_id,
                            "anti_delete_private",
                            settings,
                        ),
                        self.toggle_button(
                            owner_id,
                            "anti_delete_groups",
                            settings,
                        ),
                    ],
                    [
                        self.toggle_button(
                            owner_id,
                            "anti_delete_channels",
                            settings,
                        )
                    ],
                    [
                        glass_button(
                            "📦 تغییر سقف حجم",
                            owner_id,
                            "antidelete.max",
                            style="primary",
                        ),
                        glass_button(
                            "🕒 تغییر مدت نگهداری",
                            owner_id,
                            "antidelete.retention",
                            style="primary",
                        ),
                    ],
                    [
                        glass_button(
                            "🗑 پاک‌سازی آرشیو موقت",
                            owner_id,
                            "antidelete.clear",
                            style="danger",
                        )
                    ],
                    [
                        self.toggle_button(
                            owner_id,
                            "force_join_private",
                            settings,
                        )
                    ],
                    [
                        self.toggle_button(
                            owner_id,
                            "auto_read_private",
                            settings,
                        ),
                        self.toggle_button(
                            owner_id,
                            "auto_read_groups",
                            settings,
                        ),
                    ],
                    [
                        glass_button(
                            "🔙 بازگشت",
                            owner_id,
                            "home",
                            style="primary",
                        )
                    ],
                ]
            )
            return text, keyboard

        if page == "antidelete_confirm":
            text = (
                "⚠️ پاک‌سازی آرشیو موقت ضدحذف\n\n"
                "همه پیام‌ها و فایل‌هایی که هنوز در انتظار تشخیص حذف هستند "
                "از حافظه این سلف پاک می‌شوند. پیام‌هایی که قبلاً به "
                "Saved Messages منتقل شده‌اند حذف نخواهند شد.\n\n"
                "آیا مطمئن هستید؟"
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        glass_button(
                            "✅ بله، پاک شود",
                            owner_id,
                            "antidelete.clear.confirm",
                            style="danger",
                        ),
                        glass_button(
                            "❌ لغو",
                            owner_id,
                            "general",
                            style="primary",
                        ),
                    ]
                ]
            )
            return text, keyboard

        if page == "secretary":
            duration = settings.get("typing_duration", "5")
            offline_cooldown = settings.get(
                "offline_reply_cooldown_minutes",
                "360",
            )
            offline_text = settings.get(
                "offline_reply_text",
                "",
            ).strip()
            offline_preview = (
                offline_text.replace("\n", " ")[:180]
                if offline_text
                else "ثبت نشده"
            )
            fallback_text = settings.get(
                "secretary_fallback_text",
                "",
            ).strip()
            fallback_preview = (
                fallback_text.replace("\n", " ")[:180]
                if fallback_text
                else "ثبت نشده"
            )
            replies = list_secretary_replies(
                self.data_dir,
                phone,
                limit=8,
            )
            reply_count = count_secretary_replies(self.data_dir, phone)
            rich_rules = list_auto_reply_rules(
                self.data_dir,
                phone,
                limit=8,
            )
            reply_preview = "\n".join(
                f"• #{int(item['id'])} "
                f"{str(item['pattern']).replace(chr(10), ' ')[:50]}"
                for item in replies
            ) or "• پاسخ متنی قدیمی ثبت نشده است."
            rich_preview = "\n".join(
                f"• چندپاسخی #{int(item['id'])}: "
                f"{str(item.get('triggers') or '').replace(',', '، ')[:70]} "
                f"({int(item.get('response_count') or 0)} پاسخ)"
                for item in rich_rules
            ) or "• قانون چندپاسخی ثبت نشده است."
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            text = (
                f"{notice_text}"
                "🤖 منشی و پاسخ خودکار\n\n"
                "کار هر بخش:\n"
                "• سؤال‌وجواب: پیام را با عبارت‌های ثبت‌شده تطبیق می‌دهد "
                "و پاسخ دلخواه شما را می‌فرستد.\n"
                "• پاسخ عمومی منشی: اگر هیچ سؤال‌وجواب یا فرمی مطابق نبود، "
                "متن عمومی شما را می‌فرستد.\n"
                "• حالت آفلاین: در پیوی از همه پاسخ‌ها اولویت بیشتری دارد "
                "و با فاصله زمانی تعیین‌شده ارسال می‌شود.\n\n"
                f"اکشن تایپینگ: {self.state(settings, 'typing_action')}\n"
                f"مدت تایپینگ: {duration} ثانیه\n"
                f"پاسخ عمومی منشی: {self.state(settings, 'secretary')}\n"
                f"سؤال‌وجواب‌های ثبت‌شده: "
                f"{self.state(settings, 'auto_reply')}\n"
                f"پاسخ حالت آفلاین: "
                f"{self.state(settings, 'offline_reply_enabled')}\n"
                f"تکرار پاسخ آفلاین: هر {offline_cooldown} دقیقه\n"
                f"متن آفلاین: {offline_preview}\n"
                f"متن عمومی منشی: {fallback_preview}\n"
                f"پاسخ‌های قدیمی: {reply_count} | "
                f"قانون‌های چندپاسخی: {len(rich_rules)}\n\n"
                f"{rich_preview}\n{reply_preview}"
            )
            rows = [
                    [
                        self.toggle_button(
                            owner_id,
                            "secretary",
                            settings,
                        ),
                        self.toggle_button(
                            owner_id,
                            "auto_reply",
                            settings,
                        ),
                    ],
                    [
                        self.toggle_button(
                            owner_id,
                            "typing_action",
                            settings,
                        )
                    ],
                    [
                        glass_button(
                            f"⏱ مدت تایپینگ: {duration} ثانیه",
                            owner_id,
                            "duration",
                            style="primary",
                        )
                    ],
                    [
                        self.toggle_button(
                            owner_id,
                            "offline_reply_enabled",
                            settings,
                        )
                    ],
            ]
            if setup_base:
                rows.extend(
                    [
                        [
                            link_button(
                                "➕ افزودن پاسخ چندگانه/رسانه‌ای",
                                f"{setup_base}secretaryqa_{owner_id}",
                                style="success",
                            )
                        ],
                        [
                            link_button(
                                "✍️ تنظیم پاسخ عمومی منشی",
                                f"{setup_base}secretaryfallback_{owner_id}",
                                style="primary",
                            )
                        ],
                        [
                            link_button(
                                "🌙 تنظیم متن آفلاین",
                                f"{setup_base}offlinetext_{owner_id}",
                                style="primary",
                            ),
                            link_button(
                                "⏳ فاصله تکرار",
                                f"{setup_base}offlinecooldown_{owner_id}",
                                style="primary",
                            ),
                        ],
                    ]
                )
            for item in replies:
                label = str(item["pattern"]).replace("\n", " ")[:42]
                rows.append(
                    [
                        glass_button(
                            f"🗑 حذف: {label}",
                            owner_id,
                            f"reply.delete.{int(item['id'])}",
                            style="danger",
                        )
                    ]
                )
            for item in rich_rules:
                label = str(item.get("triggers") or "").replace(",", "، ")[:34]
                rows.append(
                    [
                        glass_button(
                            f"🗑 چندپاسخی: {label}",
                            owner_id,
                            f"autoreply.delete.{int(item['id'])}",
                            style="danger",
                        )
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔄 بروزرسانی",
                        owner_id,
                        "secretary",
                        style="success",
                    ),
                    glass_button(
                        "🔙 بازگشت",
                        owner_id,
                        "home",
                        style="primary",
                    ),
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "forms":
            forms = list_form_templates(
                self.data_dir,
                phone,
                limit=20,
            )
            active_count = sum(
                1 for form in forms if int(form.get("is_active") or 0)
            )
            intro = settings.get("form_intro_text", "").strip()
            intro_preview = (
                intro.replace("\n", " ")[:220]
                if intro
                else "ثبت نشده"
            )
            form_lines = []
            for form in forms:
                state_icon = (
                    "✅" if int(form.get("is_active") or 0) else "❌"
                )
                form_lines.append(
                    f"{state_icon} #{int(form['id'])} {form['name']} — "
                    f"شروع: «{form['trigger_text']}» — "
                    f"{int(form.get('field_count') or 0)} سؤال"
                )
            form_preview = (
                "\n".join(form_lines)
                if form_lines
                else "• هنوز فرمی ساخته نشده است."
            )
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            text = (
                f"{notice_text}"
                "🧾 فرم‌ساز سفارش و درخواست\n\n"
                "وقتی فرم‌ساز روشن باشد، هر کاربر در پیوی ابتدا فهرست "
                "فرم‌های فعال را می‌بیند. با ارسال کلمه شروع مثل «کفش» یا "
                "«لباس»، سؤال‌های همان فرم یکی‌یکی پرسیده می‌شود. در پایان "
                "همه جواب‌ها برای تأیید نمایش داده می‌شوند.\n\n"
                "پس از تأیید، نسخه کامل برای کاربر و Saved Messages ادمین "
                "ارسال می‌شود. ادمین روی نسخه Saved Messages ریپلای می‌کند "
                "و یکی از وضعیت‌های «در حال پردازش»، «آماده ارسال»، "
                "«ارسال شده»، «تکمیل شده» یا «لغو شده» را می‌فرستد.\n\n"
                f"وضعیت کلی: {self.state(settings, 'form_builder_enabled')}\n"
                f"فرم فعال: {active_count} از {len(forms)}\n"
                f"متن معرفی: {intro_preview}\n\n"
                f"{form_preview}"
            )
            rows = [
                [
                    self.toggle_button(
                        owner_id,
                        "form_builder_enabled",
                        settings,
                    )
                ]
            ]
            if setup_base:
                rows.extend(
                    [
                        [
                            link_button(
                                "➕ ساخت فرم جدید",
                                f"{setup_base}formcreate_{owner_id}",
                                style="success",
                            )
                        ],
                        [
                            link_button(
                                "✍️ تنظیم متن معرفی فرم‌ها",
                                f"{setup_base}formintro_{owner_id}",
                                style="primary",
                            )
                        ],
                    ]
                )
            for form in forms:
                form_id = int(form["id"])
                enabled = bool(int(form.get("is_active") or 0))
                rows.append(
                    [
                        glass_button(
                            (
                                f"{'❌ غیرفعال‌کردن' if enabled else '✅ فعال‌کردن'} "
                                f"{str(form['name'])[:24]}"
                            ),
                            owner_id,
                            f"form.toggle.{form_id}",
                            style="danger" if enabled else "success",
                        ),
                        glass_button(
                            "🗑 حذف",
                            owner_id,
                            f"form.delete.{form_id}",
                            style="danger",
                        ),
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔄 بروزرسانی",
                        owner_id,
                        "forms",
                        style="success",
                    ),
                    glass_button(
                        "🔙 بازگشت",
                        owner_id,
                        "home",
                        style="primary",
                    ),
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "appearance":
            font = settings.get("font", "1")
            style = settings.get("outgoing_text_style", "none")
            signature = settings.get(
                "outgoing_signature_text",
                "",
            ).strip()
            watermark = settings.get("watermark_text", "").strip()
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            text = (
                f"{notice_text}"
                "🎨 ظاهر، حالت متن و لوگو\n\n"
                f"ساعت در نام: {self.state(settings, 'timename')}\n"
                f"ساعت در بیو: {self.state(settings, 'timebio')}\n"
                f"مدل فونت ساعت: {font} از 10\n"
                f"حالت متن خروجی: {style}\n"
                f"امضای خودکار: "
                f"{self.state(settings, 'outgoing_signature_enabled')}\n"
                f"متن امضا: {signature[:80] or 'ثبت نشده'}\n"
                f"لوگوی عکس: {watermark[:80] or 'ثبت نشده'}"
            )
            rows = [
                    [
                        self.toggle_button(owner_id, "timename", settings),
                        self.toggle_button(owner_id, "timebio", settings),
                    ],
                    [
                        glass_button(
                            f"🔤 تغییر فونت؛ مدل فعلی {font}",
                            owner_id,
                            "font",
                            style="primary",
                        )
                    ],
                    [
                        glass_button(
                            f"📝 حالت متن: {style}",
                            owner_id,
                            "textstyle",
                            style="primary",
                        )
                    ],
                    [
                        self.toggle_button(
                            owner_id,
                            "outgoing_signature_enabled",
                            settings,
                        )
                    ],
            ]
            if setup_base:
                rows.extend(
                    [
                        [
                            link_button(
                                "✍️ تنظیم متن امضا",
                                f"{setup_base}signature_{owner_id}",
                                style="primary",
                            ),
                            link_button(
                                "🖼 تنظیم متن لوگو",
                                f"{setup_base}watermark_{owner_id}",
                                style="primary",
                            ),
                        ]
                    ]
                )
            rows.append(
                    [
                        glass_button(
                            "🔙 بازگشت",
                            owner_id,
                            "home",
                            style="primary",
                        )
                    ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "schedule":
            interval = settings.get(
                "scheduled_message_interval_minutes",
                "5",
            )
            target = (
                settings.get("scheduled_message_target", "").strip()
                or "ثبت نشده"
            )
            scheduled_text = settings.get(
                "scheduled_message_text",
                "",
            ).strip()
            preview = (
                scheduled_text.replace("\n", " ")[:160]
                if scheduled_text
                else "ثبت نشده"
            )
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            jobs = list_schedule_jobs(
                self.data_dir,
                phone,
                statuses=("active", "running", "paused", "uncertain"),
                limit=8,
            )
            metrics = get_runtime_metrics(self.data_dir, phone)
            queue_depth = metrics.get("send_queue_queue_depth", "0")
            queue_paused = metrics.get("send_queue_paused_seconds", "0")
            recurrence_labels = {
                "once": "یک‌باره",
                "interval": "فاصله‌ای",
                "daily": "روزانه",
                "weekly": "هفتگی",
            }
            job_lines = []
            for job in jobs:
                state_icon = (
                    "⏸"
                    if job["status"] == "paused"
                    else "⏳"
                    if job["status"] == "running"
                    else "⚠️"
                    if job["status"] == "uncertain"
                    else "✅"
                )
                job_lines.append(
                    f"{state_icon} #{job['id']} "
                    f"{recurrence_labels.get(job['recurrence_type'], job['recurrence_type'])}"
                    f" → {job['target']} | {job['next_run_at']}"
                )
            job_preview = "\n".join(job_lines) or "• برنامه حرفه‌ای ثبت نشده است."
            text = (
                f"{notice_text}"
                "⏰ زمان‌بندی حرفه‌ای\n\n"
                "برنامه‌های یک‌باره، فاصله‌ای، روزانه و هفتگی از صف امن "
                "ارسال استفاده می‌کنند و پس از FloodWait ادامه می‌یابند.\n\n"
                f"صف انتظار: {queue_depth} | مکث FloodWait: "
                f"{queue_paused} ثانیه\n\n"
                f"{job_preview}\n\n"
                "ارسال تکراری قدیمی:\n"
                f"وضعیت: {self.state(settings, 'scheduled_message_enabled')}\n"
                f"مقصد: {target}\n"
                f"فاصله: هر {interval} دقیقه\n"
                f"متن: {preview}\n\n"
                "پیام با حساب همین سلف ارسال می‌شود. حساب باید داخل گروه "
                "مقصد عضو باشد."
            )
            rows = [
                []
            ]
            if setup_base:
                rows[0].append(
                    link_button(
                        "➕ برنامه حرفه‌ای جدید",
                        f"{setup_base}schedcreate_{owner_id}",
                        style="success",
                    )
                )
            else:
                rows[0].append(
                    glass_button(
                        "هلپر تنظیم نشده",
                        owner_id,
                        "schedule",
                        style="danger",
                    )
                )
            for job in jobs:
                job_id = int(job["id"])
                if job["status"] in {"paused", "uncertain"}:
                    state_button = glass_button(
                        f"▶️ ادامه #{job_id}",
                        owner_id,
                        f"schedule.resume.{job_id}",
                        style="success",
                    )
                else:
                    state_button = glass_button(
                        f"⏸ توقف #{job_id}",
                        owner_id,
                        f"schedule.pause.{job_id}",
                        style="primary",
                    )
                rows.append(
                    [
                        state_button,
                        glass_button(
                            f"🗑 لغو #{job_id}",
                            owner_id,
                            f"schedule.cancel.{job_id}",
                            style="danger",
                        ),
                    ]
                )
            rows.append(
                [
                    self.toggle_button(
                        owner_id,
                        "scheduled_message_enabled",
                        settings,
                    )
                ]
            )
            if setup_base:
                rows.extend(
                    [
                        [
                            link_button(
                                "📝 تنظیم متن",
                                f"{setup_base}schedtext_{owner_id}",
                                style="primary",
                            ),
                            link_button(
                                "👥 تنظیم گروه مقصد",
                                f"{setup_base}schedtarget_{owner_id}",
                                style="primary",
                            ),
                        ],
                        [
                            link_button(
                                "⏱ تنظیم فاصله زمانی",
                                f"{setup_base}schedinterval_{owner_id}",
                                style="primary",
                            )
                        ],
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔄 بروزرسانی",
                        owner_id,
                        "schedule",
                        style="success",
                    ),
                    glass_button(
                        "🔙 بازگشت",
                        owner_id,
                        "home",
                        style="primary",
                    ),
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "moderation":
            filters_list = list_word_filters(
                self.data_dir,
                phone,
                limit=8,
            )
            filter_preview = "\n".join(
                f"• #{item['id']} {str(item['phrase'])[:35]} → "
                f"{item['action']}"
                for item in filters_list
            ) or "• هنوز فیلتری ثبت نشده است."
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            text = (
                f"{notice_text}"
                "🛡 قفل‌ها و فیلتر کلمات\n\n"
                f"لینک: {self.state(settings, 'lock_links')} | "
                f"فوروارد: {self.state(settings, 'lock_forwards')}\n"
                f"عکس: {self.state(settings, 'lock_photos')} | "
                f"ویدیو: {self.state(settings, 'lock_videos')}\n"
                f"گیف: {self.state(settings, 'lock_gifs')} | "
                f"استیکر: {self.state(settings, 'lock_stickers')}\n"
                f"ویس: {self.state(settings, 'lock_voice')} | "
                f"فایل: {self.state(settings, 'lock_files')}\n"
                f"نظرسنجی: {self.state(settings, 'lock_polls')}\n"
                f"فیلتر کلمات: "
                f"{self.state(settings, 'word_filter_enabled')}\n\n"
                f"{filter_preview}\n\n"
                "برای حذف پیام اعضا، حساب سلف باید در گروه ادمین باشد."
            )
            rows = [
                [
                    self.toggle_button(owner_id, "lock_links", settings),
                    self.toggle_button(owner_id, "lock_forwards", settings),
                ],
                [
                    self.toggle_button(owner_id, "lock_photos", settings),
                    self.toggle_button(owner_id, "lock_videos", settings),
                ],
                [
                    self.toggle_button(owner_id, "lock_gifs", settings),
                    self.toggle_button(owner_id, "lock_stickers", settings),
                ],
                [
                    self.toggle_button(owner_id, "lock_voice", settings),
                    self.toggle_button(owner_id, "lock_files", settings),
                ],
                [
                    self.toggle_button(owner_id, "lock_polls", settings),
                ],
                [
                    self.toggle_button(
                        owner_id,
                        "word_filter_enabled",
                        settings,
                    )
                ],
            ]
            if setup_base:
                rows.append(
                    [
                        link_button(
                            "➕ افزودن فیلتر",
                            f"{setup_base}filteradd_{owner_id}",
                            style="success",
                        )
                    ]
                )
            for item in filters_list:
                rows.append(
                    [
                        glass_button(
                            f"🗑 حذف فیلتر #{item['id']}",
                            owner_id,
                            f"filter.delete.{int(item['id'])}",
                            style="danger",
                        )
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔙 بازگشت",
                        owner_id,
                        "home",
                        style="primary",
                    )
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "automation":
            emoji = settings.get("auto_reaction_emoji", "❤️")
            comments = list_first_comment_channels(
                self.data_dir,
                phone,
                limit=8,
            )
            comment_preview = "\n".join(
                f"• {item['chat_id']} | {item['delay_seconds']} ثانیه | "
                f"{str(item['comment_text'])[:40]}"
                for item in comments
            ) or "• کانالی برای کامنت اول ثبت نشده است."
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            text = (
                f"{notice_text}"
                "⚡ اتوماسیون و ری‌اکت\n\n"
                f"ری‌اکت خودکار: {self.state(settings, 'auto_reaction')}\n"
                f"ایموجی ری‌اکت: {emoji}\n"
                f"واکنش دوست/دشمن: "
                f"{self.state(settings, 'relationship_reaction')}\n"
                f"کامنت اول: {self.state(settings, 'first_comment_enabled')}\n\n"
                f"{comment_preview}"
            )
            rows = [
                [
                    self.toggle_button(
                        owner_id,
                        "auto_reaction",
                        settings,
                    ),
                    self.toggle_button(
                        owner_id,
                        "relationship_reaction",
                        settings,
                    ),
                ],
                [
                    self.toggle_button(
                        owner_id,
                        "first_comment_enabled",
                        settings,
                    )
                ],
            ]
            if setup_base:
                rows.extend(
                    [
                        [
                            link_button(
                                f"❤️ تنظیم ری‌اکت: {emoji}",
                                f"{setup_base}reaction_{owner_id}",
                                style="primary",
                            )
                        ],
                        [
                            link_button(
                                "➕ افزودن کانال کامنت",
                                f"{setup_base}firstcomment_{owner_id}",
                                style="success",
                            ),
                            link_button(
                                "🗑 حذف کانال کامنت",
                                f"{setup_base}firstcommentdel_{owner_id}",
                                style="danger",
                            ),
                        ],
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔙 بازگشت",
                        owner_id,
                        "home",
                        style="primary",
                    )
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "relationships":
            friends = list_friends(self.data_dir, phone, limit=15)
            enemies = list_enemies(self.data_dir, phone, limit=15)
            friend_replies = list_friend_affection_replies(
                self.data_dir,
                phone,
                limit=100,
            )
            enemy_replies = list_enemy_hostile_replies(
                self.data_dir,
                phone,
                limit=100,
            )
            friend_text = "، ".join(str(item) for item in friends) or "خالی"
            enemy_text = "، ".join(str(item) for item in enemies) or "خالی"
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            text = (
                f"{notice_text}"
                "💚 دوست و دشمن\n"
                "━━━━━━━━━━━━━━\n\n"
                f"دوستان ({len(friends)}): {friend_text}\n"
                f"دشمنان ({len(enemies)}): {enemy_text}\n\n"
                f"واکنش خودکار دوست/دشمن: "
                f"{self.state(settings, 'relationship_reaction')}\n"
                f"ریپلای صمیمی به دوستان: "
                f"{self.state(settings, 'friend_affection_reply')}\n"
                f"متن‌های دلخواه دوست: {len(friend_replies)} مورد\n\n"
                f"پاسخ خودکار به دشمنان: "
                f"{self.state(settings, 'enemy_hostile_reply')}\n"
                f"متن‌های دلخواه دشمن: {len(enemy_replies)} مورد\n\n"
                "💡 روش سریع: روی پیام کاربر ریپلای کنید و بنویسید "
                "«تنظیم دوست» یا «تنظیم دشمن»."
            )
            rows = [
                [
                    self.toggle_button(
                        owner_id,
                        "relationship_reaction",
                        settings,
                    )
                ],
                [
                    self.toggle_button(
                        owner_id,
                        "friend_affection_reply",
                        settings,
                    )
                ],
                [
                    self.toggle_button(
                        owner_id,
                        "enemy_hostile_reply",
                        settings,
                    )
                ],
                [
                    glass_button(
                        "💬 مدیریت متن‌های دوست",
                        owner_id,
                        "friend_replies",
                        style="primary",
                    )
                ],
                [
                    glass_button(
                        "💢 مدیریت متن‌های دشمن",
                        owner_id,
                        "enemy_replies",
                        style="primary",
                    )
                ],
            ]
            if setup_base:
                rows.extend(
                    [
                        [
                            link_button(
                                "➕ افزودن دوست",
                                f"{setup_base}friendadd_{owner_id}",
                                style="success",
                            ),
                            link_button(
                                "➖ حذف دوست",
                                f"{setup_base}frienddel_{owner_id}",
                                style="danger",
                            ),
                        ],
                        [
                            link_button(
                                "➕ افزودن دشمن",
                                f"{setup_base}enemyadd_{owner_id}",
                                style="success",
                            ),
                            link_button(
                                "➖ حذف دشمن",
                                f"{setup_base}enemydel_{owner_id}",
                                style="danger",
                            ),
                        ],
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔙 بازگشت",
                        owner_id,
                        "home",
                        style="primary",
                    )
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "enemy_replies":
            replies = list_enemy_hostile_replies(
                self.data_dir,
                phone,
                limit=100,
            )
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            previews = "\n".join(
                f"• #{item['id']} — {str(item['response'])[:70]}"
                for item in replies[:15]
            )
            if not previews:
                previews = (
                    "• هنوز متنی ثبت نشده است.\n"
                    "• تا زمان افزودن متن، سلف به دشمن ریپلای متنی نمی‌زند."
                )
            text = (
                f"{notice_text}"
                "💢 متن‌های پاسخ به دشمن\n"
                "━━━━━━━━━━━━━━\n\n"
                f"تعداد متن‌های دلخواه: {len(replies)}\n\n"
                f"{previews}\n\n"
                "این متن‌ها فقط برای دشمنان ثبت‌شده همین سلف و به‌صورت "
                "تصادفی استفاده می‌شوند."
            )
            rows = []
            if setup_base:
                rows.append(
                    [
                        link_button(
                            "➕ افزودن متن جدید",
                            f"{setup_base}enemytext_{owner_id}",
                            style="success",
                        )
                    ]
                )
            for item in replies[:15]:
                preview = str(item["response"]).replace("\n", " ")[:28]
                rows.append(
                    [
                        glass_button(
                            f"🗑 #{item['id']} {preview}",
                            owner_id,
                            f"enemyreply.delete.{int(item['id'])}",
                            style="danger",
                        )
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔙 بازگشت به دوست و دشمن",
                        owner_id,
                        "relationships",
                        style="primary",
                    )
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "friend_replies":
            replies = list_friend_affection_replies(
                self.data_dir,
                phone,
                limit=100,
            )
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            previews = "\n".join(
                f"• #{item['id']} — {str(item['response'])[:70]}"
                for item in replies[:15]
            )
            if not previews:
                previews = (
                    "• هنوز متن دلخواهی ثبت نشده است.\n"
                    "• فعلاً متن‌های صمیمی پیش‌فرض استفاده می‌شوند."
                )
            text = (
                f"{notice_text}"
                "💬 متن‌های پاسخ به دوست\n"
                "━━━━━━━━━━━━━━\n\n"
                f"تعداد متن‌های دلخواه: {len(replies)}\n\n"
                f"{previews}\n\n"
                "اگر متن دلخواه داشته باشید، سلف برای دوستان فقط از "
                "همین متن‌ها به‌صورت تصادفی استفاده می‌کند."
            )
            rows = []
            if setup_base:
                rows.append(
                    [
                        link_button(
                            "➕ افزودن متن جدید",
                            f"{setup_base}friendtext_{owner_id}",
                            style="success",
                        )
                    ]
                )
            for item in replies[:15]:
                preview = str(item["response"]).replace("\n", " ")[:28]
                rows.append(
                    [
                        glass_button(
                            f"🗑 #{item['id']} {preview}",
                            owner_id,
                            f"friendreply.delete.{int(item['id'])}",
                            style="danger",
                        )
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔙 بازگشت به دوست و دشمن",
                        owner_id,
                        "relationships",
                        style="primary",
                    )
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "profiles":
            profiles = list_tracked_profiles(
                self.data_dir,
                phone,
                limit=12,
            )
            interval = settings.get(
                "profile_monitor_interval_minutes",
                "10",
            )
            profile_preview = "\n".join(
                f"• {item['user_id']} — {item['label'] or 'بدون نام'}"
                for item in profiles
            ) or "• کاربری برای پایش ثبت نشده است."
            helper_username = get_helper_config(
                self.users_db
            ).get("username", "")
            setup_base = (
                f"https://t.me/{helper_username}?start="
                if helper_username
                else ""
            )
            text = (
                f"{notice_text}"
                "👁 پایش تغییرات پروفایل\n\n"
                f"وضعیت: {self.state(settings, 'profile_monitor_enabled')}\n"
                f"فاصله بررسی: هر {interval} دقیقه\n\n"
                f"{profile_preview}\n\n"
                "فقط تغییرات نام، یوزرنیم، بیو و عکس ثبت می‌شود؛ "
                "بازدیدکننده پروفایل قابل تشخیص نیست."
            )
            rows = [
                [
                    self.toggle_button(
                        owner_id,
                        "profile_monitor_enabled",
                        settings,
                    )
                ],
                [
                    glass_button(
                        f"⏱ فاصله پایش: {interval} دقیقه",
                        owner_id,
                        "profileinterval",
                        style="primary",
                    )
                ],
            ]
            if setup_base:
                rows.extend(
                    [
                        [
                            link_button(
                                "➕ افزودن کاربر",
                                f"{setup_base}profileadd_{owner_id}",
                                style="success",
                            ),
                            link_button(
                                "➖ حذف با آیدی",
                                f"{setup_base}profiledel_{owner_id}",
                                style="danger",
                            ),
                        ]
                    ]
                )
            for item in profiles[:8]:
                rows.append(
                    [
                        glass_button(
                            f"🗑 حذف پایش {item['user_id']}",
                            owner_id,
                            f"profile.delete.{int(item['user_id'])}",
                            style="danger",
                        )
                    ]
                )
            rows.append(
                [
                    glass_button(
                        "🔙 بازگشت",
                        owner_id,
                        "home",
                        style="primary",
                    )
                ]
            )
            return text, InlineKeyboardMarkup(rows)

        if page == "tools":
            tts_voice = settings.get("tts_voice", "female")
            text = (
                f"{notice_text}"
                "🧰 ابزار و رسانه\n\n"
                "این ابزارها به پیام یا چت فعلی وابسته‌اند و با دستور "
                "اجرا می‌شوند:\n\n"
                "• `دانلود` روی رسانه — ذخیره در Saved Messages\n"
                "• `مخفی` / `نمایش` — بایگانی چت\n"
                "• `لوگو` روی عکس — افزودن واترمارک\n"
                "• `ترجمه en` روی پیام — ترجمه\n"
                "• `حساب 2+2*3` — ماشین‌حساب امن\n"
                "• `ویس زن متن` / `ویس مرد متن` — متن‌به‌ویس\n"
                "• `ویس ذخیره کلیدواژه` — ثبت ویس ریپلای‌شده\n"
                "• `ویس سرچ کلیدواژه` — جست‌وجوی بانک ویس\n"
                "• `آهنگ نام` — جست‌وجوی آهنگ\n"
                "• `قیمت btc` / `ارز USD EUR` — قیمت آنلاین\n"
                "• `اسکرین https://...` — تصویر صفحه وب\n"
                "• `تایپ متن` / `شمارش 10` — انیمیشن\n\n"
                f"صدای پیش‌فرض پنل: "
                f"{'مرد' if tts_voice == 'male' else 'زن'}"
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        glass_button(
                            "🎙 تغییر صدای پیش‌فرض",
                            owner_id,
                            "ttsvoice",
                            style="primary",
                        )
                    ],
                    [
                        glass_button(
                            "📚 همه دستورات",
                            owner_id,
                            "guide",
                            style="primary",
                        )
                    ],
                    [
                        glass_button(
                            "🔙 بازگشت",
                            owner_id,
                            "home",
                            style="primary",
                        )
                    ],
                ]
            )
            return text, keyboard

        if page == "status":
            running = self.process_is_running(record.get("self_pid"))
            pid_text = (
                str(record.get("self_pid"))
                if record.get("self_pid")
                else "ثبت نشده"
            )
            text = (
                f"{notice_text}"
                "📊 وضعیت سلف\n\n"
                f"پردازش: {'🟢 در حال اجرا' if running else '🔴 متوقف'}\n"
                f"شناسه پردازش: {pid_text}\n"
                f"آخرین ثبت مرکزی: {record.get('updated_at') or 'نامشخص'}\n"
                f"وضعیت حساب: {'فعال' if int(record.get('is_active') or 0) else 'غیرفعال'}\n\n"
                f"زمان بررسی: {generated_at}"
            )
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        glass_button(
                            "🔄 بررسی دوباره",
                            owner_id,
                            "status",
                            style="success",
                        )
                    ],
                    [
                        glass_button(
                            "🔙 بازگشت",
                            owner_id,
                            "home",
                            style="primary",
                        )
                    ],
                ]
            )
            return text, keyboard

        option_pages = {
            "option_guide": (
                "❓ راهنمای همه گزینه‌های پنل\n"
                "━━━━━━━━━━━━━━\n\n"
                "در این بخش نام هر گزینه، اثر دقیق آن و نکته‌های مهم "
                "هم‌زمانی با گزینه‌های دیگر توضیح داده شده است.\n\n"
                "یک موضوع را انتخاب کنید 👇"
            ),
            "option_security": (
                "🔐 توضیح گزینه‌های امنیتی\n"
                "━━━━━━━━━━━━━━\n\n"
                "• قفل کامل پیوی: پیام افراد تأییدنشده را مدیریت می‌کند.\n"
                "• حذف پیام ناشناس: پیام فرد غیرمجاز را از سمت حساب شما "
                "حذف می‌کند؛ پیام سمت فرستنده باقی می‌ماند.\n"
                "• هشدار قبل بلاک: پیش از بلاک‌کردن، متن هشدار را می‌فرستد. "
                "اگر خاموش باشد، بلاک بدون هشدار انجام می‌شود.\n"
                "• افراد مجاز: از قفل پیوی و بلاک خودکار مستثنا هستند.\n"
                "• ضد ویرایش: نسخه قبلی پیام ویرایش‌شده را در Saved "
                "Messages ثبت می‌کند؛ برای پیوی و گروه جداست.\n"
                "• قفل لینک/رسانه: پیام مطابق نوع انتخابی را در گروهی که "
                "حساب دسترسی حذف دارد پاک می‌کند.\n"
                "• فیلتر کلمات: روی عبارت‌های ثبت‌شده عملیات حذف، هشدار، "
                "سکوت یا بلاک را اجرا می‌کند."
            ),
            "option_presence": (
                "👤 توضیح گزینه‌های پروفایل و وضعیت\n"
                "━━━━━━━━━━━━━━\n\n"
                "• همیشه آنلاین: سلف هر دقیقه وضعیت حساب را آنلاین نگه "
                "می‌دارد؛ در این حالت تشخیص خودکار همیشه آنلاین است.\n"
                "• ایموجی وضعیت کنار نام: ایموجی انتخابی را بدون تغییر "
                "نام اصلی، کنار نام کوچک قرار می‌دهد.\n"
                "• تشخیص خودکار: وقتی همیشه آنلاین خاموش باشد، وضعیت واقعی "
                "تلگرام و فعالیت حساب را بررسی و 🟢/🔴 را عوض می‌کند.\n"
                "• ساعت در نام: ساعت تهران را در نام خانوادگی می‌گذارد و "
                "پس از خاموش‌شدن، نام خانوادگی قبلی را برمی‌گرداند.\n"
                "• ساعت در بیو: ساعت تهران را به بیوی اصلی اضافه می‌کند و "
                "بعد از خاموش‌شدن بیوی قبلی را برمی‌گرداند.\n"
                "• ساعت عقربه‌ای عکس: یک کپی از عکس پروفایل می‌سازد، ساعت "
                "را روی آن بروزرسانی می‌کند و عکس اصلی را برای بازیابی نگه "
                "می‌دارد.\n"
                "• پایش پروفایل: تغییر نام، بیو، یوزرنیم و عکس کاربران "
                "انتخابی را ثبت می‌کند؛ بازدید پروفایل را تشخیص نمی‌دهد."
            ),
            "option_messages": (
                "💬 توضیح گزینه‌های پیام و اتوماسیون\n"
                "━━━━━━━━━━━━━━\n\n"
                "• اکشن تایپینگ: قبل از پاسخ خودکار، حالت تایپ‌کردن را برای "
                "مدت تعیین‌شده نشان می‌دهد.\n"
                "• پاسخ عمومی منشی: وقتی قانون یا فرم مطابق پیدا نشود، متن "
                "عمومی را با فاصله امن می‌فرستد.\n"
                "• سؤال‌وجواب: محرک‌ها را پیدا می‌کند و یکی از پاسخ‌های "
                "متنی یا رسانه‌ای را تصادفی می‌فرستد.\n"
                "• پاسخ حالت آفلاین: پاسخ AFK دستی است و از پاسخ عمومی "
                "اولویت بیشتری دارد؛ تشخیص ایموجی وضعیت از آن مستقل است.\n"
                "• زمان‌بندی حرفه‌ای: یک‌باره، فاصله‌ای، روزانه یا هفتگی؛ "
                "ارسال‌ها وارد صف امن می‌شوند و پس از FloodWait ادامه دارند.\n"
                "• فرم‌ساز: سؤال‌ها را مرحله‌ای در پیوی می‌پرسد، تأیید "
                "می‌گیرد و نتیجه را در Saved Messages ذخیره می‌کند.\n"
                "• ری‌اکت خودکار: ایموجی تعیین‌شده را روی پیام‌های ورودی "
                "قرار می‌دهد؛ برای دوست و دشمن قانون جدا قابل فعال‌سازی است."
            ),
            "option_groups": (
                "👥 توضیح گزینه‌های گروه و رابطه‌ها\n"
                "━━━━━━━━━━━━━━\n\n"
                "• خوش‌آمد/خداحافظی: متن تنظیم‌شده را هنگام ورود یا خروج "
                "عضو می‌فرستد؛ متغیرهای نام، آیدی و گروه قابل استفاده‌اند.\n"
                "• عضویت اجباری پیوی: پیش از پاسخ در پیوی، عضویت کاربر در "
                "مقصد تنظیم‌شده را بررسی می‌کند.\n"
                "• سین خودکار: پیام پیوی یا گروه را بدون پاسخ خوانده علامت "
                "می‌زند؛ هرکدام کلید مستقل دارند.\n"
                "• دوست: می‌تواند ری‌اکت و پاسخ صمیمی تصادفی بگیرد.\n"
                "• دشمن: می‌تواند ری‌اکت و پاسخ دلخواه جداگانه بگیرد.\n"
                "• کامنت اول: پس از انتشار پست کانال انتخابی، متن تعیین‌شده "
                "را با تأخیر امن در بخش گفت‌وگو می‌فرستد.\n"
                "• گزارش مدیران: پیام ریپلای‌شده را فقط برای مدیران قابل "
                "دسترسی و با سقف امن ارسال می‌کند."
            ),
            "option_storage": (
                "🗄 توضیح ذخیره‌سازی، ظاهر و ابزار\n"
                "━━━━━━━━━━━━━━\n\n"
                "• ذخیره عکس زمان‌دار: رسانه قابل‌مشاهده را در محدوده مجاز "
                "به Saved Messages منتقل می‌کند.\n"
                "• ضدحذف: پیام‌های عادی را موقت نگه می‌دارد و در صورت حذف "
                "نسخه ثبت‌شده را ذخیره می‌کند؛ پیوی، گروه و کانال جداست.\n"
                "• سقف رسانه: فایل بزرگ‌تر از مقدار انتخابی ذخیره نمی‌شود.\n"
                "• مدت نگهداری: نسخه‌های موقت قدیمی را برای کنترل فضا پاک "
                "می‌کند؛ Saved Messages دست‌نخورده می‌ماند.\n"
                "• امضای خودکار: متن دلخواه را به انتهای پیام خروجی اضافه "
                "می‌کند.\n"
                "• حالت متن: ظاهر پیام‌های خروجی را بولد، ایتالیک، کد، "
                "زیرخط، خط‌خورده یا اسپویلر می‌کند.\n"
                "• واترمارک: متن ثبت‌شده را فقط با دستور `لوگو` روی عکس "
                "ریپلای‌شده قرار می‌دهد.\n"
                "• صف ارسال: فاصله ارسال‌ها را یکپارچه نگه می‌دارد؛ در "
                "FloodWait متوقف می‌شود و همان کار را بعداً ادامه می‌دهد."
            ),
        }
        if page in option_pages:
            text = f"{notice_text}{option_pages[page]}"
            if page == "option_guide":
                rows = [
                    [
                        glass_button(
                            "🔐 امنیت و قفل‌ها",
                            owner_id,
                            "option_security",
                            style="danger",
                        ),
                        glass_button(
                            "👤 پروفایل و وضعیت",
                            owner_id,
                            "option_presence",
                            style="primary",
                        ),
                    ],
                    [
                        glass_button(
                            "💬 پیام و اتوماسیون",
                            owner_id,
                            "option_messages",
                            style="success",
                        ),
                        glass_button(
                            "👥 گروه و رابطه‌ها",
                            owner_id,
                            "option_groups",
                            style="primary",
                        ),
                    ],
                    [
                        glass_button(
                            "🗄 ذخیره‌سازی و ظاهر",
                            owner_id,
                            "option_storage",
                            style="primary",
                        )
                    ],
                    [
                        glass_button(
                            "🔙 بازگشت به پنل",
                            owner_id,
                            "home",
                            style="primary",
                        )
                    ],
                ]
            else:
                rows = [
                    [
                        glass_button(
                            "❓ فهرست توضیحات",
                            owner_id,
                            "option_guide",
                            style="primary",
                        ),
                        glass_button(
                            "🔙 بازگشت به پنل",
                            owner_id,
                            "home",
                            style="primary",
                        ),
                    ]
                ]
            return text, InlineKeyboardMarkup(rows)

        guide_pages = {
            "guide": (
                "📚 راهنمای سلف\n"
                "━━━━━━━━━━━━━━\n\n"
                "روش استفاده خیلی ساده است:\n\n"
                "1️⃣ عبارت «پنل» را در چت دلخواه بفرستید.\n"
                "2️⃣ تنظیمات عمومی را با دکمه‌های پنل انجام دهید.\n"
                "3️⃣ دستورهای مرتبط با یک نفر را روی پیام او ریپلای کنید.\n\n"
                "✅ دستورهای فارسی با نقطه و بدون نقطه اجرا می‌شوند.\n"
                "نمونه: `ویس زن سلام` و `.ویس زن سلام` هر دو درست‌اند.\n\n"
                "دستورهای پایه:\n"
                "• `پنل` — بازکردن همین پنل\n"
                "• `وضعیت` — وضعیت فنی سلف\n"
                "• `راهنما` — راهنمای سریع متنی\n\n"
                "موضوع راهنما را انتخاب کنید 👇"
            ),
            "guide_people": (
                "💚 دوستان و کاربران\n"
                "━━━━━━━━━━━━━━\n\n"
                "این دستورها را روی پیام کاربر ریپلای کنید:\n\n"
                "• `تنظیم دوست` — افزودن به دوستان و شروع ریپلای صمیمی\n"
                "• `حذف دوست` — توقف پاسخ‌های صمیمی\n"
                "• `تنظیم دشمن` — افزودن به دشمنان\n"
                "• `حذف دشمن` — حذف از دشمنان\n"
                "• `اطلاعات` — نمایش اطلاعات حساب\n"
                "• `ری‌اکت ❤️` — ری‌اکت روی همان پیام\n"
                "• `سکوت 10` — سکوت ده‌دقیقه‌ای در گروه\n"
                "• `رفع سکوت` — برداشتن محدودیت\n"
                "• `بلاک` / `آنبلاک` — مدیریت کاربر\n\n"
                "💡 متن‌های دلخواه دوست و دشمن را از صفحه «دوست و دشمن» "
                "مدیریت کنید."
            ),
            "guide_groups": (
                "🛡 گروه، قفل و فیلتر\n"
                "━━━━━━━━━━━━━━\n\n"
                "• `تگ` — تگ اعضای گروه\n"
                "• `تگ ادمین ها` — تگ مدیران\n"
                "• `پین` / `آنپین` — با ریپلای روی پیام\n"
                "• `اخراج` — با ریپلای یا آیدی\n"
                "• `سکوت 10` / `رفع سکوت` — با ریپلای\n"
                "• `گزارش مدیران` — با ریپلای روی پیام\n"
                "• `خوش آمد روشن` / `خوش آمد خاموش`\n"
                "• `خداحافظی روشن` / `خداحافظی خاموش`\n"
                "• `قفل لینک روشن` / `قفل لینک خاموش`\n"
                "• `قفل عکس روشن` / `قفل عکس خاموش`\n"
                "• `قفل ویدیو روشن` / `قفل ویدیو خاموش`\n"
                "• `قفل فوروارد روشن` / `قفل فوروارد خاموش`\n"
                "• `فیلتر افزودن عبارت|حذف`\n"
                "• `فیلتر حذف شناسه`\n"
                "• `عضویت اجباری روشن` / `عضویت اجباری خاموش`\n"
                "• `سین گروه روشن` / `سین گروه خاموش`\n"
                "• `.clean 20` — حذف چند پیام اخیر\n\n"
                "همه قفل‌ها از بخش «🛡 قفل‌ها و فیلتر» نیز قابل تنظیم‌اند."
            ),
            "guide_tools": (
                "🧰 ابزار و رسانه\n"
                "━━━━━━━━━━━━━━\n\n"
                "دستورهای دارای رسانه را روی پیام موردنظر ریپلای کنید:\n\n"
                "• `جستجو متن` — جست‌وجوی پیام‌های چت\n"
                "• `شناسه پیام` — نمایش آیدی پیام، کاربر و گروه\n"
                "• `ذخیره پیام` — انتقال پیام به Saved Messages\n"
                "• `دانلود پیام لینک/آیدی` — دریافت یک پیام مشخص\n"
                "• `ارسال یکباره 18:30 | متن`\n"
                "• `اکشن تایپ 10`؛ ویس، ویدیو، عکس، فایل، استیکر یا بازی\n"
                "• `کیوآر متن` — ساخت QR Code محلی\n"
                "• `دانلود` — ذخیره عکس، ویدیو، ویس یا فایل\n"
                "• `استیکر` — ساخت استیکر از پیام ریپلای‌شده با QuotLyBot\n"
                "• `تنظیم تاس ۱` — تنظیم تاس بعدی همین چت برای شعبده\n"
                "• `تاس ۱` — اجرای مستقیم تاس نمایشی\n"
                "• `لغو تاس` — لغو تنظیم تک‌بارمصرف تاس\n"
                "• `تنظیم کازینو جکپات` — تنظیم کازینوی بعدی همین چت\n"
                "• `کازینو ۶۴` — اجرای مستقیم کازینوی نمایشی\n"
                "• `لغو کازینو` — لغو تنظیم تک‌بارمصرف کازینو\n"
                "• `دوز` — شروع بازی با ریپلای روی پیام حریف\n"
                "• `دوز ۱` تا `دوز ۹`؛ `لغو دوز` — پایان بازی\n"
                "• `متن لوگو متن` — تعیین واترمارک\n"
                "• `لوگو` — اعمال واترمارک روی عکس\n"
                "• `ترجمه en` — ترجمه متن پیام\n"
                "• `حساب 2+2*3` — ماشین‌حساب امن\n"
                "• `ویس زن متن` / `ویس مرد متن`\n"
                "• `ویس ذخیره کلید` — افزودن به بانک ویس\n"
                "• `ویس سرچ کلید` — جست‌وجوی بانک ویس\n"
                "• `آهنگ نام` — جست‌وجوی آهنگ\n"
                "• `قیمت btc` / `ارز USD EUR`\n"
                "• `اسکرین https://example.com`"
            ),
            "guide_automation": (
                "⚡ اتوماسیون و ضدحذف\n"
                "━━━━━━━━━━━━━━\n\n"
                "• `قفل پیوی روشن` / `قفل پیوی خاموش`\n"
                "• `مجاز افزودن` / `مجاز حذف` — با ریپلای\n"
                "• `ضد ویرایش پیوی روشن` / `ضد ویرایش پیوی خاموش`\n"
                "• `ضد ویرایش گروه روشن` / `ضد ویرایش گروه خاموش`\n"
                "• `ری‌اکت خودکار روشن ❤️`\n"
                "• `محبت دوست روشن` / `محبت دوست خاموش`\n"
                "• `پروفایل افزودن` — با ریپلای\n"
                "• `کامنت اول افزودن @channel|متن|2`\n"
                "• فرم‌ساز سفارش — ساخت فرم‌های مرحله‌ای پیوی از پنل\n"
                "• تغییر وضعیت فرم — ریپلای در Saved Messages و ارسال "
                "«ارسال شده» یا «تکمیل شده»\n"
                "• `.save on` / `.save off` — ذخیره عکس زمان‌دار\n"
                "• `ضدحذف روشن` / `ضدحذف خاموش`\n"
                "• `ضدحذف پیوی روشن` و حالت گروه/کانال\n"
                "• `ضدحذف حجم 50`\n"
                "• `ضدحذف نگهداری 7`\n"
                "• `ضدحذف پاکسازی`\n"
                "• `ارسال 5 متن` — ارسال دوره‌ای در همین چت\n"
                "• `توقف ارسال` — توقف ارسال دوره‌ای\n\n"
                "ارسال زمان‌بندی‌شده را می‌توانید مستقیماً از پنل تنظیم کنید."
            ),
        }
        text = f"{notice_text}{guide_pages[page]}"
        if page == "guide":
            rows = [
                [
                    copy_button("📋 کپی «پنل»", "پنل"),
                    copy_button("📋 کپی «وضعیت»", "وضعیت"),
                ],
                [
                    glass_button(
                        "💚 دوستان و کاربران",
                        owner_id,
                        "guide_people",
                        style="success",
                    ),
                    glass_button(
                        "🛡 گروه و قفل‌ها",
                        owner_id,
                        "guide_groups",
                        style="primary",
                    ),
                ],
                [
                    glass_button(
                        "🧰 ابزار و رسانه",
                        owner_id,
                        "guide_tools",
                        style="primary",
                    ),
                    glass_button(
                        "⚡ اتوماسیون",
                        owner_id,
                        "guide_automation",
                        style="success",
                    ),
                ],
                [
                    glass_button(
                        "🔙 بازگشت به پنل",
                        owner_id,
                        "home",
                        style="primary",
                    )
                ],
            ]
        else:
            command_copy_rows = {
                "guide_people": [
                    [
                        copy_button("📋 تنظیم دوست", "تنظیم دوست"),
                        copy_button("📋 اطلاعات", "اطلاعات"),
                    ],
                    [
                        copy_button("📋 سکوت 10", "سکوت 10"),
                        copy_button("📋 رفع سکوت", "رفع سکوت"),
                    ],
                ],
                "guide_groups": [
                    [
                        copy_button("📋 تگ", "تگ"),
                        copy_button("📋 تگ ادمین‌ها", "تگ ادمین ها"),
                    ],
                    [
                        copy_button("📋 قفل لینک روشن", "قفل لینک روشن"),
                        copy_button("📋 .clean 20", ".clean 20"),
                    ],
                ],
                "guide_tools": [
                    [
                        copy_button("📋 شناسه پیام", "شناسه پیام"),
                        copy_button("📋 دانلود", "دانلود"),
                    ],
                    [
                        copy_button("📋 ترجمه en", "ترجمه en"),
                        copy_button("📋 حساب", "حساب 2+2*3"),
                    ],
                ],
                "guide_automation": [
                    [
                        copy_button("📋 قفل پیوی روشن", "قفل پیوی روشن"),
                        copy_button("📋 ضدحذف روشن", "ضدحذف روشن"),
                    ],
                    [
                        copy_button("📋 .save on", ".save on"),
                        copy_button("📋 توقف ارسال", "توقف ارسال"),
                    ],
                ],
            }
            rows = command_copy_rows.get(page, []) + [
                [
                    glass_button(
                        "📚 فهرست راهنما",
                        owner_id,
                        "guide",
                        style="primary",
                    ),
                    glass_button(
                        "🔙 بازگشت به پنل",
                        owner_id,
                        "home",
                        style="primary",
                    ),
                ]
            ]
        return text, InlineKeyboardMarkup(rows)

    @staticmethod
    def state(settings, key: str) -> str:
        return "✅ فعال" if settings.get(key) == "on" else "❌ غیرفعال"

    @staticmethod
    def state_en(settings, key: str) -> str:
        return "✅ Enabled" if settings.get(key) == "on" else "❌ Disabled"

    def toggle_button_locale(
        self,
        owner_id: int,
        key: str,
        settings,
        english: bool,
    ):
        if not english:
            return self.toggle_button(owner_id, key, settings)
        labels = {
            "private_lock_enabled": "Private lock",
            "private_lock_delete_unknown": "Delete unknown",
            "private_lock_warn_before_block": "Warn before block",
            "anti_edit_private": "Anti-edit PM",
            "anti_edit_groups": "Anti-edit groups",
            "welcome_enabled": "Welcome",
            "goodbye_enabled": "Goodbye",
            "timename": "Clock in name",
            "timebio": "Clock in bio",
            "analog_clock_enabled": "Analog photo clock",
            "presence_emoji_enabled": "Presence emoji",
            "presence_auto_detect": "Auto presence detection",
        }
        enabled = settings.get(key) == "on"
        icon = "✅" if enabled else "❌"
        action = "Disable" if enabled else "Enable"
        style = "danger" if enabled else "success"
        return glass_button(
            f"{icon} {action} {labels.get(key, key)}",
            owner_id,
            f"toggle.{key}",
            style=style,
        )

    def toggle_button(self, owner_id: int, key: str, settings):
        enabled = settings.get(key) == "on"
        icon = "✅" if enabled else "❌"
        style = "danger" if enabled else "success"
        action = "غیرفعال‌کردن" if enabled else "فعال‌کردن"
        return glass_button(
            f"{icon} {action} {TOGGLE_LABELS[key]}",
            owner_id,
            f"toggle.{key}",
            style=style,
        )

    def run(self) -> None:
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Inline self-bot helper")
    parser.add_argument(
        "--data-dir",
        default=os.getenv("BOT_DATA_DIR"),
        help="مسیر پوشه داده مشترک",
    )
    parser.add_argument("--status-file", help="مسیر فایل وضعیت هلپر")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    data_dir = Path(args.data_dir) if args.data_dir else Path(__file__).parent / "data"
    users_db = data_dir / "users.db"
    config = get_helper_config(users_db)
    token = config.get("token", "")

    if not config.get("enabled"):
        raise RuntimeError("بات هلپر در تنظیمات مرکزی غیرفعال است.")
    if not token:
        raise RuntimeError("توکن بات هلپر در تنظیمات مرکزی ثبت نشده است.")

    bot = HelperPanelBot(token, data_dir, args.status_file)
    bot.run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        status_arg = None
        try:
            status_arg = parse_arguments().status_file
        except SystemExit:
            pass
        write_runtime_status(status_arg, "failed", str(exc))
        print(f"❌ خطای بات هلپر: {exc}", file=sys.stderr)
        raise

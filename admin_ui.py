"""Telegram inline UI for the administration control center."""

from __future__ import annotations

import asyncio
import json
import io
import sqlite3
from db_utils import connect as db_connect
from datetime import datetime
from pathlib import Path

import psutil
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop

from admin_center import (
    ADMIN_ROLES,
    CURRENT_RELEASE,
    FEATURE_CATALOG,
    normalize_segment,
    parse_datetime,
    utcnow,
)
from control_store import (
    get_admin_ids,
    get_financial_config,
    get_helper_config,
    self_database_path,
    set_app_settings,
)


ROLE_LABELS = {
    "owner": "مالک",
    "manager": "مدیر کامل",
    "finance": "مالی",
    "support": "پشتیبانی",
    "operator": "اپراتور",
    "viewer": "فقط مشاهده",
}

STATUS_LABELS = {
    "pending": "در انتظار",
    "running": "در حال ارسال",
    "completed": "تکمیل",
    "cancelled": "لغوشده",
    "failed": "ناموفق",
}

CC_WIZARD_STEPS = {
    "plan": [
        ("name", "نام پلن را بفرستید؛ نمونه: حرفه‌ای"),
        ("duration_days", "مدت پلن را به روز بفرستید؛ عدد ۰ یعنی دائمی."),
        ("price_coins", "قیمت پلن را به سکه و فقط عددی بفرستید."),
        ("trial_days", "تعداد روز آزمایشی را بفرستید؛ برای نداشتن، ۰."),
    ],
    "assign": [
        ("user_id", "آیدی عددی کاربر را بفرستید."),
        ("plan_id", "شماره پلن را بفرستید."),
        (
            "auto_renew",
            "تمدید خودکار فعال باشد؟ «بله» یا «خیر» بفرستید.",
        ),
    ],
    "discount": [
        ("code", "کد تخفیف را بفرستید؛ نمونه: SUMMER"),
        ("percent", "درصد تخفیف را بین ۱ تا ۱۰۰ بفرستید."),
        ("max_uses", "سقف مصرف را بفرستید؛ عدد ۰ یعنی نامحدود."),
        (
            "expires_at",
            "تاریخ انقضا را مانند 2026-08-31 23:59 بفرستید؛ "
            "برای بدون انقضا، «ندارد» بفرستید.",
        ),
    ],
    "feature": [
        (
            "scope",
            "محدوده را بفرستید: عمومی، پلن یا کاربر.",
        ),
        (
            "scope_id",
            "آیدی عددی پلن یا کاربر را بفرستید.",
        ),
        (
            "feature_key",
            "کلید قابلیت را بفرستید:\n" + "، ".join(FEATURE_CATALOG),
        ),
        (
            "enabled",
            "وضعیت قابلیت را بفرستید: روشن یا خاموش.",
        ),
    ],
    "broadcast": [
        (
            "segment",
            "گروه هدف را بفرستید: همه، فعال، منقضی یا مسدود.",
        ),
        (
            "scheduled_at",
            "زمان ارسال را بفرستید؛ «الان» یا مانند "
            "2026-07-30 18:30.",
        ),
        ("body", "متن کامل پیام را بفرستید."),
    ],
    "balance": [
        ("user_id", "آیدی عددی کاربر را بفرستید."),
        ("amount", "مبلغ تغییر را مثبت یا منفی بفرستید؛ نمونه: -50"),
        ("note", "توضیح این تغییر موجودی را بفرستید."),
    ],
    "role": [
        ("user_id", "آیدی عددی ادمین را بفرستید."),
        (
            "role",
            "نقش را بفرستید: manager، finance، support، "
            "operator یا viewer.",
        ),
    ],
}


def cc_button(text: str, data: str, style: str | None = None):
    api_kwargs = {"style": style} if style else None
    return InlineKeyboardButton(
        text=text,
        callback_data=data,
        api_kwargs=api_kwargs,
    )


def cc_back(data: str = "admin:cc:home"):
    return [cc_button("🔙 بازگشت", data, "primary")]


class AdminPanelMixin:
    """Mixin used by :class:`main_bot.TelegramAuthBot`."""

    admin_store: object
    owner_id: int
    users_db: Path
    data_dir: Path

    def cc_allowed(self, user_id: int, permission: str) -> bool:
        return self.admin_store.can(user_id, self.owner_id, permission)

    def control_center_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    cc_button("📊 داشبورد", "admin:cc:dashboard", "success"),
                    cc_button("👥 کاربران", "admin:users:page:0", "primary"),
                ],
                [
                    cc_button(
                        "🤖 مدیریت سلف‌ها",
                        "admin:selfs:page:0",
                        "primary",
                    ),
                    cc_button(
                        "💎 اشتراک و تعرفه",
                        "admin:cc:subscriptions",
                        "primary",
                    ),
                ],
                [
                    cc_button("💳 مالی و پرداخت", "admin:cc:finance", "primary"),
                    cc_button("🧩 امکانات سلف", "admin:cc:features", "primary"),
                ],
                [
                    cc_button(
                        "📣 پیام‌رسانی", "admin:cc:broadcasts", "primary"
                    ),
                    cc_button("🎧 پشتیبانی", "admin:cc:support", "primary"),
                ],
                [
                    cc_button(
                        "📢 عضویت اجباری", "admin:cc:forcejoin", "primary"
                    ),
                    cc_button("⚙️ تنظیمات سیستم", "admin:cc:settings", "primary"),
                ],
                [
                    cc_button(
                        "🛡 امنیت و ادمین‌ها",
                        "admin:cc:security",
                        "primary",
                    ),
                    cc_button("📋 گزارش و خطا", "admin:cc:reports", "primary"),
                ],
                [
                    cc_button(
                        "🔄 بروزرسانی پنل",
                        "admin:cc:home",
                        "success",
                    )
                ],
            ]
        )

    def control_center_text(self, notice: str | None = None) -> str:
        stats = self.admin_store.dashboard()
        helper = get_helper_config(self.users_db)
        helper_state = (
            "🟢 فعال"
            if helper.get("enabled") and self.helper_is_running()
            else "🔴 متوقف"
        )
        prefix = f"{notice}\n\n" if notice else ""
        return (
            f"{prefix}👑 مرکز مدیریت ربات سلف‌ساز\n\n"
            f"👥 کاربران: {stats['users']:,} "
            f"(مسدود: {stats['blocked_users']:,})\n"
            f"🤖 سلف‌ها: {stats['selfs']:,} "
            f"(روشن: {stats['enabled_selfs']:,})\n"
            f"🎧 تیکت باز: {stats['open_tickets']:,}\n"
            f"📣 ارسال در انتظار: {stats['pending_broadcasts']:,}\n"
            f"🧩 هلپر: {helper_state}\n\n"
            "هر بخش فقط ابزارهای مرتبط خودش را نمایش می‌دهد."
        )

    def cc_dashboard_view(self) -> tuple[str, InlineKeyboardMarkup]:
        stats = self.admin_store.dashboard()
        (
            _,
            _,
            monitored_total,
            running_selfs,
            watchdog_enabled,
            self_errors,
            _,
        ) = self.selfbot_page(0)
        releases = self.admin_store.release_summary(CURRENT_RELEASE)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(self.data_dir))
        cpu = psutil.cpu_percent(interval=None)
        uptime_seconds = max(
            0, int(datetime.now().timestamp() - psutil.boot_time())
        )
        uptime_hours = uptime_seconds // 3600
        text = (
            "📊 داشبورد مدیریتی\n\n"
            f"👥 کل کاربران: {stats['users']:,}\n"
            f"⛔ کاربران مسدود: {stats['blocked_users']:,}\n"
            f"🤖 کل سلف‌ها: {stats['selfs']:,}\n"
            f"🟢 اجرای زنده: {running_selfs:,} از {monitored_total:,}\n"
            f"♻️ بازیابی خودکار: {watchdog_enabled:,}\n"
            f"🔴 سلف خطادار: {self_errors:,}\n"
            f"⏳ منقضی‌شده: {stats['expired_selfs']:,}\n"
            f"📦 نسخه جاری: {CURRENT_RELEASE} "
            f"(قدیمی: {releases['outdated']:,})\n"
            f"🎧 تیکت‌های باز: {stats['open_tickets']:,}\n"
            f"⚠️ خطاهای امروز: {stats['errors_today']:,}\n\n"
            "💰 درآمد تأییدشده\n"
            f"├ امروز: {stats['revenue_today']:,} تومان\n"
            f"└ این ماه: {stats['revenue_month']:,} تومان\n\n"
            "🖥 وضعیت سرور\n"
            f"├ CPU: {cpu:.1f}٪\n"
            f"├ RAM: {memory.percent:.1f}٪\n"
            f"├ دیسک: {disk.percent:.1f}٪\n"
            f"└ زمان روشن‌بودن سرور: {uptime_hours:,} ساعت"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    cc_button(
                        "🔄 بروزرسانی",
                        "admin:cc:dashboard",
                        "success",
                    ),
                    cc_button(
                        "📋 آخرین خطاها",
                        "admin:cc:events",
                        "primary",
                    ),
                ],
                [
                    cc_button(
                        "🤖 مانیتورینگ و نسخه‌ها",
                        "admin:selfs:page:0",
                        "primary",
                    )
                ],
                cc_back(),
            ]
        )
        return text, keyboard

    def cc_subscriptions_view(self) -> tuple[str, InlineKeyboardMarkup]:
        plans = self.admin_store.list_plans()
        lines = ["💎 اشتراک و تعرفه‌ها", ""]
        rows = []
        for plan in plans:
            duration = (
                "دائمی"
                if int(plan["duration_days"]) == 0
                else f"{int(plan['duration_days'])} روز"
            )
            state = "فعال" if plan["is_active"] else "غیرفعال"
            lines.append(
                f"#{plan['id']} {plan['name']} — {duration} — "
                f"{int(plan['price_coins']):,} سکه — {state}"
            )
            rows.append(
                [
                    cc_button(
                        f"{'✅' if plan['is_active'] else '⛔'} "
                        f"{str(plan['name'])[:24]}",
                        f"admin:cc:plan:{int(plan['id'])}",
                        "primary",
                    )
                ]
            )
        rows.extend(
            [
                [
                    cc_button(
                        "➕ ساخت پلن",
                        "admin:cc:input:plan",
                        "success",
                    ),
                    cc_button(
                        "👤 تخصیص به کاربر",
                        "admin:cc:input:assign",
                        "success",
                    ),
                ],
                [
                    cc_button(
                        "🏷 کدهای تخفیف",
                        "admin:cc:discounts",
                        "primary",
                    )
                ],
                cc_back(),
            ]
        )
        lines.extend(
            [
                "",
                "مدت صفر به‌معنی پلن دائمی است. امکانات هر پلن از بخش "
                "«امکانات سلف» قابل محدودکردن است.",
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    def cc_plan_view(self, plan_id: int) -> tuple[str, InlineKeyboardMarkup]:
        plan = self.admin_store.get_plan(plan_id)
        if not plan:
            return "❌ پلن پیدا نشد.", InlineKeyboardMarkup(
                [cc_back("admin:cc:subscriptions")]
            )
        features = json.loads(plan["feature_keys"] or "[]")
        text = (
            f"💎 پلن #{plan['id']} — {plan['name']}\n\n"
            f"مدت: {'دائمی' if not plan['duration_days'] else str(plan['duration_days']) + ' روز'}\n"
            f"قیمت: {int(plan['price_coins']):,} سکه\n"
            f"دوره آزمایشی: {int(plan['trial_days']):,} روز\n"
            f"وضعیت: {'فعال' if plan['is_active'] else 'غیرفعال'}\n"
            f"تعداد قابلیت‌های پایه: {len(features)}"
        )
        return text, InlineKeyboardMarkup(
            [
                [
                    cc_button(
                        "⛔ غیرفعال‌کردن"
                        if plan["is_active"]
                        else "✅ فعال‌کردن",
                        f"admin:cc:plan:toggle:{plan_id}",
                        "danger" if plan["is_active"] else "success",
                    )
                ],
                [
                    cc_button(
                        "🧩 تنظیم امکانات این پلن",
                        "admin:cc:input:feature",
                        "primary",
                    )
                ],
                cc_back("admin:cc:subscriptions"),
            ]
        )

    def cc_discounts_view(self) -> tuple[str, InlineKeyboardMarkup]:
        codes = self.admin_store.list_discounts()
        lines = ["🏷 کدهای تخفیف", ""]
        for item in codes:
            limit = (
                "نامحدود"
                if not item["max_uses"]
                else f"{item['used_count']}/{item['max_uses']}"
            )
            lines.append(
                f"{'✅' if item['is_active'] else '⛔'} {item['code']} — "
                f"{item['percent']}٪ — مصرف: {limit}"
            )
        if not codes:
            lines.append("هنوز کد تخفیفی ساخته نشده است.")
        return "\n".join(lines), InlineKeyboardMarkup(
            [
                [
                    cc_button(
                        "➕ ساخت کد تخفیف",
                        "admin:cc:input:discount",
                        "success",
                    )
                ],
                cc_back("admin:cc:subscriptions"),
            ]
        )

    def cc_finance_view(self) -> tuple[str, InlineKeyboardMarkup]:
        stats = self.admin_store.dashboard()
        financial = get_financial_config(self.users_db)
        with db_connect(self.users_db, timeout=10) as conn:
            transactions = int(
                conn.execute(
                    "SELECT COUNT(*) FROM balance_transactions"
                ).fetchone()[0]
            )
            wallet_total = int(
                conn.execute(
                    "SELECT COALESCE(SUM(coins), 0) FROM users"
                ).fetchone()[0]
            )
        text = (
            "💳 مالی و پرداخت\n\n"
            f"قیمت هر سکه: {financial['coin_price']:,} تومان\n"
            f"مجموع موجودی کاربران: {wallet_total:,} سکه\n"
            f"تعداد گردش‌های کیف پول: {transactions:,}\n"
            f"درآمد امروز: {stats['revenue_today']:,} تومان\n"
            f"درآمد ماه: {stats['revenue_month']:,} تومان"
        )
        return text, InlineKeyboardMarkup(
            [
                [
                    cc_button(
                        "🧾 رسیدهای در انتظار",
                        "admin:receipts:page:0",
                        "success",
                    ),
                    cc_button(
                        "⚙️ تنظیمات پرداخت",
                        "admin:finance",
                        "primary",
                    ),
                ],
                [
                    cc_button(
                        "🏷 کد تخفیف",
                        "admin:cc:discounts",
                        "primary",
                    ),
                    cc_button(
                        "➕/➖ اصلاح موجودی",
                        "admin:cc:input:balance",
                        "primary",
                    ),
                ],
                cc_back(),
            ]
        )

    def cc_features_view(self) -> tuple[str, InlineKeyboardMarkup]:
        states = self.admin_store.global_feature_states()
        lines = [
            "🧩 مدیریت امکانات سلف",
            "",
            "وضعیت عمومی روی همه سلف‌ها اعمال می‌شود. تنظیم کاربر یا پلن "
            "اولویت بالاتری دارد.",
            "",
        ]
        rows = []
        for key, (label, _) in FEATURE_CATALOG.items():
            enabled = states[key]
            lines.append(f"{'✅' if enabled else '⛔'} {label}")
            rows.append(
                [
                    cc_button(
                        f"{'✅' if enabled else '⛔'} {label}",
                        f"admin:cc:feature:global:{key}",
                        "success" if enabled else "danger",
                    )
                ]
            )
        rows.extend(
            [
                [
                    cc_button(
                        "🎯 تنظیم برای پلن/کاربر",
                        "admin:cc:input:feature",
                        "primary",
                    )
                ],
                cc_back(),
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    def cc_broadcasts_view(self) -> tuple[str, InlineKeyboardMarkup]:
        items = self.admin_store.list_broadcasts()
        lines = ["📣 پیام‌رسانی و ارسال همگانی", ""]
        rows = []
        for item in items:
            status = STATUS_LABELS.get(item["status"], item["status"])
            lines.append(
                f"#{item['id']} — {status} — هدف: {item['segment']} — "
                f"موفق {item['success_count']:,} / ناموفق {item['failed_count']:,}"
            )
            if item["status"] in {"pending", "running"}:
                rows.append(
                    [
                        cc_button(
                            f"❌ لغو ارسال #{item['id']}",
                            f"admin:cc:broadcast:cancel:{int(item['id'])}",
                            "danger",
                        )
                    ]
                )
        if not items:
            lines.append("ارسالی ثبت نشده است.")
        rows.extend(
            [
                [
                    cc_button(
                        "➕ ارسال جدید",
                        "admin:cc:input:broadcast",
                        "success",
                    )
                ],
                cc_back(),
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    def cc_support_view(self) -> tuple[str, InlineKeyboardMarkup]:
        tickets = self.admin_store.list_tickets()
        lines = ["🎧 پشتیبانی و تیکت‌ها", ""]
        rows = []
        for item in tickets:
            display = (
                item["first_name"]
                or (f"@{item['username']}" if item["username"] else "")
                or str(item["user_id"])
            )
            preview = str(item["last_message"] or "").replace("\n", " ")[:45]
            lines.append(f"#{item['id']} — {display}: {preview}")
            rows.append(
                [
                    cc_button(
                        f"🎫 #{item['id']} | {str(display)[:20]}",
                        f"admin:cc:ticket:{int(item['id'])}",
                        "primary",
                    )
                ]
            )
        if not tickets:
            lines.append("تیکت بازی وجود ندارد.")
        rows.append(cc_back())
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    def cc_ticket_view(
        self, ticket_id: int, notice: str = ""
    ) -> tuple[str, InlineKeyboardMarkup]:
        ticket, messages = self.admin_store.ticket(ticket_id)
        if not ticket:
            return "❌ تیکت پیدا نشد.", InlineKeyboardMarkup(
                [cc_back("admin:cc:support")]
            )
        display = (
            ticket["first_name"]
            or (f"@{ticket['username']}" if ticket["username"] else "")
            or str(ticket["user_id"])
        )
        lines = [
            *( [notice, ""] if notice else [] ),
            f"🎫 تیکت #{ticket['id']}",
            f"کاربر: {display} ({ticket['user_id']})",
            f"وضعیت: {ticket['status']}",
            "",
        ]
        for message in messages:
            sender = "👤 کاربر" if message["sender_type"] == "user" else "🎧 مدیر"
            lines.append(f"{sender}: {message['body']}")
        rows = []
        if ticket["status"] != "closed":
            rows.extend(
                [
                    [
                        cc_button(
                            "✍️ پاسخ",
                            f"admin:cc:ticket:reply:{ticket_id}",
                            "success",
                        ),
                        cc_button(
                            "✅ بستن تیکت",
                            f"admin:cc:ticket:close:{ticket_id}",
                            "danger",
                        ),
                    ]
                ]
            )
        rows.append(cc_back("admin:cc:support"))
        return "\n".join(lines)[:4000], InlineKeyboardMarkup(rows)

    def cc_forcejoin_view(self) -> tuple[str, InlineKeyboardMarkup]:
        channels = self.admin_store.list_force_join_channels()
        lines = [
            "📢 عضویت اجباری چندکاناله",
            "",
            "کاربر باید عضو تمام کانال‌های فعال باشد.",
            "",
        ]
        rows = []
        for channel in channels:
            active = bool(channel["is_active"])
            lines.append(
                f"#{channel['id']} {'✅' if active else '⛔'} "
                f"{channel['title']} — {channel['chat_id']}"
            )
            rows.append(
                [
                    cc_button(
                        "⛔ خاموش" if active else "✅ روشن",
                        f"admin:cc:join:toggle:{int(channel['id'])}",
                        "danger" if active else "success",
                    ),
                    cc_button(
                        "🗑 حذف",
                        f"admin:cc:join:delete:{int(channel['id'])}",
                        "danger",
                    ),
                ]
            )
        if not channels:
            lines.append("کانالی ثبت نشده است.")
        rows.extend(
            [
                [
                    cc_button(
                        "➕ افزودن کانال",
                        "admin:join:set",
                        "success",
                    )
                ],
                cc_back(),
            ]
        )
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    def cc_settings_view(self) -> tuple[str, InlineKeyboardMarkup]:
        with db_connect(self.users_db, timeout=10) as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'maintenance_mode'"
            ).fetchone()
        maintenance = bool(row and str(row[0]).lower() in {"1", "on", "true"})
        helper = get_helper_config(self.users_db)
        helper_running = bool(
            helper.get("enabled") and self.helper_is_running()
        )
        if helper_running:
            helper_state = "🟢 روشن"
            helper_action = "admin:helper:stop"
            helper_action_label = "🔴 خاموش‌کردن هلپر"
            helper_action_style = "danger"
        elif helper.get("enabled"):
            helper_state = "🟠 فعال ولی متوقف"
            helper_action = "admin:helper:restart"
            helper_action_label = "🟢 راه‌اندازی هلپر"
            helper_action_style = "success"
        else:
            helper_state = "🔴 خاموش"
            helper_action = "admin:helper:restart"
            helper_action_label = "🟢 روشن‌کردن هلپر"
            helper_action_style = "success"
        text = (
            "⚙️ تنظیمات سیستم\n\n"
            f"حالت تعمیرات: {'🟠 فعال' if maintenance else '🟢 غیرفعال'}\n"
            f"هلپر: {helper_state}\n\n"
            "متن‌ها، دکمه‌ها، آیدی‌ها و هویت ربات از بخش‌های زیر مدیریت "
            "می‌شوند."
        )
        return text, InlineKeyboardMarkup(
            [
                [
                    cc_button(
                        "🤖 نام و منوی استارت",
                        "admin:startmenu",
                        "primary",
                    ),
                    cc_button(
                        "📝 متن پشتیبانی و قوانین",
                        "admin:content",
                        "primary",
                    ),
                ],
                [
                    cc_button(
                        "🆔 آیدی‌ها و برند",
                        "admin:identities",
                        "primary",
                    ),
                    cc_button(
                        "🔑 تنظیم هلپر",
                        "admin:helper:set",
                        "primary",
                    ),
                ],
                [
                    cc_button(
                        helper_action_label,
                        helper_action,
                        helper_action_style,
                    )
                ],
                [
                    cc_button(
                        "🟢 خروج از تعمیرات"
                        if maintenance
                        else "🟠 ورود به تعمیرات",
                        "admin:cc:maintenance",
                        "danger" if not maintenance else "success",
                    )
                ],
                [
                    cc_button(
                        "💾 بکاپ و بازیابی",
                        "admin:cc:backups",
                        "primary",
                    )
                ],
                cc_back(),
            ]
        )

    def cc_security_view(self) -> tuple[str, InlineKeyboardMarkup]:
        admins = sorted(get_admin_ids(self.users_db, self.owner_id))
        login = self.admin_store.login_security_summary()
        lines = [
            "🛡 امنیت و ادمین‌ها",
            "",
            "نقش‌ها دسترسی هر مدیر به بخش‌های پنل را محدود می‌کنند.",
            (
                f"امنیت ورود: {login['tracked']} کاربر پایش‌شده، "
                f"{login['blocked']} قفل موقت، "
                f"{login['failed']} تلاش ناموفق"
            ),
            "",
        ]
        for admin_id in admins:
            role = self.admin_store.admin_role(admin_id, self.owner_id)
            lines.append(
                f"{admin_id} — {ROLE_LABELS.get(role, role)}"
            )
        return "\n".join(lines), InlineKeyboardMarkup(
            [
                [
                    cc_button(
                        "👮 افزودن/حذف ادمین",
                        "admin:admins",
                        "primary",
                    ),
                    cc_button(
                        "🎚 تعیین نقش",
                        "admin:cc:input:role",
                        "primary",
                    ),
                ],
                [
                    cc_button(
                        "📜 فعالیت ادمین‌ها",
                        "admin:cc:audit",
                        "primary",
                    )
                ],
                cc_back(),
            ]
        )

    def cc_reports_view(self) -> tuple[str, InlineKeyboardMarkup]:
        stats = self.admin_store.dashboard()
        with db_connect(self.users_db, timeout=10) as conn:
            last_activity = conn.execute(
                "SELECT MAX(updated_at) FROM users"
            ).fetchone()[0]
            pending_receipts = conn.execute(
                "SELECT COUNT(*) FROM payment_receipts WHERE status = 'pending'"
            ).fetchone()[0]
        text = (
            "📋 گزارش و پایش\n\n"
            f"خطاهای امروز: {stats['errors_today']:,}\n"
            f"رسیدهای در انتظار: {int(pending_receipts):,}\n"
            f"سلف‌های منقضی: {stats['expired_selfs']:,}\n"
            f"آخرین فعالیت ثبت‌شده: {last_activity or 'ثبت نشده'}\n"
            f"درآمد ماه: {stats['revenue_month']:,} تومان"
        )
        return text, InlineKeyboardMarkup(
            [
                [
                    cc_button(
                        "⚠️ خطاهای اخیر",
                        "admin:cc:events",
                        "primary",
                    ),
                    cc_button(
                        "📜 فعالیت ادمین",
                        "admin:cc:audit",
                        "primary",
                    ),
                ],
                [
                    cc_button(
                        "💰 گزارش مالی ۳۰ روز",
                        "admin:cc:financial-report",
                        "primary",
                    )
                ],
                [
                    cc_button(
                        "💾 ساخت بکاپ",
                        "admin:cc:backup:create",
                        "success",
                    )
                ],
                cc_back(),
            ]
        )

    def cc_financial_report_view(self) -> tuple[str, InlineKeyboardMarkup]:
        summary = self.admin_store.financial_summary(30)
        text = (
            "💰 گزارش مالی ۳۰ روز اخیر\n\n"
            f"رسیدها: {summary['receipts']:,}\n"
            f"├ تأییدشده: {summary['approved']:,}\n"
            f"└ در انتظار: {summary['pending']:,}\n"
            f"درآمد تأییدشده: {summary['revenue']:,} تومان\n\n"
            f"گردش کیف پول: {summary['movements']:,}\n"
            f"├ افزایش موجودی: {summary['credits']:,} سکه\n"
            f"├ کسر موجودی: {summary['debits']:,} سکه\n"
            f"├ هزینه روزانه سلف: {summary['daily_fees']:,} سکه\n"
            f"└ تمدید خودکار: {summary['renewals']:,} سکه"
        )
        return text, InlineKeyboardMarkup(
            [cc_back("admin:cc:reports")]
        )

    def cc_events_view(self) -> tuple[str, InlineKeyboardMarkup]:
        events = self.admin_store.recent_events()
        lines = ["⚠️ رویدادها و خطاهای اخیر", ""]
        for event in events:
            lines.append(
                f"{event['created_at']} | {event['level']} | "
                f"{event['component']}: {str(event['message'])[:180]}"
            )
        if not events:
            lines.append("خطایی ثبت نشده است.")
        return "\n".join(lines)[:4000], InlineKeyboardMarkup(
            [cc_back("admin:cc:reports")]
        )

    def cc_audit_view(self) -> tuple[str, InlineKeyboardMarkup]:
        items = self.admin_store.recent_audit()
        lines = ["📜 فعالیت ادمین‌ها", ""]
        for item in items:
            try:
                detail = json.loads(item["detail_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                detail = {}
            change = ""
            if "before" in detail or "after" in detail:
                change = (
                    f" | قبل: {detail.get('before')} "
                    f"→ بعد: {detail.get('after')}"
                )
            lines.append(
                f"{item['created_at']} | مدیر {item['admin_id']} | "
                f"{item['action']} | {item['target_type']} {item['target_id']}"
                f"{change}"
            )
        if not items:
            lines.append("فعالیتی ثبت نشده است.")
        return "\n".join(lines)[:4000], InlineKeyboardMarkup(
            [cc_back("admin:cc:security")]
        )

    def cc_backups_view(
        self, notice: str = ""
    ) -> tuple[str, InlineKeyboardMarkup]:
        lines = [*( [notice, ""] if notice else [] ), "💾 بکاپ ابری", ""]
        lines.extend(
            [
                "بکاپ ZIP فقط در حافظه ساخته و مستقیم به تلگرام ارسال می‌شود.",
                "هیچ فایل بکاپی روی هاست نگهداری نمی‌شود.",
                "برای بازیابی، فایل دریافتی را در جای امن نگه دارید.",
            ]
        )
        rows = [
            [
                cc_button(
                    "☁️ ساخت و دریافت بکاپ",
                    "admin:cc:backup:create",
                    "success",
                )
            ],
            cc_back("admin:cc:settings"),
        ]
        return "\n".join(lines), InlineKeyboardMarkup(rows)

    @staticmethod
    def cc_wizard_back(action: str) -> str:
        return {
            "plan": "admin:cc:subscriptions",
            "assign": "admin:cc:subscriptions",
            "discount": "admin:cc:discounts",
            "feature": "admin:cc:features",
            "broadcast": "admin:cc:broadcasts",
            "balance": "admin:cc:finance",
            "role": "admin:cc:security",
            "search": "admin:users:page:0",
        }.get(action, "admin:cc:home")

    def cc_prompt(
        self,
        action: str,
        wizard: dict | None = None,
    ) -> tuple[str, InlineKeyboardMarkup]:
        if action == "search":
            text = (
                "🔎 جست‌وجوی کاربر\n\n"
                "آیدی عددی، نام کاربری، نام یا شماره را ارسال کنید."
            )
        else:
            steps = CC_WIZARD_STEPS[action]
            state = wizard or {"step": 0}
            step = min(max(0, int(state.get("step") or 0)), len(steps) - 1)
            _, prompt = steps[step]
            text = (
                f"🧭 مرحله {step + 1} از {len(steps)}\n\n"
                f"{prompt}\n\n"
                "برای لغو از دکمه بازگشت استفاده کنید."
            )
        return text, InlineKeyboardMarkup(
            [cc_back(self.cc_wizard_back(action))]
        )

    async def handle_control_center_callback(
        self, query, context, action: str
    ) -> None:
        user_id = int(query.from_user.id)
        permission = "dashboard"
        action_permissions = (
            (("admin:cc:user:", "admin:cc:input:search"), "users"),
            (
                (
                    "admin:cc:subscriptions",
                    "admin:cc:plan:",
                    "admin:cc:input:plan",
                    "admin:cc:input:assign",
                ),
                "subscriptions",
            ),
            (
                (
                    "admin:cc:finance",
                    "admin:cc:discounts",
                    "admin:cc:input:discount",
                    "admin:cc:input:balance",
                ),
                "finance",
            ),
            (
                (
                    "admin:cc:features",
                    "admin:cc:feature:",
                    "admin:cc:input:feature",
                ),
                "features",
            ),
            (
                (
                    "admin:cc:broadcasts",
                    "admin:cc:broadcast:",
                    "admin:cc:input:broadcast",
                ),
                "broadcast",
            ),
            (
                (
                    "admin:cc:support",
                    "admin:cc:ticket:",
                ),
                "support",
            ),
            (
                (
                    "admin:cc:forcejoin",
                    "admin:cc:join:",
                    "admin:cc:settings",
                    "admin:cc:security",
                    "admin:cc:maintenance",
                    "admin:cc:backups",
                    "admin:cc:backup:",
                    "admin:cc:input:role",
                ),
                "settings",
            ),
            (
                (
                    "admin:cc:reports",
                    "admin:cc:events",
                    "admin:cc:audit",
                    "admin:cc:financial-report",
                ),
                "reports",
            ),
        )
        for prefixes, required in action_permissions:
            if action.startswith(prefixes):
                permission = required
                break
        if not self.cc_allowed(user_id, permission):
            await query.answer("برای این بخش دسترسی ندارید.", show_alert=True)
            return
        await query.answer()
        context.user_data.pop("awaiting_cc_action", None)
        context.user_data.pop("cc_wizard", None)

        if action in {"admin:cc:home", "admin:cc"}:
            await query.edit_message_text(
                self.control_center_text(),
                reply_markup=self.control_center_keyboard(),
            )
            return

        view = None
        if action == "admin:cc:dashboard":
            view = self.cc_dashboard_view()
        elif action == "admin:cc:subscriptions":
            view = self.cc_subscriptions_view()
        elif action.startswith("admin:cc:plan:toggle:"):
            plan_id = int(action.rsplit(":", 1)[1])
            self.admin_store.toggle_plan(plan_id, user_id)
            view = self.cc_plan_view(plan_id)
        elif action.startswith("admin:cc:plan:"):
            view = self.cc_plan_view(int(action.rsplit(":", 1)[1]))
        elif action == "admin:cc:discounts":
            view = self.cc_discounts_view()
        elif action == "admin:cc:finance":
            view = self.cc_finance_view()
        elif action == "admin:cc:features":
            view = self.cc_features_view()
        elif action.startswith("admin:cc:feature:global:"):
            feature_key = action.rsplit(":", 1)[1]
            current = self.admin_store.global_feature_states()[feature_key]
            self.admin_store.set_feature_policy(
                "global", "", feature_key, not current, user_id
            )
            await self.apply_feature_policies_to_all()
            view = self.cc_features_view()
        elif action == "admin:cc:broadcasts":
            view = self.cc_broadcasts_view()
        elif action.startswith("admin:cc:broadcast:cancel:"):
            broadcast_id = int(action.rsplit(":", 1)[1])
            self.admin_store.cancel_broadcast(broadcast_id, user_id)
            view = self.cc_broadcasts_view()
        elif action == "admin:cc:support":
            view = self.cc_support_view()
        elif action.startswith("admin:cc:ticket:reply:"):
            ticket_id = int(action.rsplit(":", 1)[1])
            context.user_data["awaiting_cc_action"] = f"ticket_reply:{ticket_id}"
            await query.edit_message_text(
                f"✍️ پاسخ تیکت #{ticket_id}\n\nمتن پاسخ را ارسال کنید.",
                reply_markup=InlineKeyboardMarkup(
                    [cc_back(f"admin:cc:ticket:{ticket_id}")]
                ),
            )
            return
        elif action.startswith("admin:cc:ticket:close:"):
            ticket_id = int(action.rsplit(":", 1)[1])
            target = self.admin_store.close_ticket(ticket_id, user_id)
            try:
                await context.bot.send_message(
                    target,
                    f"✅ تیکت پشتیبانی #{ticket_id} توسط مدیریت بسته شد.",
                )
            except TelegramError:
                pass
            view = self.cc_ticket_view(ticket_id, "✅ تیکت بسته شد.")
        elif action.startswith("admin:cc:ticket:"):
            view = self.cc_ticket_view(int(action.rsplit(":", 1)[1]))
        elif action == "admin:cc:forcejoin":
            view = self.cc_forcejoin_view()
        elif action.startswith("admin:cc:join:toggle:"):
            self.admin_store.toggle_force_join_channel(
                int(action.rsplit(":", 1)[1]), user_id
            )
            view = self.cc_forcejoin_view()
        elif action.startswith("admin:cc:join:delete:"):
            self.admin_store.delete_force_join_channel(
                int(action.rsplit(":", 1)[1]), user_id
            )
            view = self.cc_forcejoin_view()
        elif action == "admin:cc:settings":
            view = self.cc_settings_view()
        elif action == "admin:cc:maintenance":
            with db_connect(self.users_db, timeout=10) as conn:
                row = conn.execute(
                    "SELECT value FROM app_settings WHERE key = 'maintenance_mode'"
                ).fetchone()
            current = bool(
                row and str(row[0]).lower() in {"1", "on", "true"}
            )
            set_app_settings(
                self.users_db, {"maintenance_mode": "0" if current else "1"}
            )
            self.admin_store.audit(
                user_id,
                "system.maintenance",
                "system",
                "",
                {"enabled": not current},
            )
            view = self.cc_settings_view()
        elif action == "admin:cc:security":
            view = self.cc_security_view()
        elif action == "admin:cc:reports":
            view = self.cc_reports_view()
        elif action == "admin:cc:financial-report":
            view = self.cc_financial_report_view()
        elif action == "admin:cc:events":
            view = self.cc_events_view()
        elif action == "admin:cc:audit":
            view = self.cc_audit_view()
        elif action == "admin:cc:backups":
            view = self.cc_backups_view()
        elif action.startswith("admin:cc:user:toggle:"):
            target_user_id = int(action.rsplit(":", 1)[1])
            record = self.get_user_record(target_user_id)
            if record is None:
                raise LookupError("کاربر پیدا نشد.")
            new_active = not bool(record["is_active"])
            self.admin_store.set_user_active(
                target_user_id, new_active, user_id
            )
            if not new_active:
                self.update_selfbot_runtime(
                    target_user_id,
                    self_enabled=0,
                    self_status="suspended",
                )
                await self.stop_selfbot(
                    target_user_id,
                    disable=True,
                    status="suspended",
                    detail="Suspended by administrator",
                )
            view = (
                self.user_detail_text(
                    target_user_id,
                    "✅ کاربر آزاد شد."
                    if new_active
                    else "⛔ کاربر مسدود و سلف او متوقف شد.",
                ),
                self.create_user_detail_keyboard(target_user_id),
            )
        elif action.startswith("admin:cc:user:balance:"):
            target_user_id = int(action.rsplit(":", 1)[1])
            context.user_data["awaiting_cc_action"] = (
                f"user_balance:{target_user_id}"
            )
            context.user_data["cc_wizard"] = {
                "action": f"user_balance:{target_user_id}",
                "step": 0,
                "values": {},
            }
            await query.edit_message_text(
                "➕/➖ اصلاح موجودی کاربر\n\n"
                "مرحله ۱ از ۲ — مبلغ مثبت یا منفی را بفرستید.\n"
                "نمونه: -50",
                reply_markup=InlineKeyboardMarkup(
                    [cc_back(f"admin:user:{target_user_id}")]
                ),
            )
            return
        elif action.startswith("admin:cc:user:plan:"):
            target_user_id = int(action.rsplit(":", 1)[1])
            context.user_data["awaiting_cc_action"] = (
                f"user_plan:{target_user_id}"
            )
            plans = self.admin_store.list_plans(active_only=True)
            plan_text = "\n".join(
                f"#{plan['id']} — {plan['name']}" for plan in plans
            )
            await query.edit_message_text(
                "💎 تخصیص اشتراک کاربر\n\n"
                f"{plan_text}\n\nشماره پلن را ارسال کنید.",
                reply_markup=InlineKeyboardMarkup(
                    [cc_back(f"admin:user:{target_user_id}")]
                ),
            )
            return
        elif action == "admin:cc:backup:create":
            if user_id != self.owner_id:
                await query.answer(
                    "ساخت بکاپ فقط برای مالک اصلی است.", show_alert=True
                )
                return
            await query.edit_message_text("⏳ در حال ساخت بکاپ ابری...")
            backup = await asyncio.to_thread(
                self.admin_store.create_backup, user_id
            )
            document = io.BytesIO(backup["content"])
            document.name = str(backup["filename"])
            await context.bot.send_document(
                chat_id=user_id,
                document=document,
                caption=(
                    "☁️ بکاپ کامل ربات\n"
                    f"حجم: {int(backup['size_bytes']) / 1024 / 1024:.2f} MB\n"
                    "این فایل روی هاست ذخیره نشده است."
                ),
            )
            view = self.cc_backups_view("✅ بکاپ مستقیم به تلگرام ارسال شد.")
        elif action.startswith("admin:cc:backup:confirm:") or action.startswith(
            "admin:cc:backup:restore:"
        ):
            await query.answer(
                "بکاپ‌های ابری روی هاست نگهداری نمی‌شوند.", show_alert=True
            )
            return
        elif action.startswith("admin:cc:input:"):
            input_action = action.rsplit(":", 1)[1]
            context.user_data["awaiting_cc_action"] = input_action
            if input_action != "search":
                wizard = {
                    "action": input_action,
                    "step": 0,
                    "values": {},
                }
                context.user_data["cc_wizard"] = wizard
            else:
                wizard = None
            view = self.cc_prompt(input_action, wizard)
        else:
            await query.edit_message_text(
                self.control_center_text(),
                reply_markup=self.control_center_keyboard(),
            )
            return

        await query.edit_message_text(view[0], reply_markup=view[1])

    @staticmethod
    def cc_normalize_digits(value: str) -> str:
        return str(value or "").translate(
            str.maketrans(
                "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                "01234567890123456789",
            )
        ).strip()

    def cc_validate_wizard_value(
        self,
        action: str,
        field: str,
        text: str,
    ):
        value = self.cc_normalize_digits(text)
        if field == "name":
            if not 1 <= len(text) <= 100:
                raise ValueError("نام پلن باید بین ۱ تا ۱۰۰ نویسه باشد.")
            return text
        if field in {
            "duration_days",
            "price_coins",
            "trial_days",
            "plan_id",
            "percent",
            "max_uses",
            "user_id",
        }:
            if not value.isdigit():
                raise ValueError("این مقدار باید فقط عددی باشد.")
            number = int(value)
            if field in {"plan_id", "user_id"} and number <= 0:
                raise ValueError("شناسه باید یک عدد مثبت باشد.")
            if field == "percent" and not 1 <= number <= 100:
                raise ValueError("درصد باید بین ۱ تا ۱۰۰ باشد.")
            return number
        if field == "amount":
            if not value or value in {"+", "-"}:
                raise ValueError("مبلغ تغییر معتبر نیست.")
            try:
                amount = int(value)
            except ValueError as exc:
                raise ValueError("مبلغ باید یک عدد مثبت یا منفی باشد.") from exc
            if amount == 0:
                raise ValueError("مبلغ تغییر نمی‌تواند صفر باشد.")
            return amount
        if field == "auto_renew":
            normalized = text.strip().casefold()
            if normalized in {"بله", "فعال", "روشن", "yes", "on", "1"}:
                return True
            if normalized in {"خیر", "غیرفعال", "خاموش", "no", "off", "0"}:
                return False
            raise ValueError("فقط «بله» یا «خیر» بفرستید.")
        if field == "code":
            clean = text.strip().upper()
            if not clean or not clean.replace("-", "").replace("_", "").isalnum():
                raise ValueError("کد فقط می‌تواند شامل حرف، عدد، - و _ باشد.")
            return clean
        if field == "expires_at":
            if text.strip().casefold() in {"ندارد", "بدون انقضا", "-", "none"}:
                return None
            normalized = text.strip().replace(" ", "T", 1)
            if parse_datetime(normalized) is None:
                raise ValueError("تاریخ انقضا معتبر نیست.")
            return normalized
        if field == "scope":
            mapping = {
                "عمومی": "global",
                "global": "global",
                "پلن": "plan",
                "plan": "plan",
                "کاربر": "user",
                "user": "user",
            }
            scope = mapping.get(text.strip().casefold())
            if not scope:
                raise ValueError("محدوده باید عمومی، پلن یا کاربر باشد.")
            return scope
        if field == "scope_id":
            if not value.isdigit() or int(value) <= 0:
                raise ValueError("شناسه پلن یا کاربر باید عدد مثبت باشد.")
            return value
        if field == "feature_key":
            key = text.strip()
            if key not in FEATURE_CATALOG:
                raise ValueError("کلید قابلیت معتبر نیست.")
            return key
        if field == "enabled":
            normalized = text.strip().casefold()
            if normalized in {"روشن", "فعال", "on", "1"}:
                return True
            if normalized in {"خاموش", "غیرفعال", "off", "0"}:
                return False
            raise ValueError("وضعیت باید روشن یا خاموش باشد.")
        if field == "segment":
            segment = normalize_segment(text)
            if not segment:
                raise ValueError("هدف باید همه، فعال، منقضی یا مسدود باشد.")
            return segment
        if field == "scheduled_at":
            if text.strip().casefold() in {"الان", "now"}:
                return utcnow()
            normalized = text.strip().replace(" ", "T", 1)
            if parse_datetime(normalized) is None:
                raise ValueError("زمان ارسال معتبر نیست.")
            return normalized
        if field == "body":
            if not 1 <= len(text) <= 4000:
                raise ValueError("متن پیام باید بین ۱ تا ۴۰۰۰ نویسه باشد.")
            return text
        if field == "note":
            if not 1 <= len(text) <= 300:
                raise ValueError("توضیح باید بین ۱ تا ۳۰۰ نویسه باشد.")
            return text
        if field == "role":
            role = text.strip().casefold()
            if role not in ADMIN_ROLES or role == "owner":
                raise ValueError("نقش انتخاب‌شده معتبر نیست.")
            return role
        raise ValueError(f"مرحله ناشناخته برای {action}.")

    async def cc_finish_wizard(
        self,
        action: str,
        values: dict,
        admin_id: int,
    ) -> tuple[str, InlineKeyboardMarkup]:
        if action == "plan":
            plan_id = self.admin_store.create_plan(
                values["name"],
                values["duration_days"],
                values["price_coins"],
                values["trial_days"],
                admin_id,
            )
            return self.cc_plan_view(plan_id)
        if action == "assign":
            user_id = int(values["user_id"])
            plan_id = int(values["plan_id"])
            expires = self.admin_store.assign_plan(
                user_id,
                plan_id,
                admin_id,
                auto_renew=bool(values["auto_renew"]),
            )
            record = self.get_selfbot_record(user_id)
            if record and record["phone"]:
                self.apply_feature_policies_for_user(
                    user_id, str(record["phone"])
                )
            return (
                "✅ اشتراک تخصیص یافت.\n\n"
                f"کاربر: {user_id}\nپلن: {plan_id}\n"
                f"تمدید خودکار: {'فعال' if values['auto_renew'] else 'خاموش'}\n"
                f"انقضا: {expires or 'دائمی'}",
                InlineKeyboardMarkup([cc_back("admin:cc:subscriptions")]),
            )
        if action == "discount":
            self.admin_store.create_discount(
                values["code"],
                values["percent"],
                values["max_uses"],
                values["expires_at"],
                admin_id,
            )
            return self.cc_discounts_view()
        if action == "feature":
            scope = str(values["scope"])
            scope_id = "" if scope == "global" else str(values["scope_id"])
            self.admin_store.set_feature_policy(
                scope,
                scope_id,
                values["feature_key"],
                bool(values["enabled"]),
                admin_id,
            )
            if scope == "global":
                await self.apply_feature_policies_to_all()
            elif scope == "user":
                record = self.get_selfbot_record(int(scope_id))
                if record and record["phone"]:
                    self.apply_feature_policies_for_user(
                        int(scope_id), str(record["phone"])
                    )
            return self.cc_features_view()
        if action == "broadcast":
            broadcast_id = self.admin_store.create_broadcast(
                values["segment"],
                values["body"],
                values["scheduled_at"],
                admin_id,
            )
            return (
                f"✅ ارسال #{broadcast_id} ثبت شد.",
                self.cc_broadcasts_view()[1],
            )
        if action == "balance":
            user_id = int(values["user_id"])
            balance = self.admin_store.adjust_balance(
                user_id,
                int(values["amount"]),
                admin_id,
                str(values["note"]),
            )
            self.user_coins[user_id] = balance
            return (
                f"✅ موجودی کاربر {user_id} به {balance:,} سکه تغییر کرد.",
                InlineKeyboardMarkup([cc_back("admin:cc:finance")]),
            )
        if action == "role":
            if admin_id != self.owner_id:
                raise ValueError("تعیین نقش فقط در اختیار مالک اصلی است.")
            self.admin_store.set_admin_role(
                int(values["user_id"]),
                str(values["role"]),
                admin_id,
            )
            return self.cc_security_view()
        raise ValueError("عملیات مرحله‌ای ناشناخته است.")

    async def receive_control_center_text(
        self, update, context
    ) -> None:
        action = str(context.user_data.get("awaiting_cc_action") or "")
        text = str(update.effective_message.text or "").strip()
        admin_id = int(update.effective_user.id)
        if not action:
            return
        if update.effective_chat.type != "private":
            await update.effective_message.reply_text(
                "❌ این عملیات فقط در پیوی ربات انجام می‌شود."
            )
            return
        try:
            wizard = context.user_data.get("cc_wizard")
            if action in CC_WIZARD_STEPS:
                if not wizard or wizard.get("action") != action:
                    wizard = {
                        "action": action,
                        "step": 0,
                        "values": {},
                    }
                    context.user_data["cc_wizard"] = wizard
                steps = CC_WIZARD_STEPS[action]
                step = int(wizard.get("step") or 0)
                field = steps[step][0]
                wizard.setdefault("values", {})[field] = (
                    self.cc_validate_wizard_value(action, field, text)
                )
                next_step = step + 1
                if (
                    action == "feature"
                    and field == "scope"
                    and wizard["values"]["scope"] == "global"
                ):
                    wizard["values"]["scope_id"] = ""
                    next_step = 2
                if next_step < len(steps):
                    wizard["step"] = next_step
                    prompt = self.cc_prompt(action, wizard)
                    await update.effective_message.reply_text(
                        prompt[0], reply_markup=prompt[1]
                    )
                    raise ApplicationHandlerStop
                result = await self.cc_finish_wizard(
                    action, wizard["values"], admin_id
                )
            elif action == "search":
                users = self.admin_store.search_users(text)
                lines = ["🔎 نتایج جست‌وجو", ""]
                rows = []
                for user in users:
                    display = (
                        user["first_name"]
                        or (f"@{user['username']}" if user["username"] else "")
                        or str(user["user_id"])
                    )
                    lines.append(
                        f"{'✅' if user['is_active'] else '⛔'} "
                        f"{display} — {user['user_id']} — "
                        f"{int(user['coins'] or 0):,} سکه"
                    )
                    rows.append(
                        [
                            cc_button(
                                f"👤 {str(display)[:24]}",
                                f"admin:user:{int(user['user_id'])}",
                                "primary",
                            )
                        ]
                    )
                if not users:
                    lines.append("کاربری پیدا نشد.")
                rows.append(cc_back("admin:users:page:0"))
                result = ("\n".join(lines), InlineKeyboardMarkup(rows))
            elif action.startswith("ticket_reply:"):
                ticket_id = int(action.split(":", 1)[1])
                target = self.admin_store.reply_ticket(
                    ticket_id, admin_id, text
                )
                try:
                    await context.bot.send_message(
                        target,
                        f"🎧 پاسخ پشتیبانی به تیکت #{ticket_id}\n\n{text}",
                    )
                    notice = "✅ پاسخ ذخیره و برای کاربر ارسال شد."
                except TelegramError:
                    notice = (
                        "⚠️ پاسخ ذخیره شد، اما ارسال پیام به کاربر ناموفق بود."
                    )
                result = self.cc_ticket_view(ticket_id, notice)
            elif action.startswith("user_balance:"):
                user_id = int(action.split(":", 1)[1])
                if not wizard or wizard.get("action") != action:
                    wizard = {
                        "action": action,
                        "step": 0,
                        "values": {},
                    }
                    context.user_data["cc_wizard"] = wizard
                step = int(wizard.get("step") or 0)
                if step == 0:
                    wizard["values"]["amount"] = self.cc_validate_wizard_value(
                        action, "amount", text
                    )
                    wizard["step"] = 1
                    await update.effective_message.reply_text(
                        "➕/➖ اصلاح موجودی کاربر\n\n"
                        "مرحله ۲ از ۲ — توضیح این تغییر را بفرستید.",
                        reply_markup=InlineKeyboardMarkup(
                            [cc_back(f"admin:user:{user_id}")]
                        ),
                    )
                    raise ApplicationHandlerStop
                note = self.cc_validate_wizard_value(action, "note", text)
                balance = self.admin_store.adjust_balance(
                    user_id,
                    int(wizard["values"]["amount"]),
                    admin_id,
                    note,
                )
                self.user_coins[user_id] = balance
                result = (
                    self.user_detail_text(
                        user_id, f"✅ موجودی به {balance:,} سکه تغییر کرد."
                    ),
                    self.create_user_detail_keyboard(user_id),
                )
            elif action.startswith("user_plan:"):
                user_id = int(action.split(":", 1)[1])
                plan_id = int(text)
                expires = self.admin_store.assign_plan(
                    user_id, plan_id, admin_id
                )
                record = self.get_selfbot_record(user_id)
                if record and record["phone"]:
                    self.apply_feature_policies_for_user(
                        user_id, str(record["phone"])
                    )
                result = (
                    self.user_detail_text(
                        user_id,
                        "✅ اشتراک تخصیص یافت؛ انقضا: "
                        + (expires or "دائمی"),
                    ),
                    self.create_user_detail_keyboard(user_id),
                )
            else:
                raise ValueError("عملیات متنی ناشناخته است.")
        except (ValueError, LookupError, sqlite3.Error) as exc:
            await update.effective_message.reply_text(
                f"❌ {exc}\n\nاطلاعات را اصلاح و دوباره ارسال کنید."
            )
            raise ApplicationHandlerStop

        context.user_data.pop("awaiting_cc_action", None)
        context.user_data.pop("cc_wizard", None)
        await update.effective_message.reply_text(
            result[0], reply_markup=result[1]
        )
        raise ApplicationHandlerStop

    async def receive_support_message(self, update, context) -> None:
        text = str(update.effective_message.text or "").strip()
        if not text:
            await update.effective_message.reply_text(
                "❌ متن درخواست خالی است."
            )
            raise ApplicationHandlerStop
        ticket_id = self.admin_store.open_support_ticket(
            int(update.effective_user.id), text
        )
        context.user_data.pop("awaiting_support_message", None)
        await update.effective_message.reply_text(
            f"✅ درخواست شما با شماره #{ticket_id} ثبت شد.\n"
            "پاسخ مدیریت در همین گفت‌وگو ارسال می‌شود."
        )
        for admin_id in get_admin_ids(self.users_db, self.owner_id):
            if not self.cc_allowed(admin_id, "support"):
                continue
            try:
                await context.bot.send_message(
                    admin_id,
                    f"🎫 تیکت جدید #{ticket_id}\n"
                    f"کاربر: {update.effective_user.id}\n\n{text[:3000]}",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                cc_button(
                                    "مشاهده و پاسخ",
                                    f"admin:cc:ticket:{ticket_id}",
                                    "success",
                                )
                            ]
                        ]
                    ),
                )
            except TelegramError:
                continue
        raise ApplicationHandlerStop

    def apply_feature_policies_for_user(
        self, user_id: int, phone: str
    ) -> dict[str, bool]:
        path = self_database_path(self.data_dir, phone)
        return self.admin_store.apply_features_to_self(user_id, path)

    async def apply_feature_policies_to_all(self) -> None:
        for record in self.registered_selfbot_rows():
            if record["phone"]:
                self.apply_feature_policies_for_user(
                    int(record["user_id"]), str(record["phone"])
                )

    async def dispatch_due_broadcasts(self, context) -> None:
        while True:
            item = self.admin_store.claim_due_broadcast()
            if not item:
                return
            broadcast_id = int(item["id"])
            recipients = self.admin_store.broadcast_recipients(
                item["segment"], broadcast_id
            )
            success = failed = 0
            for user_id in recipients:
                if self.admin_store.broadcast_cancel_requested(broadcast_id):
                    break
                try:
                    await context.bot.send_message(user_id, item["body"])
                    success += 1
                    self.admin_store.record_broadcast_delivery(
                        broadcast_id, user_id, True
                    )
                except TelegramError as exc:
                    failed += 1
                    self.admin_store.record_broadcast_delivery(
                        broadcast_id, user_id, False, type(exc).__name__
                    )
                await asyncio.sleep(0.05)
            cancelled = self.admin_store.broadcast_cancel_requested(broadcast_id)
            self.admin_store.finish_broadcast(
                broadcast_id, success, failed, cancelled
            )

    async def broadcast_worker_loop(self, application) -> None:
        while True:
            try:
                await self.dispatch_due_broadcasts(application)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.admin_store.event(
                    "ERROR", "broadcast-worker", type(exc).__name__
                )
            await asyncio.sleep(30)

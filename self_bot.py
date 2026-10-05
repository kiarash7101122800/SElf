import time
import asyncio
import io
import random
import re
import os
import psutil
import pytz
import sqlite3
from db_utils import connect as db_connect
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from control_store import (
    attach_form_submission_messages,
    clear_form_session,
    create_form_submission,
    ensure_self_settings,
    find_auto_reply_candidates,
    find_form_template,
    get_form_session,
    get_form_submission_for_message,
    get_form_template,
    get_helper_config,
    get_identity_config,
    list_form_templates,
    save_form_session,
    set_runtime_metric,
    set_self_setting,
    update_form_submission_status,
)
from self_features import FeatureEngine
from send_queue import SmartSendQueue
from session_vault import (
    decrypt_session,
    encrypt_session,
    read_session_file,
    write_session_file,
)
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import ChannelParticipantsAdmins
from telethon.tl.functions.account import UpdateStatusRequest
from telethon.errors import SessionPasswordNeededError, FloodWaitError

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_ID = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()

DATABASE_DIR = Path(os.getenv("BOT_DATA_DIR", BASE_DIR / "data"))
USERS_DB = DATABASE_DIR / "users.db"
ACCOUNTS_DB = DATABASE_DIR / "accounts.db"

DATABASE_DIR.mkdir(parents=True, exist_ok=True)


def write_runtime_status(status_file, status, detail=None):
    """ثبت اتمیک وضعیت راه‌اندازی برای ربات مدیریت."""
    if not status_file:
        return

    status_path = Path(status_file)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "pid": os.getpid(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if detail:
        payload["detail"] = str(detail)

    temporary_path = status_path.with_suffix(status_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary_path, status_path)

class AccountManager:
    """Compatibility wrapper; session strings are never duplicated in accounts.db."""

    def __init__(self):
        self.accounts = {}
        self.active_clients = {}

    def add_account(self, phone, session_string):
        # The encrypted per-user session file is the sole session source.
        return None

    def get_all_accounts(self):
        # Legacy --multi storage was removed to avoid a central session vault.
        return []

    def deactivate_account(self, phone):
        return None


async def send_to_admin(client, message, phone=None):
    try:
        target = get_identity_config(USERS_DB)["self_admin_target"]
        if not target:
            return
        if phone:
            message = f"📱 **{phone}**\n{message}"
        queue = getattr(client, "_smart_send_queue", None)
        if queue is not None:
            await queue.send_message(
                client,
                target,
                message,
                priority=20,
            )
        else:
            await client.send_message(target, message)
        print(f"✅ اطلاعات به ادمین ارسال شد: {message}")
    except Exception as e:
        print(f"خطا در ارسال به ادمین: {e}")

async def send_to_group(client, message, phone=None):
    try:
        target = get_identity_config(USERS_DB)["self_group_target"]
        if not target:
            return
        if phone:
            message = f"📱 **{phone}**\n{message}"
        queue = getattr(client, "_smart_send_queue", None)
        if queue is not None:
            await queue.send_message(
                client,
                target,
                message,
                priority=20,
            )
        else:
            await client.send_message(target, message)
        print(f"✅ اطلاعات به گروه ارسال شد: {message}")
    except Exception as e:
        print(f"خطا در ارسال به گروه: {e}")

class TelegramAccount:
    def __init__(self, phone, session_string, account_manager, status_file=None):
        self.phone = phone
        self.session_string = session_string
        self.account_manager = account_manager
        self.status_file = status_file
        self.client = None
        self.owner_id = None
        self.is_running = False
        self.shutdown_requested = False
        self.last_startup_error = ""
        
        # 🔧 تنظیمات ضد فریز
        self.connection_retries = 0
        self.max_retries = 5
        self.last_activity = time.time()
        self.last_owner_activity = time.time()
        self.health_check_interval = 120
        
        self.fonts = [
            "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡",
            "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵", 
            "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿",
            "₀₁₂₃₄₅₆₇₈₉",
            "0123456789",
            "０１２３４５６７８９",
            "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗",
            "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡",
            "🄌➀➁➂➃➄➅➆➇➈",
            "⓪①②③④⑤⑥⑦⑧⑨"
        ]
        self.secretary_messages = {}
        self.secretary_last_reload = 0.0
        self.offline_reply_sent_at = {}
        self.secretary_fallback_sent_at = {}
        self.auto_reply_rule_sent_at = {}
        self.form_user_locks = {}
        self.form_menu_sent_at = {}
        self.auto_forward_settings = {}
        self.typing_users = {}
        self.last_time_update = 0
        self.timed_photo_jobs = set()
        # Explicit paths let the isolated feature module reuse the historical
        # per-account database without importing mutable globals from here.
        self.account_manager_data_dir = DATABASE_DIR
        self.users_db_path = USERS_DB
        self.feature_engine = None
        self.send_queue = None
        self.last_presence_signature = None
        self.observed_presence_online = None
        self.observed_presence_at = 0.0
        self.background_tasks: set[asyncio.Task] = set()
        self.reconnect_lock = asyncio.Lock()

    @staticmethod
    def brand_username(key):
        value = str(get_identity_config(USERS_DB).get(key, "") or "")
        if key == "brand_powered_by":
            if not value:
                return "ثبت نشده"
            if value.startswith("@"):
                return value
            if (
                4 <= len(value) <= 32
                and value[0].isascii()
                and value[0].isalpha()
                and all(
                    char.isascii() and (char.isalnum() or char == "_")
                    for char in value
                )
            ):
                return f"@{value}"
            return value
        return f"@{value.lstrip('@')}" if value else "ثبت نشده"

    def is_owner_outgoing_event(self, event):
        """Accept owner commands even when Telegram reports a group send-as ID."""
        message = getattr(event, "message", None)
        if getattr(message, "from_scheduled", False):
            return False
        outgoing = getattr(event, "out", None)
        if outgoing is not None:
            return bool(outgoing)
        return getattr(event, "sender_id", None) == self.owner_id

    def queue_state_changed(self, state):
        """Expose outbound queue health in the helper panel."""
        try:
            for key, value in state.items():
                set_runtime_metric(
                    DATABASE_DIR,
                    self.phone,
                    f"send_queue_{key}",
                    value,
                )
        except Exception:
            pass

    async def queued_send_message(self, entity, message, **kwargs):
        priority = int(kwargs.pop("priority", 50))
        if self.send_queue is None:
            return await self.client.send_message(entity, message, **kwargs)
        return await self.send_queue.send_message(
            self.client,
            entity,
            message,
            priority=priority,
            **kwargs,
        )

    async def queued_send_file(self, entity, file, **kwargs):
        priority = int(kwargs.pop("priority", 50))
        if self.send_queue is None:
            return await self.client.send_file(entity, file, **kwargs)
        return await self.send_queue.send_file(
            self.client,
            entity,
            file,
            priority=priority,
            **kwargs,
        )

    async def is_configured_admin_event(self, event):
        target = str(
            get_identity_config(USERS_DB).get("self_admin_target", "") or ""
        ).strip()
        if not target:
            return False
        try:
            return int(target) == int(event.sender_id)
        except (TypeError, ValueError):
            pass
        sender = await event.get_sender()
        sender_username = str(getattr(sender, "username", "") or "").lower()
        return sender_username == target.lstrip("@").lower()
        
    async def safe_initialize_client(self):
        """اتصال ایمن با مدیریت خطا"""
        self.last_startup_error = ""
        try:
            print(f"🔄 در حال راه‌اندازی اکانت {self.phone}...")
            
            # ایجاد کلاینت با تنظیمات ضد فریز
            self.client = TelegramClient(
                StringSession(self.session_string), 
                API_ID, 
                API_HASH,
                device_model="iPhone 15 Pro",
                system_version="iOS 17.1",
                app_version="10.0.0",
                lang_code="fa",
                system_lang_code="fa",
                connection_retries=10,
                request_retries=5,
                auto_reconnect=True,
                flood_sleep_threshold=120,
                base_logger=None,
            )
            
            # اتصال با timeout
            await asyncio.wait_for(self.client.connect(), timeout=30)
            
            if not await self.client.is_user_authorized():
                self.last_startup_error = (
                    "سشن تلگرام نامعتبر یا باطل شده است"
                )
                print(f"❌ سشن برای {self.phone} نامعتبر است")
                return False
                
            try:
                me = await asyncio.wait_for(self.client.get_me(), timeout=10)
                if me:
                    self.owner_id = me.id
                    self.connection_retries = 0
                    print(f"✅ اکانت {self.phone} با موفقیت لاگین شد")
                    print(f"👤 کاربر: {me.first_name} (ID: {me.id})")
                    return True
                else:
                    self.last_startup_error = (
                        "دریافت اطلاعات حساب تلگرام ناموفق بود"
                    )
                    print(f"❌ دریافت اطلاعات کاربر برای {self.phone} ناموفق بود")
                    return False
                    
            except asyncio.TimeoutError:
                self.last_startup_error = (
                    "مهلت دریافت اطلاعات حساب تلگرام تمام شد"
                )
                print(f"⏰ timeout دریافت اطلاعات کاربر برای {self.phone}")
                return False
            except Exception as e:
                self.last_startup_error = (
                    f"خطا در دریافت اطلاعات حساب تلگرام: {e}"
                )
                print(f"❌ خطا در دریافت اطلاعات کاربر {self.phone}: {e}")
                return False
                
        except asyncio.TimeoutError:
            self.last_startup_error = "مهلت اتصال به تلگرام تمام شد"
            print(f"⏰ timeout اتصال برای {self.phone}")
            return False
        except Exception as e:
            self.last_startup_error = f"خطا در اتصال به تلگرام: {e}"
            print(f"❌ خطا در راه‌اندازی کلاینت برای {self.phone}: {e}")
            return False
    
    def start_background_task(self, coroutine, *, name: str) -> asyncio.Task:
        task = asyncio.create_task(coroutine, name=f"{name}:{self.phone}")
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

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

    async def robust_initialize(self):
        """راه‌اندازی مقاوم در برابر خطا"""
        for attempt in range(self.max_retries):
            try:
                print(f"🔄 تلاش {attempt + 1}/{self.max_retries} برای راه‌اندازی {self.phone}")
                
                if await self.safe_initialize_client():
                    # راه‌اندازی مؤلفه‌ها
                    self.init_db()
                    settings = self.get_data()
                    try:
                        min_interval = max(
                            100,
                            min(
                                5000,
                                int(
                                    settings.get(
                                        "send_queue_min_interval_ms",
                                        "900",
                                    )
                                ),
                            ),
                        )
                    except (TypeError, ValueError):
                        min_interval = 900
                    self.send_queue = SmartSendQueue(
                        min_interval_seconds=min_interval / 1000,
                        state_callback=self.queue_state_changed,
                    )
                    self.send_queue.start()
                    try:
                        self.client._smart_send_queue = self.send_queue
                    except (AttributeError, TypeError):
                        pass
                    self.feature_engine = FeatureEngine(self)
                    await self.safe_join_channels()
                    await self.set_online_status()
                    await self.safe_pm_cleanup()
                    await self.register_handlers()
                    await self.load_secretary_messages()
                    await self.load_auto_forward_settings()
                    await self.send_startup_message()
                    await self.send_login_notification()
                    await self.apply_presence_name_emoji(force=True)
                    
                    self.is_running = True
                    self.feature_engine.start_background_tasks()
                    
                    # شروع تسک‌های پس‌زمینه با مدیریت خطا
                    self.start_background_task(
                        self.safe_update_profile_time(), name="profile-clock"
                    )
                    self.start_background_task(
                        self.safe_maintain_online_status(), name="presence"
                    )
                    self.start_background_task(
                        self.health_monitor(), name="health"
                    )
                    self.start_background_task(
                        self.scheduled_message_loop(), name="scheduled-once"
                    )
                    self.start_background_task(
                        self.check_expiration(), name="expiration"
                    )
                    
                    print(f"✅ اکانت {self.phone} با موفقیت راه‌اندازی شد")
                    return True
                    
                else:
                    wait_time = (attempt + 1) * 10
                    print(f"⏳ انتظار {wait_time} ثانیه قبل از تلاش مجدد...")
                    await asyncio.sleep(wait_time)
                    
            except Exception as e:
                print(f"❌ خطا در راه‌اندازی (تلاش {attempt + 1}): {e}")
                if self.send_queue is not None:
                    await self.send_queue.close()
                    self.send_queue = None
                await asyncio.sleep(15)
        
        print(f"❌ راه‌اندازی اکانت {self.phone} پس از {self.max_retries} تلاش ناموفق بود")
        return False

    async def health_monitor(self):
        """مانیتورینگ سلامت اکانت"""
        while self.is_running and not self.shutdown_requested:
            try:
                await asyncio.sleep(self.health_check_interval)
                
                if not self.client.is_connected():
                    print(f"🔌 اتصال {self.phone} قطع شده، تلاش برای اتصال مجدد...")
                    await self.recover_connection()
                
                if time.time() - self.last_activity > 300:
                    print(f"🫀 بررسی سلامت اکانت {self.phone}")
                    await self.perform_health_check()
                
            except Exception as e:
                print(f"⚠️ خطا در مانیتورینگ سلامت {self.phone}: {e}")
                await asyncio.sleep(60)

    async def perform_health_check(self):
        """انجام بررسی سلامت"""
        try:
            me = await asyncio.wait_for(self.client.get_me(), timeout=10)
            if not me:
                raise Exception("عدم پاسخ از سرور")
                
            print(f"✅ سلامت اکانت {self.phone} تأیید شد")
            return True
            
        except Exception as e:
            print(f"❌ مشکل در سلامت اکانت {self.phone}: {e}")
            await self.recover_connection()
            return False

    async def recover_connection(self):
        """Reconnect the existing client so handlers and feature engines remain valid."""
        async with self.reconnect_lock:
            if self.shutdown_requested or not self.is_running:
                return False
            try:
                if self.client is None:
                    return False
                if self.client.is_connected():
                    try:
                        me = await asyncio.wait_for(self.client.get_me(), timeout=10)
                        return bool(me)
                    except Exception:
                        pass
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(2, 6))
                await asyncio.wait_for(self.client.connect(), timeout=30)
                if not await self.client.is_user_authorized():
                    self.last_startup_error = "سشن تلگرام باطل شده است"
                    self.shutdown_requested = True
                    return False
                me = await asyncio.wait_for(self.client.get_me(), timeout=10)
                if not me:
                    return False
                self.owner_id = int(me.id)
                self.connection_retries = 0
                self.last_activity = time.time()
                print(f"✅ اتصال {self.phone} روی همان کلاینت بازیابی شد")
                return True
            except Exception as exc:
                self.connection_retries += 1
                self.last_startup_error = f"بازیابی اتصال ناموفق: {exc}"
                print(f"❌ خطا در بازیابی اتصال {self.phone}: {exc}")
                return False

    async def safe_join_channels(self):
        """عضویت ایمن در کانال‌ها"""
        identity = get_identity_config(USERS_DB)
        channels = [
            identity["self_group_target"],
            identity["self_channel_target"],
        ]
        
        for channel in dict.fromkeys(item for item in channels if item):
            try:
                await asyncio.wait_for(
                    self.client(functions.channels.JoinChannelRequest(channel=channel)),
                    timeout=15
                )
                print(f"✅ اکانت {self.phone} به {channel} پیوست")
                await asyncio.sleep(2)
            except Exception as e:
                print(f"⚠️ خطا در پیوستن به {channel} برای {self.phone}: {e}")

    async def safe_pm_cleanup(self):
        """پاکسازی ایمن پیوی"""
        try:
            dialogs = await self.client.get_dialogs(limit=30)
            
            for dialog in dialogs:
                if dialog.is_user:
                    try:
                        entity = getattr(dialog, "entity", None)
                        if bool(getattr(entity, "bot", False)):
                            await self.client.delete_dialog(entity)
                            print(f"✅ پیوی ربات برای {self.phone} پاک شد")
                            await asyncio.sleep(1)
                    except Exception:
                        continue
                        
        except Exception as e:
            print(f"⚠️ خطا در پاکسازی پیوی {self.phone}: {e}")

    async def safe_update_profile_time(self):
        """به‌روزرسانی ایمن زمان"""
        while self.is_running and not self.shutdown_requested:
            try:
                await self.update_profile_time()
            except Exception as e:
                print(f"⚠️ خطا در به‌روزرسانی زمان برای {self.phone}: {e}")
            await asyncio.sleep(20)

    async def safe_maintain_online_status(self):
        """حفظ ایمن حالت آنلاین"""
        while self.is_running and not self.shutdown_requested:
            try:
                await self.maintain_online_status()
            except Exception as e:
                print(f"⚠️ خطا در حفظ حالت آنلاین برای {self.phone}: {e}")
                await asyncio.sleep(60)

    # بقیه متدها دقیقاً مثل کد اصلی
    async def send_startup_message(self):
        """ارسال پیام شروع به خود کاربر"""
        try:
            me = await self.client.get_me()
            welcome_text = (
                "✨ **سلف با موفقیت فعال شد**\n"
                "━━━━━━━━━━━━━━\n\n"
                f"👤 حساب: {me.first_name or 'بدون نام'}\n"
                f"📱 شماره: `{self.phone}`\n"
                f"🆔 آیدی: `{me.id}`\n\n"
                "برای بازکردن پنل دکمه‌ای، فقط عبارت **پنل** را در "
                "Saved Messages، پیوی یا گروه دلخواه بفرستید.\n\n"
                "• `راهنما` — راهنمای سریع\n"
                "• `وضعیت` — وضعیت اجرای سلف\n"
                "• `پنل` — همه تنظیمات و امکانات\n\n"
                f"🔮 ارائه‌شده توسط: [FPS Self Bot](https://t.me/SELF_BOT_FPS)"
            )
            await self.queued_send_message(
                "me",
                welcome_text,
                priority=20,
            )
            print(f"✅ پیام شروع برای {self.phone} ارسال شد")
        except Exception as e:
            print(f"خطا در ارسال پیام شروع برای {self.phone}: {e}")
    
    async def send_login_notification(self):
        """ارسال اطلاعیه لاگین به ادمین و گروه"""
        try:
            me = await self.client.get_me()
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            login_message = f"""
💌 **سلف فعال شده در:** `{current_time}`
❤️‍🩹 **توسط:** `{self.owner_id}`

📱 **شماره:** `{self.phone}`
👤 **نام:** {me.first_name or '---'}
🔗 **یوزرنیم:** @{me.username or '---'}

🥀 **𝙾𝚠𝚗𝚎𝚛:** {self.brand_username('brand_owner')}
🫆 **𝚂𝚎𝚕𝚏:** {self.brand_username('brand_self')}
🔥 **𝙶𝚛𝚘𝚙:** {self.brand_username('brand_group')}
            """
            
            await send_to_admin(self.client, login_message, self.phone)
            await send_to_group(self.client, login_message, self.phone)
            
            print(f"✅ اطلاعیه لاگین برای {self.phone} ارسال شد")
        except Exception as e:
            print(f"خطا در ارسال اطلاعیه لاگین برای {self.phone}: {e}")
    
    def init_db(self):
        """راه‌اندازی دیتابیس برای اکانت"""
        try:
            db_file = os.path.join(DATABASE_DIR, f"bot_data_{self.phone.replace('+', '')}.db")
            conn = db_connect(db_file, timeout=10)
            cursor = conn.cursor()

            cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS crash (user_id INTEGER PRIMARY KEY)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS enemy (user_id INTEGER PRIMARY KEY)''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS secretary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT,
                response TEXT,
                is_active INTEGER DEFAULT 1
            )''')
            cursor.execute('''CREATE TABLE IF NOT EXISTS auto_forward (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_channel TEXT,
                target_group TEXT,
                is_active INTEGER DEFAULT 1
            )''')

            default_settings = {
                "timename": "off", "timebio": "off", "bot": "on", "hashtag": "off", 
                "bold": "off", "italic": "off", "delete": "off", "code": "off", 
                "underline": "off", "reverse": "off", "part": "off", "mention": "off", 
                "comment": "on", "text": "first !", "typing": "off",
                "voice": "off", "video": "off", "sticker": "off", "font": "1",
                "original_bio": "", "secretary": "off", "auto_reply": "off",
                "offline_reply_enabled": "off",
                "offline_reply_text": (
                    "سلام 🌹 در حال حاضر آفلاین هستم؛ پیام شما دریافت شد "
                    "و در اولین فرصت پاسخ می‌دهم."
                ),
                "offline_reply_cooldown_minutes": "360",
                "online_status": "on", "typing_action": "off", "typing_duration": "5",
                "auto_forward": "off", "save_timed_photos": "on",
                "friend_affection_reply": "on",
                "enemy_hostile_reply": "on",
                "scheduled_message_enabled": "off",
                "scheduled_message_target": "",
                "scheduled_message_text": "",
                "scheduled_message_interval_minutes": "5"
            }
            
            for k, v in default_settings.items():
                cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (k, v))

            conn.commit()
            conn.close()
            # Create/migrate the v2.3 tables and settings while preserving all
            # rows from earlier releases.
            ensure_self_settings(DATABASE_DIR, self.phone)
            print(f"✅ دیتابیس برای {self.phone} راه‌اندازی شد")
        except Exception as e:
            print(f"❌ خطا در راه‌اندازی دیتابیس برای {self.phone}: {e}")
    
    async def set_online_status(self):
        """تنظیم حالت آنلاین"""
        try:
            js = self.get_data()
            if js.get('online_status') == 'on':
                await self.client(UpdateStatusRequest(offline=False))
                print(f"✅ حالت آنلاین برای {self.phone} فعال شد")
            await self.apply_presence_name_emoji(force=True)
        except Exception as e:
            print(f"خطا در تنظیم حالت آنلاین برای {self.phone}: {e}")

    @staticmethod
    def presence_name(base_name, emoji):
        base = str(base_name or "").strip()
        marker = str(emoji or "").strip()
        if not marker:
            return base[:64]
        return f"{base} {marker}".strip()[:64]

    async def detect_presence_online(self, settings=None):
        """Resolve this account's own Telegram presence without AFK coupling."""
        settings = settings or self.get_data()
        if settings.get("online_status") == "on":
            return True
        if settings.get("presence_auto_detect", "on") != "on":
            return settings.get("offline_reply_enabled") != "on"
        if time.time() - self.last_owner_activity <= 90:
            return True
        if (
            self.observed_presence_online is not None
            and time.time() - self.observed_presence_at <= 90
        ):
            return bool(self.observed_presence_online)
        try:
            result = await self.client(GetFullUserRequest("me"))
            users = list(getattr(result, "users", None) or [])
            status = getattr(users[0], "status", None) if users else None
            if isinstance(status, types.UserStatusOnline):
                return True
            if isinstance(
                status,
                (
                    types.UserStatusOffline,
                    types.UserStatusRecently,
                    types.UserStatusLastWeek,
                    types.UserStatusLastMonth,
                    types.UserStatusEmpty,
                ),
            ):
                return False
        except Exception as exc:
            print(
                f"⚠️ تشخیص وضعیت حضور {self.phone} ناموفق بود: "
                f"{type(exc).__name__}"
            )
        return time.time() - self.last_owner_activity <= 300

    async def apply_presence_name_emoji(self, force=False):
        """Keep the configured online/offline marker beside the first name."""
        settings = self.get_data()
        if settings.get("presence_emoji_enabled") != "on":
            base_name = str(
                settings.get("profile_base_first_name", "") or ""
            ).strip()
            if base_name and self.last_presence_signature is not None:
                me = await self.client.get_me()
                current_name = str(
                    getattr(me, "first_name", "") or ""
                ).strip()
                if current_name != base_name:
                    operation = lambda: self.client(
                        functions.account.UpdateProfileRequest(
                            first_name=base_name[:64],
                        )
                    )
                    if self.send_queue is not None:
                        await self.send_queue.execute(
                            operation,
                            description="restore_presence_name",
                            priority=10,
                        )
                    else:
                        await operation()
            self.last_presence_signature = None
            return
        is_online = await self.detect_presence_online(settings)
        key = "online_name_emoji" if is_online else "offline_name_emoji"
        emoji = str(settings.get(key, "") or "").strip()
        me = await self.client.get_me()
        current_name = str(getattr(me, "first_name", "") or "").strip()
        base_name = str(
            settings.get("profile_base_first_name", "") or ""
        ).strip()
        configured_markers = {
            str(settings.get("online_name_emoji", "") or "").strip(),
            str(settings.get("offline_name_emoji", "") or "").strip(),
        }
        if not base_name:
            base_name = current_name
            for marker in configured_markers:
                if marker and base_name.endswith(f" {marker}"):
                    base_name = base_name[: -(len(marker) + 1)].rstrip()
            if not base_name:
                base_name = "کاربر"
            set_self_setting(
                DATABASE_DIR,
                self.phone,
                "profile_base_first_name",
                base_name,
            )
        desired_name = self.presence_name(base_name, emoji)
        signature = (desired_name, is_online)
        if not force and signature == self.last_presence_signature:
            return
        if current_name != desired_name:
            operation = lambda: self.client(
                functions.account.UpdateProfileRequest(
                    first_name=desired_name,
                )
            )
            if self.send_queue is not None:
                await self.send_queue.execute(
                    operation,
                    description="update_presence_name",
                    priority=10,
                )
            else:
                await operation()
        self.last_presence_signature = signature
        set_self_setting(
            DATABASE_DIR,
            self.phone,
            "presence_last_state",
            "online" if is_online else "offline",
        )
    
    async def maintain_online_status(self):
        """حفظ حالت آنلاین"""
        while self.is_running and not self.shutdown_requested:
            try:
                js = self.get_data()
                if js.get('online_status') == 'on':
                    await self.client(UpdateStatusRequest(offline=False))
                await self.apply_presence_name_emoji()
                await asyncio.sleep(30)
            except Exception as e:
                print(f"خطا در حفظ حالت آنلاین برای {self.phone}: {e}")
                await asyncio.sleep(60)

    @staticmethod
    def normalize_scheduled_target(target):
        """Return a Telethon-compatible target from the persisted panel value."""
        value = str(target or "").strip()
        if value.lstrip("-").isdigit():
            return int(value)
        return value

    async def scheduled_message_loop(self):
        """Send the per-account scheduled message configured from the helper panel."""
        active_signature = None
        next_send_at = None

        while self.is_running and not self.shutdown_requested:
            try:
                settings = self.get_data()
                enabled = settings.get("scheduled_message_enabled") == "on"
                target = str(
                    settings.get("scheduled_message_target", "") or ""
                ).strip()
                message_text = str(
                    settings.get("scheduled_message_text", "") or ""
                ).strip()
                try:
                    interval_minutes = int(
                        settings.get(
                            "scheduled_message_interval_minutes",
                            "5",
                        )
                    )
                except (TypeError, ValueError):
                    interval_minutes = 5
                interval_minutes = max(1, min(interval_minutes, 10080))

                if not enabled or not target or not message_text:
                    active_signature = None
                    next_send_at = None
                    await asyncio.sleep(5)
                    continue

                signature = (target, message_text, interval_minutes)
                now = time.monotonic()
                if signature != active_signature:
                    active_signature = signature
                    next_send_at = now + (interval_minutes * 60)

                if next_send_at is None or now < next_send_at:
                    remaining = (
                        5
                        if next_send_at is None
                        else max(1, min(5, next_send_at - now))
                    )
                    await asyncio.sleep(remaining)
                    continue

                await self.queued_send_message(
                    self.normalize_scheduled_target(target),
                    message_text,
                    priority=60,
                )
                self.last_activity = time.time()
                next_send_at = time.monotonic() + (interval_minutes * 60)
                print(
                    f"✅ پیام زمان‌بندی‌شده سلف {self.phone} "
                    f"به {target} ارسال شد"
                )
            except FloodWaitError as exc:
                wait_seconds = max(1, int(getattr(exc, "seconds", 60)))
                print(
                    f"⏳ محدودیت تلگرام برای پیام زمان‌بندی‌شده "
                    f"{self.phone}: {wait_seconds} ثانیه"
                )
                next_send_at = time.monotonic() + wait_seconds
                await asyncio.sleep(min(wait_seconds, 60))
            except Exception as exc:
                print(
                    f"❌ خطا در ارسال زمان‌بندی‌شده برای "
                    f"{self.phone}: {exc}"
                )
                next_send_at = time.monotonic() + 60
                await asyncio.sleep(10)

    @staticmethod
    def timed_photo_ttl(message):
        """Return the TTL for a self-destructing photo, otherwise None."""
        media = getattr(message, "media", None)
        if not isinstance(media, types.MessageMediaPhoto):
            return None

        try:
            ttl_seconds = int(getattr(media, "ttl_seconds", 0) or 0)
        except (TypeError, ValueError):
            return None
        return ttl_seconds if ttl_seconds > 0 else None

    async def save_timed_photo(self, event):
        """Download a timed photo immediately and re-upload it to Saved Messages."""
        ttl_seconds = self.timed_photo_ttl(event.message)
        if ttl_seconds is None:
            return False

        settings = self.get_data()
        if settings.get("save_timed_photos", "on") != "on":
            return False

        chat_id = int(getattr(event, "chat_id", 0) or 0)
        message_id = int(getattr(event, "id", 0) or 0)
        job_key = (chat_id, message_id)
        if job_key in self.timed_photo_jobs:
            return False

        self.timed_photo_jobs.add(job_key)
        try:
            max_bytes = max(1, min(int(os.getenv("MAX_IN_MEMORY_MEDIA_MB", "50") or 50), 100)) * 1024 * 1024
            declared_size = int(getattr(getattr(event.message, "file", None), "size", 0) or 0)
            if declared_size and declared_size > max_bytes:
                raise ValueError("حجم عکس زمان‌دار بیشتر از سقف حافظه است")
            download_timeout = max(30, min(ttl_seconds + 30, 90))
            photo_bytes = await asyncio.wait_for(
                self.client.download_media(event.message, file=bytes),
                timeout=download_timeout,
            )
            if not photo_bytes:
                raise RuntimeError("داده عکس از تلگرام دریافت نشد")
            if len(photo_bytes) > max_bytes:
                raise ValueError("حجم عکس زمان‌دار بیشتر از سقف حافظه است")

            sender_id = int(getattr(event, "sender_id", 0) or 0)
            sender_name = str(sender_id or "نامشخص")
            try:
                sender = await event.get_sender()
                name_parts = [
                    getattr(sender, "first_name", "") or "",
                    getattr(sender, "last_name", "") or "",
                ]
                display_name = " ".join(part for part in name_parts if part).strip()
                username = getattr(sender, "username", "") or ""
                sender_name = display_name or (
                    f"@{username}" if username else sender_name
                )
            except Exception:
                pass

            original_caption = (getattr(event, "raw_text", "") or "").strip()
            caption_lines = [
                "⏱ عکس زمان‌دار ذخیره شد",
                f"👤 فرستنده: {sender_name}",
                f"🆔 شناسه: {sender_id or 'نامشخص'}",
                f"⌛ زمان نمایش: {ttl_seconds} ثانیه",
                f"🕒 ذخیره: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ]
            if original_caption:
                caption_lines.extend(["", "📝 متن:", original_caption])
            caption = "\n".join(caption_lines)[:1024]

            photo_file = io.BytesIO(photo_bytes)
            photo_file.name = (
                f"timed_photo_{sender_id or 'unknown'}_{message_id}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            )
            try:
                await self.client.send_file(
                    "me",
                    photo_file,
                    caption=caption,
                    force_document=False,
                    parse_mode=None,
                    silent=True,
                )
            finally:
                photo_file.close()

            print(
                f"✅ عکس زمان‌دار پیام {message_id} برای {self.phone} "
                "در Saved Messages ذخیره شد"
            )
            return True
        except asyncio.TimeoutError:
            print(
                f"⏰ زمان دریافت عکس زمان‌دار پیام {message_id} "
                f"برای {self.phone} تمام شد"
            )
            return False
        except Exception as exc:
            print(
                f"❌ ذخیره عکس زمان‌دار پیام {message_id} برای {self.phone} "
                f"ناموفق بود: {type(exc).__name__}"
            )
            return False
        finally:
            self.timed_photo_jobs.discard(job_key)
    
    async def register_handlers(self):
        """ثبت هندلرهای رویداد"""
        
        # هندلر پیام‌های دریافتی از ادمین برای خاموش کردن
        @self.client.on(events.NewMessage(incoming=True))
        async def handle_admin_commands(event):
            try:
                self.last_activity = time.time()
                if not await self.is_configured_admin_event(event):
                    return
                message_text = event.raw_text.lower().strip()
                
                if message_text == '/off':
                    await self.handle_shutdown(event)
                    
            except Exception as e:
                print(f"خطا در پردازش دستور ادمین برای {self.phone}: {e}")
        
        # هندلر پیام‌های دریافتی
        @self.client.on(events.NewMessage(incoming=True))
        async def handle_incoming_messages(event):
            try:
                self.last_activity = time.time()

                await self.save_timed_photo(event)
                
                if await self.is_configured_admin_event(event):
                    return
                    
                if not event.is_private:
                    return
                    
                # Security boundary: verification codes and passwords belong
                # only to the account owner.  They are never forwarded,
                # copied, logged, or deleted by the self-bot.
                return
                        
            except Exception as e:
                print(f"خطا در پردازش پیام دریافتی برای {self.phone}: {e}")
        
        # هندلر پیام‌های ارسالی توسط مالک
        @self.client.on(events.NewMessage(outgoing=True))
        async def handle_outgoing_messages(event):
            try:
                self.last_activity = time.time()
                self.last_owner_activity = self.last_activity
                self.observed_presence_online = True
                self.observed_presence_at = self.last_activity
                
                if not self.is_owner_outgoing_event(event):
                    return
                    
                message_text = event.raw_text.lower().strip()

                if await self.handle_form_status_command(event):
                    return
                
                handlers = {
                    'help': self.help_handler,
                    '.help': self.help_handler,
                    'راهنما': self.help_handler,
                    'پنل': self.inline_panel_handler,
                    '.پنل': self.inline_panel_handler,
                    'panel': self.inline_panel_handler,
                    '.panel': self.inline_panel_handler,
                    'menu': self.inline_panel_handler,
                    'منو': self.inline_panel_handler,
                    
                    'status': self.status_handler,
                    'وضعیت': self.status_handler,
                    '.status': self.status_handler,
                    '.وضعیت': self.status_handler,
                    
                    'heart': self.heart_handler,
                    'قلب': self.heart_handler,
                    '.heart': self.heart_handler,
                    '.قلب': self.heart_handler,
                    
                    'listcrash': self.listcrash_handler,
                    'لیست کراش': self.listcrash_handler,
                    '.listcrash': self.listcrash_handler,
                    '.لیست کراش': self.listcrash_handler,
                    
                    'listenemy': self.listenemy_handler,
                    'لیست انمی': self.listenemy_handler,
                    '.listenemy': self.listenemy_handler,
                    '.لیست انمی': self.listenemy_handler,
                    
                    'tagall': self.tagall_handler,
                    'تگ': self.tagall_handler,
                    '.tagall': self.tagall_handler,
                    '.تگ': self.tagall_handler,
                    
                    'tagadmins': self.tagadmins_handler,
                    'تگ ادمین ها': self.tagadmins_handler,
                    '.tagadmins': self.tagadmins_handler,
                    '.تگ ادمین ها': self.tagadmins_handler,
                    
                    'sessions': self.sessions_handler,
                    'نشست های فعال': self.sessions_handler,
                    '.sessions': self.sessions_handler,
                    '.نشست های فعال': self.sessions_handler,
                    
                    'listfonts': self.listfonts_handler,
                    'لیست فونت': self.listfonts_handler,
                    '.listfonts': self.listfonts_handler,
                    '.لیست فونت': self.listfonts_handler,
                    
                    'secretary': self.secretary_handler,
                    'منشی': self.secretary_handler,
                    '.secretary': self.secretary_handler,
                    '.منشی': self.secretary_handler,
                    
                    'groups': self.groups_handler,
                    'گروه ها': self.groups_handler,
                    '.groups': self.groups_handler,
                    '.گروه ها': self.groups_handler,
                    
                    'tools': self.tools_handler,
                    'ابزار': self.tools_handler,
                    '.tools': self.tools_handler,
                    '.ابزار': self.tools_handler,
                    
                    'settings': self.settings_handler,
                    'تنظیمات': self.settings_handler,
                    '.settings': self.settings_handler,
                    '.تنظیمات': self.settings_handler,
                    
                    'forward': self.forward_handler,
                    'فوروارد': self.forward_handler,
                    '.forward': self.forward_handler,
                    '.فوروارد': self.forward_handler,
                }
                
                for key, handler in handlers.items():
                    if message_text == key:
                        await handler(event)
                        return
                
                if message_text.startswith('info') or message_text.startswith('اطلاعات'):
                    await self.info_handler(event)
                    
            except Exception as e:
                print(f"خطا در هندلر پیام‌های ارسالی برای {self.phone}: {e}")

        @self.client.on(events.UserUpdate())
        async def handle_own_presence_update(event):
            """Apply configured name emoji when Telegram changes our status."""
            try:
                if int(getattr(event, "user_id", 0) or 0) != int(
                    self.owner_id or 0
                ):
                    return
                if getattr(event, "online", None) is True:
                    self.observed_presence_online = True
                    self.observed_presence_at = time.time()
                    self.last_owner_activity = time.time()
                elif getattr(event, "online", None) is False:
                    self.observed_presence_online = False
                    self.observed_presence_at = time.time()
                await self.apply_presence_name_emoji()
            except FloodWaitError as exc:
                print(
                    f"⏳ محدودیت تغییر ایموجی وضعیت {self.phone}: "
                    f"{max(1, int(getattr(exc, 'seconds', 60)))} ثانیه"
                )
            except Exception as exc:
                print(
                    f"⚠️ پردازش وضعیت حضور {self.phone} ناموفق بود: "
                    f"{type(exc).__name__}"
                )
        
        await self.register_settings_handlers()
        if self.feature_engine is not None:
            await self.feature_engine.register_handlers()
        await self.auto_reply_secretary()
        
        print(f"✅ تمام هندلرها برای {self.phone} ثبت شدند")

    async def handle_shutdown(self, event):
        """مدیریت خاموش کردن سلف توسط ادمین"""
        try:
            print(f"🛑 درخواست خاموش کردن برای {self.phone} از طرف ادمین")
            
            shutdown_msg = await event.reply(f"""
🔴 **درخواست خاموش کردن دریافت شد**

📱 **شماره:** `{self.phone}`
🆔 **آیدی:** `{self.owner_id}`
⏰ **زمان:** {datetime.now().strftime('%H:%M:%S')}

🔄 **در حال خاموش کردن...**
            """)
            
            self.account_manager.deactivate_account(self.phone)
            self.mark_controller_stopped(
                status="stopped",
                detail=None,
            )
            self.shutdown_requested = True
            self.is_running = False
            
            await shutdown_msg.edit(f"""
🔴 **سلف خاموش شد**

📱 **شماره:** `{self.phone}`
🆔 **آیدی:** `{self.owner_id}`
⏰ **زمان:** {datetime.now().strftime('%H:%M:%S')}

✅ **اکانت با موفقیت غیرفعال شد**
            """)
            
            await self.client.disconnect()
            print(f"✅ اکانت {self.phone} با موفقیت خاموش شد")
            
        except Exception as e:
            print(f"❌ خطا در خاموش کردن اکانت {self.phone}: {e}")
            try:
                await event.reply(f"❌ خطا در خاموش کردن: {e}")
            except:
                pass

    def mark_controller_stopped(self, *, status, detail=None):
        """هماهنگ‌کردن خاموشی عمدی یا انقضا با Watchdog ربات اصلی."""
        try:
            if not USERS_DB.exists():
                return
            with db_connect(USERS_DB, timeout=10) as conn:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(users)")
                }
                if "self_enabled" not in columns:
                    return
                values = [
                    "self_enabled = 0",
                    "self_pid = NULL",
                    "self_status = ?",
                    "updated_at = datetime('now')",
                ]
                parameters = [str(status)]
                if "self_last_error" in columns:
                    values.append("self_last_error = ?")
                    parameters.append(detail)
                if "self_last_stopped_at" in columns:
                    values.append("self_last_stopped_at = datetime('now')")
                if "self_next_restart_at" in columns:
                    values.append("self_next_restart_at = NULL")
                if "self_consecutive_failures" in columns:
                    values.append("self_consecutive_failures = 0")
                parameters.append(self.phone)
                conn.execute(
                    f'''UPDATE users
                        SET {", ".join(values)}
                        WHERE phone = ?''',
                    parameters,
                )
        except Exception as exc:
            print(
                f"⚠️ ثبت وضعیت خاموشی {self.phone} در ربات اصلی "
                f"ناموفق بود: {exc}"
            )

    async def inline_panel_handler(self, event):
        """نمایش پنل Inline هلپر در همان چتی که فرمان ارسال شده است."""
        try:
            config = get_helper_config(USERS_DB)
            helper_username = config.get("username", "")
            if not config.get("enabled") or not helper_username:
                await event.edit(
                    "❌ بات هلپر پنل هنوز توسط ادمین اصلی تنظیم نشده است."
                )
                return

            await event.edit("⏳ در حال ساخت پنل...")
            results = await asyncio.wait_for(
                self.client.inline_query(
                    helper_username,
                    "panel",
                    entity=event.chat_id,
                ),
                timeout=15,
            )
            if not results:
                await event.edit(
                    "❌ بات هلپر پاسخی برای پنل برنگرداند. "
                    "تنظیم Inline هلپر را بررسی کنید."
                )
                return

            reply_to = getattr(event.message, "reply_to_msg_id", None)
            await results[0].click(
                event.chat_id,
                reply_to=reply_to,
            )
            await event.delete()
        except asyncio.TimeoutError:
            await event.edit(
                "❌ زمان پاسخ بات هلپر تمام شد؛ چند لحظه بعد دوباره «پنل» را ارسال کنید."
            )
        except FloodWaitError as exc:
            wait_seconds = max(1, int(getattr(exc, "seconds", 60)))
            print(
                f"محدودیت تلگرام برای پنل هلپر {self.phone}: "
                f"{wait_seconds} ثانیه"
            )
        except Exception as exc:
            print(f"خطا در ساخت پنل هلپر برای {self.phone}: {type(exc).__name__}")
            try:
                await event.edit(
                    "❌ ساخت پنل از طریق بات هلپر ناموفق بود. "
                    "وضعیت هلپر را از ادمین اصلی بررسی کنید."
                )
            except Exception:
                pass

    async def help_handler(self, event):
        """هندلر دستور help"""
        try:
            help_text = await self.generate_help_text()
            await event.reply(help_text)
            await event.delete()
        except Exception as e:
            print(f"خطا در دستور help برای {self.phone}: {e}")
    
    async def generate_help_text(self):
        """تولید متن راهنما"""
        me = await self.client.get_me()
        return (
            "📚 **راهنمای سریع سلف**\n"
            "━━━━━━━━━━━━━━\n\n"
            f"👤 {me.first_name or 'کاربر'} | 📱 `{self.phone}`\n\n"
            "🎛 **دسترسی اصلی**\n"
            "• `پنل` — بازکردن پنل دکمه‌ای در همان چت\n"
            "• `وضعیت` — نمایش وضعیت اجرا\n"
            "• `راهنما` — نمایش همین راهنما\n\n"
            "💚 **کار با ریپلای**\n"
            "• `تنظیم دوست` — ثبت فرستنده پیام به‌عنوان دوست\n"
            "• `حذف دوست` — حذف او از فهرست دوستان\n"
            "• `اطلاعات` — نمایش اطلاعات کاربر\n"
            "• `ری‌اکت ❤️` — واکنش به پیام\n\n"
            "👥 **گروه و ابزار**\n"
            "• `تگ` — تگ اعضای گروه\n"
            "• `تگ ادمین ها` — تگ مدیران\n"
            "• `دانلود` — ذخیره رسانه ریپلای‌شده\n"
            "• `استیکر` — تبدیل پیام ریپلای‌شده به استیکر با QuotLyBot\n"
            "• `تنظیم تاس ۱` — تنظیم تک‌بارمصرف تاس بعدی همین چت\n"
            "• `تاس ۱` — اجرای مستقیم تاس نمایشی با نتیجه انتخابی\n"
            "• `لغو تاس` — لغو تنظیم تاس همین چت\n"
            "• `تنظیم کازینو جکپات` — تنظیم تک‌بارمصرف کازینوی بعدی\n"
            "• `کازینو` — ارسال یک کازینوی رسمی و عادی تلگرام\n"
            "• `کازینو ۶۴` — اجرای مستقیم کازینوی نمایشی\n"
            "• `لغو کازینو` — لغو تنظیم کازینو همین چت\n"
            "• `شیر یا خط` — انتخاب تصادفی شیر یا خط\n"
            "• `عدد تصادفی ۱ ۱۰۰` — ساخت عدد تصادفی در بازه\n"
            "• `انتخاب چای | قهوه | آبمیوه` — انتخاب یکی از گزینه‌ها\n"
            "• `سنگ کاغذ قیچی سنگ` — بازی سریع با سلف\n"
            "• `دوز` — شروع بازی مستقل با ریپلای روی پیام حریف\n"
            "• `دوز ۱` تا `دوز ۹` — انتخاب خانه بازی\n"
            "• `لغو دوز` — پایان بازی فعال همان چت\n"
            "• `ترجمه en` — ترجمه پیام ریپلای‌شده\n"
            "• `ویس زن سلام` — تبدیل متن به ویس\n\n"
            "🧾 **فرم و منشی**\n"
            "• فرم‌ساز سفارش از پنل، فرم‌های مرحله‌ای پیوی می‌سازد.\n"
            "• سؤال‌وجواب‌ها و متن عمومی منشی را خودتان تعیین می‌کنید.\n"
            "• وضعیت فرم با ریپلای روی نسخه Saved Messages تغییر می‌کند.\n\n"
            "دستورهای فارسی با نقطه و بدون نقطه کار می‌کنند.\n"
            "برای دیدن همه دستورات وارد **پنل ← راهنما** شوید."
        )
    
    async def status_handler(self, event):
        """هندلر وضعیت سیستم"""
        try:
            async def get_ping():
                st = time.time()
                await self.client.get_me()
                return time.time() - st
                
            try: 
                ping = await get_ping()
                ping_text = f"{ping * 1000:.0f} ms"
            except: 
                ping_text = "N/A"
                
            try:
                mp = psutil.virtual_memory().percent
            except:
                mp = "N/A"
            try:
                cp = psutil.cpu_percent()
            except:
                cp = "N/A"
                
            me = await self.client.get_me()
            js = self.get_data()
            
            state = lambda key: (
                "✅ فعال" if js.get(key) == "on" else "❌ غیرفعال"
            )
            txt = (
                "📊 **وضعیت سلف**\n"
                "━━━━━━━━━━━━━━\n\n"
                f"🟢 وضعیت اجرا: فعال\n"
                f"⏱ پینگ: `{ping_text}`\n"
                f"📈 مصرف RAM: `{mp}%`\n"
                f"🖥 مصرف CPU: `{cp}%`\n\n"
                "👤 **اطلاعات حساب**\n"
                f"• نام: {me.first_name or 'ثبت نشده'}\n"
                f"• شماره: `{self.phone}`\n"
                f"• آیدی: `{me.id}`\n"
                f"• نام کاربری: @{me.username or 'ثبت نشده'}\n\n"
                "⚙️ **تنظیمات مهم**\n"
                f"• همیشه آنلاین: {state('online_status')}\n"
                f"• تایپینگ: {state('typing_action')}\n"
                f"• منشی: {state('secretary')}\n"
                f"• پاسخ صمیمی دوست: {state('friend_affection_reply')}"
            )
            await event.reply(txt)
            await event.delete()
            
        except Exception as e:
            print(f"خطا در status برای {self.phone}: {e}")
    
    async def heart_handler(self, event):
        """هندلر انیمیشن قلب"""
        try:
            message = await event.reply("💫 در حال ساخت انیمیشن قلب...")
            animations = ["💖", "❤️", "🧡", "💛", "💚", "💙", "💜", "🤎", "🖤", "🤍"]
            
            for x in range(3):
                for i in range(1, 11):
                    heart = animations[i % len(animations)]
                    txt = f"✨ {x+1} {heart * i} | {10 * i}%"
                    await message.edit(txt)
                    await asyncio.sleep(0.2)
            
            await message.edit("💖 **انیمیشن قلب کامل شد** ✨")
        except Exception as e:
            print(f"خطا در دستور heart برای {self.phone}: {e}")
    
    async def listcrash_handler(self, event):
        """هندلر لیست کراش"""
        try:
            js = self.get_data()
            if js.get('crash'):
                txt = "💖 **لیست کراش:**\n\n"
                for i in js.get('crash', []):
                    txt += f"• [{i}](tg://user?id={i})\n"
            else:
                txt = "💔 **لیست کراش خالی است.**"
            await event.reply(txt)
            await event.delete()
        except Exception as e:
            print(f"خطا در دستور listcrash برای {self.phone}: {e}")
    
    async def listenemy_handler(self, event):
        """هاندلر لیست دشمن"""
        try:
            js = self.get_data()
            if js.get('enemy'):
                txt = "😈 **لیست دشمن:**\n\n"
                for i in js.get('enemy', []):
                    txt += f"• [{i}](tg://user?id={i})\n"
            else:
                txt = "😇 **لیست دشمن خالی است.**"
            await event.reply(txt)
            await event.delete()
        except Exception as e:
            print(f"خطا در دستور listenemy برای {self.phone}: {e}")
    
    async def tagall_handler(self, event):
        """هندلر تگ همه"""
        try:
            if not event.is_group:
                await event.reply("❌ **این دستور فقط در گروه کار می‌کند**")
                return
                
            processing_msg = await event.reply("🔄 **در حال تگ کردن اعضا...**")
            mentions = "👥 **تگ همه اعضا:**\n\n"
            chat = await event.get_input_chat()
            count = 0
            
            async for x in self.client.iter_participants(chat, 50):
                if not x.bot and not x.deleted:
                    mentions += f" [{x.first_name}](tg://user?id={x.id})"
                    count += 1
                    if count % 10 == 0:
                        await asyncio.sleep(0.5)
                
            mentions += f"\n\n✅ **تعداد:** `{count}` نفر"
            await processing_msg.delete()
            await event.reply(mentions)
            await event.delete()
            
        except Exception as e:
            print(f"خطا در دستور tagall برای {self.phone}: {e}")
    
    async def tagadmins_handler(self, event):
        """هندلر تگ ادمین‌ها"""
        try:
            if not event.is_group:
                await event.reply("❌ **این دستور فقط در گروه کار می‌کند**")
                return
                
            mentions = "👮‍♂️ **تگ ادمین‌ها:**\n\n"
            chat = await event.get_input_chat()
            count = 0
            async for x in self.client.iter_participants(chat, filter=ChannelParticipantsAdmins):
                mentions += f" [{x.first_name}](tg://user?id={x.id})"
                count += 1
                
            mentions += f"\n\n✅ **تعداد:** `{count}` نفر"
            await event.reply(mentions)
            await event.delete()
            
        except Exception as e:
            print(f"خطا در دستور tagadmins برای {self.phone}: {e}")
    
    async def sessions_handler(self, event):
        """هندلر نشست‌های فعال"""
        try:
            result = await self.client(functions.account.GetAuthorizationsRequest())
            txt = "🔐 **نشست‌های فعال تلگرام**\n━━━━━━━━━━━━━━\n\n"
            
            for i, auth in enumerate(result.authorizations, 1):
                device = auth.device_model or "نامشخص"
                platform = auth.platform or "نامشخص"
                country = auth.country or "نامشخص"
                ip = auth.ip or "نامشخص"
                
                txt += f"**نشست {i}**\n"
                txt += f"📱 **دستگاه:** `{device}`\n"
                txt += f"🌐 **پلتفرم:** `{platform}`\n"
                txt += f"🕒 **تاریخ:** `{auth.date_created}`\n"
                txt += f"🌍 **کشور:** `{country}`\n"
                txt += f"📶 **IP:** `{ip}`\n"
                txt += "──────────────\n"
                
            await event.reply(txt)
            await event.delete()
            
        except Exception as e:
            print(f"خطا در دستور sessions برای {self.phone}: {e}")
    
    async def info_handler(self, event):
        """هندلر اطلاعات کاربر"""
        try:
            if event.is_reply:
                get_message = await event.get_reply_message()
                get_id = get_message.sender_id
            else:
                get_id = event.sender_id
                
            full = await self.client(GetFullUserRequest(get_id))
            user = full.users[0]
            
            status = "آنلاین" if user.status else "آفلاین"
            is_bot = "✅" if user.bot else "❌"
            is_verified = "✅" if user.verified else "❌"
            is_restricted = "✅" if user.restricted else "❌"
            is_scam = "✅" if user.scam else "❌"
            is_fake = "✅" if user.fake else "❌"
            
            info_text = (
                "👤 **اطلاعات کاربر**\n"
                "━━━━━━━━━━━━━━\n\n"
                f"🆔 آیدی: `{user.id}`\n"
                f"👤 نام: {user.first_name or 'ثبت نشده'}\n"
                f"📛 نام خانوادگی: {user.last_name or 'ثبت نشده'}\n"
                f"🔗 نام کاربری: @{user.username or 'ثبت نشده'}\n"
                f"📞 شماره: {user.phone or 'نمایش داده نمی‌شود'}\n"
                f"📝 بیو: {full.full_user.about or 'ثبت نشده'}\n\n"
                "🔍 **وضعیت حساب**\n"
                f"• آنلاین: {status}\n"
                f"• ربات: {is_bot}\n"
                f"• تأییدشده: {is_verified}\n"
                f"• محدودشده: {is_restricted}\n"
                f"• کلاهبرداری: {is_scam}\n"
                f"• جعلی: {is_fake}"
            )
            
            await event.reply(info_text)
            await event.delete()
            
        except Exception as e:
            print(f"خطا در دستور info برای {self.phone}: {e}")
    
    async def listfonts_handler(self, event):
        """نمایش لیست فونت‌ها"""
        try:
            fonts_list = "🎨 **فونت ساعت**\n━━━━━━━━━━━━━━\n\n"
            
            for i, font in enumerate(self.fonts, 1):
                sample = "۱۲:۳۴"
                if i <= len(self.fonts):
                    try:
                        converted = sample.translate(str.maketrans("۱۲۳۴", font[:4]))
                        fonts_list += f"**{i}.** `{converted}` — مدل {i}\n"
                    except:
                        fonts_list += f"**{i}.** `{sample}` — مدل {i}\n"
            
            fonts_list += "\n📝 روش استفاده: `.font 3`"
            await event.reply(fonts_list)
            await event.delete()
        except Exception as e:
            print(f"خطا در listfonts برای {self.phone}: {e}")
    
    async def secretary_handler(self, event):
        """مدیریت منشی هوشمند"""
        secretary_text = (
            "🤖 **منشی و پاسخ خودکار**\n"
            "━━━━━━━━━━━━━━\n\n"
            "• `.secretary on/off` — پاسخ عمومیِ قابل‌تنظیم به پیام‌های "
            "بدون پاسخ\n"
            "• `.autoreply on/off` — پاسخ از سؤال‌وجواب‌های ثبت‌شده\n"
            "• `.addreply سؤال|پاسخ` — ثبت پاسخ دلخواه\n\n"
            "برای دیدن کاربرد هر گزینه، تعیین متن منشی، مدیریت "
            "سؤال‌وجواب‌ها و ساخت فرم وارد **پنل ← منشی و تایپینگ** یا "
            "**پنل ← فرم‌ساز سفارش** شوید."
        )
        await event.reply(secretary_text)
        await event.delete()
    
    async def groups_handler(self, event):
        """منوی مدیریت گروه"""
        groups_text = (
            "👥 **مدیریت گروه**\n"
            "━━━━━━━━━━━━━━\n\n"
            "• `تگ` — تگ اعضا\n"
            "• `تگ ادمین ها` — تگ مدیران\n"
            "• `قفل لینک روشن/خاموش` — کنترل لینک\n"
            "• `فیلتر افزودن عبارت|حذف` — افزودن فیلتر\n"
            "• `سکوت 10` — سکوت کاربر ریپلای‌شده\n"
            "• `رفع سکوت` — برداشتن سکوت\n"
            "• `بلاک` / `آنبلاک` — مدیریت کاربر با ریپلای\n\n"
            "تنظیم همه قفل‌ها: **پنل ← قفل‌ها و فیلتر**"
        )
        await event.reply(groups_text)
        await event.delete()
    
    async def tools_handler(self, event):
        """منوی ابزارها"""
        tools_text = (
            "🧰 **ابزار و رسانه**\n"
            "━━━━━━━━━━━━━━\n\n"
            "• `دانلود` — ذخیره رسانه ریپلای‌شده\n"
            "• `لوگو` — افزودن واترمارک به عکس\n"
            "• `ترجمه en` — ترجمه پیام\n"
            "• `ویس زن متن` / `ویس مرد متن` — متن‌به‌ویس\n"
            "• `آهنگ نام` — جست‌وجوی آهنگ\n"
            "• `اسکرین https://...` — تصویر صفحه وب\n"
            "• `.save on/off` — ذخیره عکس زمان‌دار\n"
            "• `.ضدحذف وضعیت` — وضعیت آرشیو موقت\n\n"
            "تنظیمات کامل: **پنل ← ابزار و رسانه**"
        )
        await event.reply(tools_text)
        await event.delete()
    
    async def settings_handler(self, event):
        """منوی تنظیمات"""
        settings_text = (
            "⚙️ **تنظیمات سلف**\n"
            "━━━━━━━━━━━━━━\n\n"
            "برای تنظیم همه امکانات، عبارت **پنل** را ارسال کنید.\n\n"
            "دستورهای سریع:\n"
            "• `.online on/off` — همیشه آنلاین\n"
            "• `.typing on/off` — نمایش تایپینگ\n"
            "• `.secretary on/off` — منشی\n"
            "• `.timename on/off` — ساعت در نام\n"
            "• `.timebio on/off` — ساعت در بیو\n"
            "• `.font 1-10` — فونت ساعت\n"
            "• `.save on/off` — ذخیره عکس زمان‌دار\n"
            "• `.محبت دوست on/off` — ریپلای صمیمی به دوستان"
            "\n• `.پاسخ دشمن on/off` — ریپلای از متن‌های ثبت‌شده دشمن"
        )
        await event.reply(settings_text)
        await event.delete()
    
    async def forward_handler(self, event):
        """منوی فوروارد خودکار"""
        forward_text = (
            "🔄 **فوروارد خودکار**\n"
            "━━━━━━━━━━━━━━\n\n"
            "• `.autoforward on` — فعال‌کردن\n"
            "• `.autoforward off` — غیرفعال‌کردن\n\n"
            "پیام‌های کانال‌های ثبت‌شده به مقصدهای تعیین‌شده منتقل می‌شوند."
        )
        await event.reply(forward_text)
        await event.delete()

    @staticmethod
    def normalize_automatic_reply_text(value):
        text = str(value or "").strip().lower()
        text = text.translate(
            str.maketrans(
                {
                    "ي": "ی",
                    "ى": "ی",
                    "ك": "ک",
                    "\u200c": " ",
                }
            )
        )
        for mark in ("؟", "?", "!", ".", "،", ",", "؛", ";", ":", "ـ"):
            text = text.replace(mark, " ")
        return " ".join(text.split())

    def find_secretary_response(self, message_text):
        """Return the best configured Q&A response for a private message."""
        normalized_message = self.normalize_automatic_reply_text(message_text)
        if not normalized_message:
            return None

        partial_matches = []
        for raw_pattern, response in self.secretary_messages.items():
            aliases = [
                self.normalize_automatic_reply_text(alias)
                for alias in str(raw_pattern).replace("،", "/").split("/")
            ]
            aliases = [alias for alias in aliases if alias]
            if normalized_message in aliases:
                return response
            for alias in aliases:
                if len(alias) >= 2 and alias in normalized_message:
                    partial_matches.append((len(alias), response))
        if not partial_matches:
            return None
        partial_matches.sort(key=lambda item: item[0], reverse=True)
        return partial_matches[0][1]

    @staticmethod
    def form_status_label(status):
        return {
            "processing": "⏳ در حال پردازش",
            "ready": "🧰 آماده ارسال",
            "shipped": "📦 ارسال شده",
            "completed": "✅ تکمیل شده",
            "cancelled": "❌ لغو شده",
        }.get(str(status or "").strip().lower(), "⏳ در حال پردازش")

    @staticmethod
    def parse_form_status_command(text):
        normalized = TelegramAccount.normalize_automatic_reply_text(text)
        return {
            "در حال پردازش": "processing",
            "درحال پردازش": "processing",
            "پردازش": "processing",
            "آماده ارسال": "ready",
            "اماده ارسال": "ready",
            "ارسال شده": "shipped",
            "ارسال شد": "shipped",
            "فرستاده شد": "shipped",
            "تکمیل شده": "completed",
            "تکمیل شد": "completed",
            "انجام شد": "completed",
            "لغو شده": "cancelled",
            "لغو شد": "cancelled",
        }.get(normalized)

    @staticmethod
    def format_form_summary(
        form,
        answers,
        *,
        submission_id=None,
        status=None,
    ):
        title = str(form.get("name") or "فرم").strip()
        lines = [f"🧾 فرم «{title}»"]
        if submission_id is not None:
            lines.append(f"🔖 شماره درخواست: #{int(submission_id)}")
        lines.extend(["", "📋 اطلاعات ثبت‌شده"])
        fields = list(form.get("fields") or [])
        for index, field in enumerate(fields):
            answer = str(answers[index] if index < len(answers) else "").strip()
            lines.extend(
                [
                    "",
                    f"{index + 1}. {str(field.get('question') or '').strip()}",
                    f"↳ {answer or '—'}",
                ]
            )
        if status is not None:
            lines.extend(
                [
                    "",
                    f"وضعیت: {TelegramAccount.form_status_label(status)}",
                ]
            )
        return "\n".join(lines)

    async def start_form_for_user(self, event, form):
        fields = list(form.get("fields") or [])
        if not fields:
            await event.reply("❌ این فرم هنوز سؤالی ندارد.")
            return True
        save_form_session(
            DATABASE_DIR,
            self.phone,
            user_id=int(event.sender_id),
            chat_id=int(event.chat_id),
            form_id=int(form["id"]),
            current_index=0,
            stage="answering",
            answers=[],
        )
        await event.reply(
            f"🧾 فرم «{form['name']}» شروع شد.\n\n"
            f"سؤال ۱ از {len(fields)}:\n"
            f"{fields[0]['question']}\n\n"
            "برای توقف در هر مرحله بنویسید: لغو فرم"
        )
        return True

    async def send_form_menu(self, event, settings=None):
        settings = settings or self.get_data()
        if settings.get("form_builder_enabled") != "on":
            return False
        forms = list_form_templates(
            DATABASE_DIR,
            self.phone,
            active_only=True,
            limit=30,
        )
        if not forms:
            return False
        now = time.monotonic()
        menu_sent_at = getattr(self, "form_menu_sent_at", None)
        if menu_sent_at is None:
            menu_sent_at = {}
            self.form_menu_sent_at = menu_sent_at
        last_sent = menu_sent_at.get(int(event.sender_id))
        if last_sent is not None and now - last_sent < 300:
            return False
        lines = [
            str(
                settings.get("form_intro_text")
                or "برای ثبت درخواست، نام یکی از فرم‌های زیر را ارسال کنید:"
            ).strip(),
            "",
        ]
        lines.extend(
            f"• {form['name']} — ارسال «{form['trigger_text']}»"
            for form in reversed(forms)
        )
        await event.reply("\n".join(lines))
        menu_sent_at[int(event.sender_id)] = now
        return True

    async def process_form_message(self, event, message_text):
        """Handle an active form session or start a matching form."""
        settings = self.get_data()
        if settings.get("form_builder_enabled") != "on":
            return False

        user_id = int(event.sender_id)
        chat_id = int(event.chat_id)
        lock = self.form_user_locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            normalized = self.normalize_automatic_reply_text(message_text)
            session = get_form_session(DATABASE_DIR, self.phone, user_id)

            if session and normalized in {"لغو", "لغو فرم", "انصراف"}:
                clear_form_session(DATABASE_DIR, self.phone, user_id)
                await event.reply(
                    "✅ فرم نیمه‌کاره لغو شد. برای شروع دوباره، نام فرم را "
                    "ارسال کنید."
                )
                return True

            if not session:
                form = find_form_template(
                    DATABASE_DIR,
                    self.phone,
                    normalized,
                )
                if not form:
                    return False
                return await self.start_form_for_user(event, form)

            form = get_form_template(
                DATABASE_DIR,
                self.phone,
                int(session["form_id"]),
            )
            if not form:
                clear_form_session(DATABASE_DIR, self.phone, user_id)
                await event.reply(
                    "❌ فرم انتخاب‌شده دیگر موجود نیست. نام یک فرم فعال را "
                    "دوباره ارسال کنید."
                )
                return True

            fields = list(form.get("fields") or [])
            answers = [str(answer) for answer in session.get("answers") or []]
            stage = str(session.get("stage") or "answering")

            if stage == "confirming":
                if normalized in {"تایید", "تأیید", "بله", "تایید میکنم"}:
                    preview_summary = self.format_form_summary(form, answers)
                    submission_id = create_form_submission(
                        DATABASE_DIR,
                        self.phone,
                        form_id=int(form["id"]),
                        form_name=str(form["name"]),
                        user_id=user_id,
                        chat_id=chat_id,
                        summary_text=preview_summary,
                        answers=answers,
                    )
                    final_summary = self.format_form_summary(
                        form,
                        answers,
                        submission_id=submission_id,
                        status="processing",
                    )
                    customer_message = await event.reply(
                        f"{final_summary}\n\n"
                        "✅ درخواست شما ثبت شد. هر تغییر وضعیت در همین "
                        "گفت‌وگو اطلاع داده می‌شود.",
                        parse_mode=None,
                    )
                    admin_message = await self.client.send_message(
                        "me",
                        f"{final_summary}\n\n"
                        f"👤 کاربر: {user_id}\n"
                        "برای تغییر وضعیت روی همین پیام ریپلای کنید و یکی "
                        "از این عبارت‌ها را بفرستید:\n"
                        "در حال پردازش | آماده ارسال | ارسال شده | "
                        "تکمیل شده | لغو شده",
                        parse_mode=None,
                    )
                    attach_form_submission_messages(
                        DATABASE_DIR,
                        self.phone,
                        submission_id,
                        customer_message_id=int(customer_message.id),
                        admin_message_id=int(admin_message.id),
                    )
                    clear_form_session(DATABASE_DIR, self.phone, user_id)
                    return True

                if normalized in {"ویرایش", "اصلاح", "از اول"}:
                    save_form_session(
                        DATABASE_DIR,
                        self.phone,
                        user_id=user_id,
                        chat_id=chat_id,
                        form_id=int(form["id"]),
                        current_index=0,
                        stage="answering",
                        answers=[],
                    )
                    await event.reply(
                        f"✏️ فرم از ابتدا باز شد.\n\n"
                        f"سؤال ۱ از {len(fields)}:\n{fields[0]['question']}"
                    )
                    return True

                await event.reply(
                    "لطفاً یکی از این گزینه‌ها را ارسال کنید:\n"
                    "تأیید | ویرایش | لغو فرم"
                )
                return True

            if not message_text.strip():
                await event.reply("❌ پاسخ خالی پذیرفته نمی‌شود.")
                return True
            if len(message_text.strip()) > 200:
                await event.reply(
                    "❌ پاسخ هر سؤال حداکثر ۲۰۰ نویسه است. پاسخ کوتاه‌تر "
                    "را دوباره بفرستید."
                )
                return True

            current_index = max(0, int(session.get("current_index") or 0))
            if current_index >= len(fields):
                current_index = len(answers)
            answers = answers[:current_index]
            answers.append(message_text.strip())
            next_index = current_index + 1
            if next_index < len(fields):
                save_form_session(
                    DATABASE_DIR,
                    self.phone,
                    user_id=user_id,
                    chat_id=chat_id,
                    form_id=int(form["id"]),
                    current_index=next_index,
                    stage="answering",
                    answers=answers,
                )
                await event.reply(
                    f"سؤال {next_index + 1} از {len(fields)}:\n"
                    f"{fields[next_index]['question']}"
                )
                return True

            save_form_session(
                DATABASE_DIR,
                self.phone,
                user_id=user_id,
                chat_id=chat_id,
                form_id=int(form["id"]),
                current_index=next_index,
                stage="confirming",
                answers=answers,
            )
            summary = self.format_form_summary(form, answers)
            await event.reply(
                f"{summary}\n\n"
                "آیا اطلاعات بالا را تأیید می‌کنید؟\n"
                "تأیید | ویرایش | لغو فرم",
                parse_mode=None,
            )
            return True

    async def handle_form_status_command(self, event):
        status = self.parse_form_status_command(event.raw_text)
        if not status or not getattr(event, "is_reply", False):
            return False
        replied = await event.get_reply_message()
        if not replied:
            return False
        submission = get_form_submission_for_message(
            DATABASE_DIR,
            self.phone,
            message_id=int(replied.id),
            chat_id=int(event.chat_id),
        )
        if not submission:
            return False
        submission = update_form_submission_status(
            DATABASE_DIR,
            self.phone,
            int(submission["id"]),
            status,
        )
        if not submission:
            return False

        summary = str(submission["summary_text"]).strip()
        customer_text = (
            f"{summary}\n\n"
            f"🔖 شماره درخواست: #{int(submission['id'])}\n"
            f"وضعیت: {self.form_status_label(status)}"
        )
        customer_message_id = submission.get("customer_message_id")
        admin_message_id = submission.get("admin_message_id")
        try:
            if customer_message_id:
                await self.client.edit_message(
                    int(submission["chat_id"]),
                    int(customer_message_id),
                    customer_text,
                    parse_mode=None,
                )
        except Exception as exc:
            print(
                f"⚠️ ویرایش پیام فرم مشتری #{submission['id']} ناموفق بود: "
                f"{type(exc).__name__}"
            )
        try:
            if admin_message_id:
                await self.client.edit_message(
                    "me",
                    int(admin_message_id),
                    f"{customer_text}\n\n"
                    f"👤 کاربر: {int(submission['user_id'])}\n"
                    "برای تغییر دوباره وضعیت، روی همین پیام ریپلای کنید.",
                    parse_mode=None,
                )
        except Exception as exc:
            print(
                f"⚠️ ویرایش نسخه ادمین فرم #{submission['id']} ناموفق بود: "
                f"{type(exc).__name__}"
            )
        await self.client.send_message(
            int(submission["chat_id"]),
            f"🔔 وضعیت درخواست #{int(submission['id'])} تغییر کرد:\n"
            f"{self.form_status_label(status)}",
            parse_mode=None,
        )
        await event.delete()
        return True

    async def load_secretary_messages(self):
        """بارگذاری پیام‌های منشی از دیتابیس"""
        try:
            db = os.path.join(DATABASE_DIR, f"bot_data_{self.phone.replace('+', '')}.db")
            conn = db_connect(db, timeout=10)
            cursor = conn.cursor()
            cursor.execute('SELECT pattern, response FROM secretary WHERE is_active = 1')
            results = cursor.fetchall()
            conn.close()
            
            self.secretary_messages = {}
            for pattern, response in results:
                self.secretary_messages[pattern.lower()] = response
            self.secretary_last_reload = time.monotonic()
                
            print(f"✅ {len(self.secretary_messages)} پیام منشی برای {self.phone} بارگذاری شد")
        except Exception as e:
            print(f"خطا در بارگذاری پیام‌های منشی برای {self.phone}: {e}")
    
    async def load_auto_forward_settings(self):
        """بارگذاری تنظیمات فوروارد خودکار"""
        try:
            db = os.path.join(DATABASE_DIR, f"bot_data_{self.phone.replace('+', '')}.db")
            conn = db_connect(db, timeout=10)
            cursor = conn.cursor()
            cursor.execute('SELECT source_channel, target_group FROM auto_forward WHERE is_active = 1')
            results = cursor.fetchall()
            conn.close()
            
            self.auto_forward_settings = {}
            for source, target in results:
                if source not in self.auto_forward_settings:
                    self.auto_forward_settings[source] = []
                self.auto_forward_settings[source].append(target)
                
            print(f"✅ {len(results)} تنظیمات فوروارد برای {self.phone} بارگذاری شد")
        except Exception as e:
            print(f"خطا در بارگذاری تنظیمات فوروارد برای {self.phone}: {e}")
    
    async def send_rich_auto_reply(self, event, response, sender):
        """Send one randomly selected text or media response through the queue."""
        response_type = str(response.get("response_type") or "text")
        variables = {
            "{time}": datetime.now().strftime("%H:%M"),
            "{date}": datetime.now().strftime("%Y/%m/%d"),
            "{name}": str(getattr(sender, "first_name", "") or "کاربر"),
            "{username}": (
                f"@{getattr(sender, 'username', '')}"
                if getattr(sender, "username", None)
                else ""
            ),
            "{id}": str(int(getattr(event, "sender_id", 0) or 0)),
        }

        def render(value):
            rendered = str(value or "")
            for marker, replacement in variables.items():
                rendered = rendered.replace(marker, replacement)
            return rendered

        if response_type == "text":
            await self.queued_send_message(
                event.chat_id,
                render(response.get("content_text")),
                reply_to=int(event.id),
                priority=40,
            )
            return

        media_path = str(response.get("media_path") or "").strip()
        if not media_path or self.feature_engine is None:
            raise FileNotFoundError("مرجع پاسخ چندرسانه‌ای پیدا نشد.")
        media_buffer = await self.feature_engine.advanced._buffer_from_media_reference(
            media_path, default_name="auto-reply-media.bin"
        )
        kwargs = {
            "reply_to": int(event.id),
            "priority": 40,
        }
        caption = render(response.get("caption"))
        if caption and response_type not in {"sticker"}:
            kwargs["caption"] = caption[:1000]
        if response_type == "voice":
            kwargs["voice_note"] = True
        await self.queued_send_file(
            event.chat_id,
            media_buffer,
            **kwargs,
        )

    async def auto_reply_secretary(self):
        """Run private Q&A, forms, fallback secretary, and offline replies."""
        @self.client.on(events.NewMessage(incoming=True))
        async def secretary_handler(event):
            try:
                if event.sender_id == self.owner_id:
                    return
                if (
                    self.feature_engine is not None
                    and (
                        self.feature_engine.friend_affection_was_sent(event)
                        or self.feature_engine.enemy_hostile_was_sent(event)
                    )
                ):
                    return

                sender = await event.get_sender()
                if getattr(sender, "bot", False):
                    return

                js = self.get_data()
                scope = "private" if event.is_private else "group"
                secretary_enabled = js.get("secretary") == "on"
                auto_reply_enabled = js.get("auto_reply") == "on"
                offline_reply_enabled = (
                    js.get("offline_reply_enabled") == "on"
                )
                form_enabled = js.get("form_builder_enabled") == "on"
                typing_enabled = js.get("typing_action") == "on"
                if (
                    auto_reply_enabled
                    and time.monotonic() - self.secretary_last_reload >= 10
                ):
                    await self.load_secretary_messages()

                if (
                    not secretary_enabled
                    and not auto_reply_enabled
                    and not offline_reply_enabled
                    and not form_enabled
                ):
                    return

                message_text = str(event.raw_text or "").strip()
                if not message_text:
                    return

                if typing_enabled:
                    try:
                        duration = max(
                            1,
                            min(60, int(js.get("typing_duration", "5"))),
                        )
                    except (TypeError, ValueError):
                        duration = 5
                    async with self.client.action(event.chat_id, "typing"):
                        await asyncio.sleep(duration)

                # Offline mode deliberately has the highest priority.  When it
                # is enabled the user gets one configured acknowledgement per
                # cooldown window instead of a second Q&A/form response.
                if event.is_private and offline_reply_enabled:
                    try:
                        cooldown_minutes = max(
                            1,
                            min(
                                10080,
                                int(
                                    js.get(
                                        "offline_reply_cooldown_minutes",
                                        "360",
                                    )
                                ),
                            ),
                        )
                    except (TypeError, ValueError):
                        cooldown_minutes = 360
                    last_sent = self.offline_reply_sent_at.get(
                        int(event.sender_id)
                    )
                    now = time.monotonic()
                    if (
                        last_sent is not None
                        and now - last_sent < cooldown_minutes * 60
                    ):
                        return
                    response = str(
                        js.get("offline_reply_text", "")
                        or "در حال حاضر آفلاین هستم؛ پیام شما دریافت شد."
                    )
                    response = response.replace(
                        "{time}",
                        datetime.now().strftime("%H:%M"),
                    ).replace(
                        "{date}",
                        datetime.now().strftime("%Y/%m/%d"),
                    )
                    await self.queued_send_message(
                        event.chat_id,
                        response,
                        reply_to=int(event.id),
                        priority=40,
                    )
                    self.offline_reply_sent_at[int(event.sender_id)] = now
                    return

                # An existing form session or an exact form trigger always
                # wins. Unmatched messages continue to Q&A before the form menu
                # is shown, so FAQ answers and forms can be enabled together.
                if event.is_private and form_enabled and await self.process_form_message(
                    event,
                    message_text,
                ):
                    return

                if auto_reply_enabled:
                    candidates = find_auto_reply_candidates(
                        DATABASE_DIR,
                        self.phone,
                        message_text=message_text,
                        scope=scope,
                    )
                    if candidates:
                        selected_rule = int(candidates[0]["rule_id"])
                        candidates = [
                            item
                            for item in candidates
                            if int(item["rule_id"]) == selected_rule
                        ]
                        cooldown_seconds = max(
                            0,
                            int(candidates[0].get("cooldown_seconds") or 0),
                        )
                        cooldown_key = (
                            int(event.sender_id or 0),
                            selected_rule,
                        )
                        now = time.monotonic()
                        last_sent = self.auto_reply_rule_sent_at.get(
                            cooldown_key
                        )
                        if (
                            last_sent is not None
                            and now - last_sent < cooldown_seconds
                        ):
                            return
                        response = random.choice(candidates)
                        await self.send_rich_auto_reply(event, response, sender)
                        self.auto_reply_rule_sent_at[cooldown_key] = now
                        return
                    if not event.is_private:
                        return
                    response = self.find_secretary_response(message_text)
                    if response:
                        response = response.replace(
                            "{time}",
                            datetime.now().strftime("%H:%M"),
                        )
                        response = response.replace(
                            "{date}",
                            datetime.now().strftime("%Y/%m/%d"),
                        )
                        await self.queued_send_message(
                            event.chat_id,
                            response,
                            reply_to=int(event.id),
                            priority=40,
                        )
                        return

                if not event.is_private:
                    return

                if form_enabled and await self.send_form_menu(event, js):
                    return

                if secretary_enabled:
                    try:
                        cooldown_minutes = max(
                            1,
                            min(
                                10080,
                                int(
                                    js.get(
                                        "secretary_fallback_cooldown_minutes",
                                        "60",
                                    )
                                ),
                            ),
                        )
                    except (TypeError, ValueError):
                        cooldown_minutes = 60
                    now = time.monotonic()
                    last_sent = self.secretary_fallback_sent_at.get(
                        int(event.sender_id)
                    )
                    if (
                        last_sent is not None
                        and now - last_sent < cooldown_minutes * 60
                    ):
                        return
                    response = str(
                        js.get("secretary_fallback_text", "")
                        or "پیام شما دریافت شد."
                    )
                    response = response.replace(
                        "{time}",
                        datetime.now().strftime("%H:%M"),
                    ).replace(
                        "{date}",
                        datetime.now().strftime("%Y/%m/%d"),
                    )
                    await self.queued_send_message(
                        event.chat_id,
                        response,
                        reply_to=int(event.id),
                        priority=40,
                    )
                    self.secretary_fallback_sent_at[int(event.sender_id)] = now

            except Exception as e:
                print(f"خطا در منشی هوشمند برای {self.phone}: {e}")
    
    async def register_settings_handlers(self):
        """ثبت هندلرهای تنظیمات"""
        
        @self.client.on(events.NewMessage(pattern=r'\.(online|typing|secretary|autoreply|autoforward|timename|timebio|save|schedule) (on|off)'))
        async def settings_handler(event):
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                    
                command = event.pattern_match.group(1)
                value = event.pattern_match.group(2)
                setting_key = {
                    "online": "online_status",
                    "typing": "typing_action",
                    "secretary": "secretary",
                    "autoreply": "auto_reply",
                    "autoforward": "auto_forward",
                    "timename": "timename",
                    "timebio": "timebio",
                    "save": "save_timed_photos",
                    "schedule": "scheduled_message_enabled",
                }[command]
                
                js = self.get_data()
                js[setting_key] = value
                self.put_data(js)
                
                if command == "online" and value == "on":
                    await self.set_online_status()
                elif command == "online" and value == "off":
                    self.observed_presence_online = None
                    await self.apply_presence_name_emoji(force=True)
                elif command == "timename" and value == "on":
                    await self.force_time_update()
                    response_msg = "✅ **زمان در نام خانوادگی فعال شد**\n🕒 زمان از الان در نام خانوادگی نمایش داده می‌شود"
                elif command == "timename" and value == "off":
                    await self.force_time_update()
                    response_msg = "✅ **زمان در نام خانوادگی غیرفعال شد**"
                elif command == "timebio" and value == "on":
                    await self.force_time_update()
                    response_msg = "✅ **زمان در بیوگرافی فعال شد**\n🕒 زمان از الان در بیوگرافی نمایش داده می‌شود"
                elif command == "timebio" and value == "off":
                    await self.force_time_update()
                    response_msg = "✅ **زمان در بیوگرافی غیرفعال شد**"
                else:
                    command_names = {
                        "online": "حالت آنلاین",
                        "typing": "اکشن تایپینگ",
                        "secretary": "پاسخ عمومی منشی",
                        "autoreply": "سؤال‌وجواب‌های ثبت‌شده",
                        "autoforward": "فوروارد خودکار",
                        "save": "ذخیره عکس زمان‌دار",
                        "schedule": "ارسال زمان‌بندی‌شده",
                    }
                    response_msg = f"✅ **{command_names.get(command, command)}** `{value}` شد"
                
                await event.reply(response_msg)
                await event.delete()
                
            except Exception as e:
                print(f"خطا در تنظیمات برای {self.phone}: {e}")
                try:
                    await event.reply(f"❌ **خطا در اجرای دستور:** {e}")
                except:
                    pass
        
        @self.client.on(events.NewMessage(pattern=r'\.typing (\d+)'))
        async def typing_duration_handler(event):
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                    
                duration = event.pattern_match.group(1)
                js = self.get_data()
                js["typing_duration"] = duration
                self.put_data(js)
                
                await event.reply(f"✅ **مدت زمان تایپینگ** به `{duration}` ثانیه تنظیم شد")
                await event.delete()
                
            except Exception as e:
                print(f"خطا در تنظیم مدت تایپینگ برای {self.phone}: {e}")

        @self.client.on(
            events.NewMessage(
                outgoing=True,
                pattern=r'^ارسال\s+([۰-۹٠-٩0-9]+)\s+([\s\S]+)$',
            )
        )
        async def persian_same_chat_schedule_handler(event):
            """Configure the one active schedule directly in this chat."""
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                translated = event.pattern_match.group(1).translate(
                    str.maketrans(
                        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                        "01234567890123456789",
                    )
                )
                interval = int(translated)
                message_text = event.pattern_match.group(2).strip()
                if not 1 <= interval <= 10080:
                    await event.reply(
                        "❌ فاصله ارسال باید بین ۱ دقیقه تا ۷ روز باشد."
                    )
                    return
                if not message_text or len(message_text) > 3500:
                    await event.reply(
                        "❌ متن پیام باید بین ۱ تا ۳۵۰۰ نویسه باشد."
                    )
                    return

                settings = self.get_data()
                settings["scheduled_message_interval_minutes"] = str(interval)
                settings["scheduled_message_target"] = str(event.chat_id)
                settings["scheduled_message_text"] = message_text
                settings["scheduled_message_enabled"] = "on"
                self.put_data(settings)
                destination = "همین پیوی" if event.is_private else "همین گروه"
                await event.reply(
                    "✅ ارسال خودکار فعال شد.\n"
                    f"📍 مقصد: {destination}\n"
                    f"⏱ فاصله: هر {interval} دقیقه\n"
                    f"📝 متن: {message_text[:250]}"
                )
                await event.delete()
            except Exception as exc:
                print(
                    f"خطا در دستور فارسی ارسال زمان‌بندی‌شده "
                    f"برای {self.phone}: {exc}"
                )

        @self.client.on(
            events.NewMessage(
                outgoing=True,
                pattern=r'^(?:توقف ارسال|ارسال خاموش)$',
            )
        )
        async def persian_stop_schedule_handler(event):
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                settings = self.get_data()
                settings["scheduled_message_enabled"] = "off"
                self.put_data(settings)
                await event.reply("⏹ ارسال زمان‌بندی‌شده غیرفعال شد.")
                await event.delete()
            except Exception as exc:
                print(
                    f"خطا در توقف ارسال زمان‌بندی‌شده "
                    f"برای {self.phone}: {exc}"
                )

        @self.client.on(events.NewMessage(pattern=r'\.schedule every (\d+)'))
        async def scheduled_interval_handler(event):
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                interval = int(event.pattern_match.group(1))
                if not 1 <= interval <= 10080:
                    await event.reply(
                        "❌ فاصله ارسال باید بین ۱ تا ۱۰۰۸۰ دقیقه باشد."
                    )
                    return
                settings = self.get_data()
                settings["scheduled_message_interval_minutes"] = str(interval)
                self.put_data(settings)
                await event.reply(
                    f"✅ فاصله ارسال زمان‌بندی‌شده روی {interval} دقیقه قرار گرفت."
                )
                await event.delete()
            except Exception as exc:
                print(
                    f"خطا در تنظیم فاصله ارسال زمان‌بندی‌شده "
                    f"برای {self.phone}: {exc}"
                )

        @self.client.on(events.NewMessage(pattern=r'\.schedule target (.+)'))
        async def scheduled_target_handler(event):
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                target = event.pattern_match.group(1).strip()
                valid_username = (
                    target.startswith("@")
                    and target[1:].replace("_", "").isalnum()
                    and 4 <= len(target[1:]) <= 32
                )
                valid_numeric = (
                    target.lstrip("-").isdigit()
                    and 5 <= len(target.lstrip("-")) <= 20
                )
                if not valid_username and not valid_numeric:
                    await event.reply(
                        "❌ مقصد را به‌صورت @username یا آیدی عددی گروه بفرستید."
                    )
                    return
                settings = self.get_data()
                settings["scheduled_message_target"] = target
                self.put_data(settings)
                await event.reply(
                    f"✅ مقصد ارسال زمان‌بندی‌شده روی `{target}` ذخیره شد."
                )
                await event.delete()
            except Exception as exc:
                print(
                    f"خطا در تنظیم مقصد ارسال زمان‌بندی‌شده "
                    f"برای {self.phone}: {exc}"
                )

        @self.client.on(events.NewMessage(pattern=r'\.schedule text (.+)'))
        async def scheduled_text_handler(event):
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                message_text = event.pattern_match.group(1).strip()
                if not message_text or len(message_text) > 3500:
                    await event.reply(
                        "❌ متن پیام باید بین ۱ تا ۳۵۰۰ نویسه باشد."
                    )
                    return
                settings = self.get_data()
                settings["scheduled_message_text"] = message_text
                self.put_data(settings)
                await event.reply("✅ متن پیام زمان‌بندی‌شده ذخیره شد.")
                await event.delete()
            except Exception as exc:
                print(
                    f"خطا در تنظیم متن ارسال زمان‌بندی‌شده "
                    f"برای {self.phone}: {exc}"
                )
        
        @self.client.on(events.NewMessage(pattern=r'\.font ([1-9]|10)'))
        async def font_handler(event):
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                    
                font_num = event.pattern_match.group(1)
                js = self.get_data()
                js["font"] = font_num
                # The legacy command intentionally updates both clocks.
                # They can then be changed independently with «فونت نام» and
                # «فونت بیو» or from the helper panel.
                js["timename_font"] = font_num
                js["timebio_font"] = font_num
                self.put_data(js)
                
                await event.reply(f"✅ **فونت زمان** به شماره `{font_num}` تغییر کرد")
                await event.delete()
                
            except Exception as e:
                print(f"خطا در تغییر فونت برای {self.phone}: {e}")
        
        @self.client.on(events.NewMessage(pattern=r'\.(addcrash|delcrash|addenemy|delenemy) (.*)'))
        async def user_management_handler(event):
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                    
                command = event.pattern_match.group(1)
                user_id_str = event.pattern_match.group(2)
                
                try:
                    user_id = int(user_id_str)
                except ValueError:
                    await event.reply("❌ **لطفاً یک ID معتبر وارد کنید**")
                    return
                    
                js = self.get_data()
                
                if command == "addcrash":
                    if user_id in js.get('crash', []):
                        txt = "✅ **کاربر از قبل در لیست کراش بود**"
                    else:
                        js.setdefault('crash', []).append(user_id)
                        txt = "✅ **کاربر به لیست کراش اضافه شد**"
                        
                elif command == "delcrash":
                    if user_id in js.get('crash', []):
                        js['crash'] = [x for x in js.get('crash', []) if x != user_id]
                        txt = "✅ **کاربر از لیست کراش حذف شد**"
                    else:
                        txt = "❌ **کاربر در لیست کراش نبود**"
                        
                elif command == "addenemy":
                    if user_id in js.get('enemy', []):
                        txt = "✅ **کاربر از قبل در لیست دشمن بود**"
                    else:
                        js.setdefault('enemy', []).append(user_id)
                        txt = "✅ **کاربر به لیست دشمن اضافه شد**"
                        
                elif command == "delenemy":
                    if user_id in js.get('enemy', []):
                        js['enemy'] = [x for x in js.get('enemy', []) if x != user_id]
                        txt = "✅ **کاربر از لیست دشمن حذف شد**"
                    else:
                        txt = "❌ **کاربر در لیست دشمن نبود**"
                
                self.put_data(js)
                await event.reply(txt)
                await event.delete()
                
            except Exception as e:
                print(f"خطا در مدیریت کاربران برای {self.phone}: {e}")
        
        @self.client.on(events.NewMessage(pattern=r'\.clean (\d+)'))
        async def clean_handler(event):
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                    
                count = int(event.pattern_match.group(1))
                message_id = event.message.id
                deleted = 0
                
                for i in range(count):
                    try:
                        await self.client.delete_messages(event.chat_id, message_id - i)
                        deleted += 1
                    except:
                        pass
                        
                await event.reply(f"✅ **{deleted}** پیام پاک شد")
                
            except Exception as e:
                print(f"خطا در دستور clean برای {self.phone}: {e}")
        
        @self.client.on(events.NewMessage(pattern=r'\.addreply (.+)\|(.+)'))
        async def add_reply_handler(event):
            try:
                if not self.is_owner_outgoing_event(event):
                    return
                    
                pattern = event.pattern_match.group(1).strip().lower()
                response = event.pattern_match.group(2).strip()
                
                db = os.path.join(DATABASE_DIR, f"bot_data_{self.phone.replace('+', '')}.db")
                conn = db_connect(db, timeout=10)
                cursor = conn.cursor()
                cursor.execute('INSERT INTO secretary (pattern, response) VALUES (?, ?)', (pattern, response))
                cursor.execute(
                    """INSERT INTO settings (key, value)
                       VALUES ('auto_reply', 'on')
                       ON CONFLICT(key) DO UPDATE SET value = 'on'"""
                )
                conn.commit()
                conn.close()
                
                self.secretary_messages[pattern] = response
                await event.reply(
                    f"✅ **پاسخ جدید افزوده و پاسخ خودکار فعال شد:**\n"
                    f"**الگو:** `{pattern}`\n**پاسخ:** `{response}`"
                )
                await event.delete()
                
            except Exception as e:
                print(f"خطا در افزودن پاسخ برای {self.phone}: {e}")
    
    async def force_time_update(self):
        """اجبار به به‌روزرسانی فوری زمان"""
        try:
            self.last_time_update = 0
            await self.update_profile_time()
        except Exception as e:
            print(f"خطا در به‌روزرسانی فوری زمان برای {self.phone}: {e}")
    
    async def update_profile_time(self):
        """Apply or restore the name/bio clock once without blocking commands."""
        js = self.get_data()
        current_time = time.time()
        clock_enabled = (
            js.get("timename") == "on" or js.get("timebio") == "on"
        )
        restore_needed = (
            js.get("timename_applied") == "on"
            or js.get("timebio_applied") == "on"
        )
        if not clock_enabled and not restore_needed:
            return
        if (
            clock_enabled
            and current_time - self.last_time_update < 55
        ):
            return

        try:
            full = await self.client(GetFullUserRequest("me"))
            users = list(getattr(full, "users", None) or [])
            me = users[0] if users else await self.client.get_me()
            current_bio = str(
                getattr(getattr(full, "full_user", None), "about", "") or ""
            )
        except Exception:
            me = await self.client.get_me()
            current_bio = str(js.get("original_bio", "") or "")

        current_last_name = str(getattr(me, "last_name", "") or "")
        tz = pytz.timezone("Asia/Tehran")
        plain_time = datetime.now(tz).strftime("%H:%M")

        def formatted_time(font_key):
            try:
                index = int(
                    js.get(font_key, js.get("font", "1"))
                ) - 1
            except (TypeError, ValueError):
                index = 0
            if not 0 <= index < len(self.fonts):
                return plain_time
            try:
                return plain_time.translate(
                    str.maketrans("0123456789", self.fonts[index])
                )
            except (TypeError, ValueError):
                return plain_time

        def normalize_clock_text(value):
            normalized = str(value or "")
            for glyphs in self.fonts:
                try:
                    normalized = normalized.translate(
                        str.maketrans(glyphs, "0123456789")
                    )
                except (TypeError, ValueError):
                    continue
            return normalized

        def is_clock_token(value):
            return bool(
                re.fullmatch(
                    r"(?:[01]?\d|2[0-3]):[0-5]\d",
                    normalize_clock_text(str(value or "").strip()),
                )
            )

        updates = {}
        changed_settings = {}

        if js.get("timename") == "on":
            if js.get("timename_applied") != "on":
                original_last_name = current_last_name
                if is_clock_token(original_last_name):
                    original_last_name = str(
                        js.get("original_last_name", "") or ""
                    )
                changed_settings["original_last_name"] = original_last_name
                changed_settings["timename_applied"] = "on"
                js["original_last_name"] = original_last_name
            desired_last_name = formatted_time("timename_font")
            if current_last_name != desired_last_name:
                updates["last_name"] = desired_last_name
        elif js.get("timename_applied") == "on":
            desired_last_name = str(js.get("original_last_name", "") or "")
            if current_last_name != desired_last_name:
                updates["last_name"] = desired_last_name
            changed_settings["timename_applied"] = "off"

        if js.get("timebio") == "on":
            if js.get("timebio_applied") != "on":
                original_bio = current_bio
                parts = original_bio.rsplit(" ", 1)
                if parts and is_clock_token(parts[-1]):
                    original_bio = parts[0] if len(parts) == 2 else ""
                changed_settings["original_bio"] = original_bio
                changed_settings["timebio_applied"] = "on"
                js["original_bio"] = original_bio
            desired_bio = (
                f"{str(js.get('original_bio', '') or '').strip()} "
                f"{formatted_time('timebio_font')}"
            ).strip()
            if current_bio != desired_bio:
                updates["about"] = desired_bio
        elif js.get("timebio_applied") == "on":
            desired_bio = str(js.get("original_bio", "") or "")
            if current_bio != desired_bio:
                updates["about"] = desired_bio
            changed_settings["timebio_applied"] = "off"

        if changed_settings:
            for key, value in changed_settings.items():
                set_self_setting(DATABASE_DIR, self.phone, key, value)
        if updates:
            operation = lambda: self.client(
                functions.account.UpdateProfileRequest(**updates)
            )
            if self.send_queue is not None:
                await self.send_queue.execute(
                    operation,
                    description="update_profile_clock",
                    priority=10,
                )
            else:
                await operation()
            print(
                f"✅ ساعت پروفایل {self.phone} بروزرسانی شد: "
                f"{', '.join(updates)}"
            )
        self.last_time_update = current_time
    
    def get_data(self):
        """خواندن داده‌ها از دیتابیس"""
        try:
            db = os.path.join(DATABASE_DIR, f"bot_data_{self.phone.replace('+', '')}.db")
            conn = db_connect(db, timeout=10)
            cur = conn.cursor()
            cur.execute('SELECT key, value FROM settings')
            settings = {k: v for k, v in cur.fetchall()}
            cur.execute('SELECT user_id FROM crash')
            settings['crash'] = [r[0] for r in cur.fetchall()]
            cur.execute('SELECT user_id FROM enemy')
            settings['enemy'] = [r[0] for r in cur.fetchall()]
            conn.close()
            return settings
        except Exception as e:
            print(f"خطا در خواندن داده‌ها برای {self.phone}: {e}")
            return {}
    
    def put_data(self, data):
        """نوشتن داده‌ها به دیتابیس"""
        try:
            db = os.path.join(DATABASE_DIR, f"bot_data_{self.phone.replace('+', '')}.db")
            conn = db_connect(db, timeout=10)
            cur = conn.cursor()
            for k, v in data.items():
                if k not in ['crash', 'enemy']:
                    cur.execute('INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)', (k, v))
            if 'crash' in data:
                cur.execute('DELETE FROM crash')
                cur.executemany('INSERT INTO crash(user_id) VALUES (?)', [(u,) for u in data['crash']])
            if 'enemy' in data:
                cur.execute('DELETE FROM enemy')
                cur.executemany('INSERT INTO enemy(user_id) VALUES (?)', [(u,) for u in data['enemy']])
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"خطا در نوشتن داده‌ها برای {self.phone}: {e}")
    
    async def check_expiration(self):
        """بررسی انقضای اکانت"""
        while self.is_running and not self.shutdown_requested:
            if not self.is_self_valid():
                print(f"❌ اکانت {self.phone} منقضی شده است. توقف...")
                await send_to_admin(self.client, f"❌ اکانت {self.phone} منقضی شده است", self.phone)
                self.mark_controller_stopped(
                    status="expired",
                    detail="اعتبار سلف منقضی شده است.",
                )
                await self.client.disconnect()
                break
            await asyncio.sleep(60)
    
    def is_self_valid(self):
        """Validate subscription with ISO-aware parsing and fail closed."""
        try:
            if not USERS_DB.is_file():
                print(f"❌ دیتابیس کاربران برای {self.phone} پیدا نشد")
                return False
            with db_connect(USERS_DB, timeout=10) as conn:
                conn.execute("PRAGMA busy_timeout = 10000")
                result = conn.execute(
                    """SELECT expiration_date, is_active, self_enabled
                       FROM users WHERE phone = ? LIMIT 1""",
                    (self.phone,),
                ).fetchone()
            if not result:
                print(f"❌ رکورد مالک شماره {self.phone} پیدا نشد")
                return False
            expiration_text, is_active, self_enabled = result
            if not bool(is_active) or not bool(self_enabled):
                return False
            if not expiration_text:
                return True
            parsed = datetime.fromisoformat(
                str(expiration_text).strip().replace("Z", "+00:00")
            )
            if parsed.tzinfo is not None:
                now = datetime.now(timezone.utc)
                parsed = parsed.astimezone(timezone.utc)
            else:
                now = datetime.now()
            return now < parsed
        except (TypeError, ValueError) as exc:
            print(f"❌ تاریخ انقضای نامعتبر برای {self.phone}: {exc}")
            return False
        except sqlite3.Error as exc:
            print(f"❌ خطای دیتابیس در بررسی انقضا برای {self.phone}: {exc}")
            return False
    
    async def run(self):
        """اجرای اکانت"""
        try:
            success = await self.robust_initialize()
            if success:
                write_runtime_status(self.status_file, "ready")
                print(f"🚀 اکانت {self.phone} در حال اجرا است...")
                await self.client.run_until_disconnected()
            else:
                write_runtime_status(
                    self.status_file,
                    "failed",
                    self.last_startup_error
                    or "راه‌اندازی کلاینت تلگرام ناموفق بود",
                )
                print(f"❌ اکانت {self.phone} راه‌اندازی نشد")
        except Exception as e:
            write_runtime_status(self.status_file, "failed", e)
            print(f"❌ خطا در اجرای اکانت {self.phone}: {e}")
        finally:
            self.shutdown_requested = True
            self.is_running = False
            await self.stop_background_tasks()
            if self.feature_engine is not None:
                try:
                    await self.feature_engine.stop_background_tasks()
                except Exception:
                    pass
            if self.send_queue is not None:
                await self.send_queue.close()
            if self.client is not None:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass

async def create_session_file(phone, session_file):
    """ایجاد فایل سشن جدید"""
    try:
        print(f"📱 ایجاد سشن جدید برای {phone}...")
        
        client = TelegramClient(StringSession(), API_ID, API_HASH,
                              device_model="iPhone 15 Pro",
                              system_version="iOS 17.1",
                              app_version="10.0.0")
        
        await client.connect()
        
        await client.send_code_request(phone)
        print(f"✅ کد تأیید برای {phone} ارسال شد")
        
        code = input(f"📝 لطفاً کد تأیید ارسال شده برای {phone} را وارد کنید: ").strip()
        
        try:
            await client.sign_in(phone, code)
            print(f"✅ لاگین موفقیت‌آمیز برای {phone}")
        except SessionPasswordNeededError:
            password = input("🔐 لطفاً رمز دو مرحله‌ای را وارد کنید: ")
            await client.sign_in(password=password)
            print(f"✅ لاگین با رمز دو مرحله‌ای موفقیت‌آمیز برای {phone}")
        
        session_string = client.session.save()
        session_path = Path(session_file)
        write_session_file(session_path, DATABASE_DIR, session_string)
        
        print(f"✅ سشن برای {phone} در {session_file} ذخیره شد")
        await client.disconnect()
        return session_string
        
    except Exception as e:
        print(f"❌ خطا در ایجاد سشن برای {phone}: {e}")
        return None

def parse_arguments():
    parser = argparse.ArgumentParser(description="Telegram self-bot launcher")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--multi",
        action="store_true",
        help="اجرای تمام حساب‌های فعال دیتابیس",
    )
    mode.add_argument(
        "--create",
        action="store_true",
        help="ساخت سشن جدید به‌صورت تعاملی",
    )
    parser.add_argument("--phone", help="شماره تلفن حساب")
    parser.add_argument(
        "--session-file",
        "--session",
        dest="session_file",
        help="مسیر فایل StringSession",
    )
    parser.add_argument(
        "--status-file",
        help="فایل وضعیت مورد استفاده ربات مدیریت",
    )
    parser.add_argument("--api-id", type=int, help="API ID اختیاری")
    parser.add_argument("--api-hash", help="API HASH اختیاری")
    return parser.parse_args()


async def main():
    """راه‌اندازی تک‌حساب، چندحساب یا ساخت سشن."""
    global API_ID, API_HASH

    args = parse_arguments()
    if args.api_id:
        API_ID = args.api_id
    if args.api_hash:
        API_HASH = args.api_hash.strip()

    if not API_ID or not API_HASH:
        raise RuntimeError(
            "TELEGRAM_API_ID و TELEGRAM_API_HASH باید در .env یا آرگومان‌ها تنظیم شوند."
        )

    account_manager = AccountManager()

    if args.create:
        if not args.phone or not args.session_file:
            raise ValueError(
                "برای --create باید --phone و --session-file وارد شوند."
            )

        session_string = await create_session_file(
            args.phone,
            args.session_file,
        )
        if not session_string:
            raise RuntimeError("ساخت سشن ناموفق بود.")

        print(f"✅ سشن رمزنگاری‌شده برای {args.phone} ساخته شد")
        return

    if args.multi:
        print("🔧 راه‌اندازی حالت چند اکانته...")
        accounts = account_manager.get_all_accounts()

        if not accounts:
            raise RuntimeError("هیچ اکانت فعالی در دیتابیس یافت نشد.")

        print(f"✅ تعداد {len(accounts)} اکانت برای راه‌اندازی یافت شد")
        tasks = []
        for phone, session_string in accounts:
            print(f"🔄 راه‌اندازی اکانت {phone}...")
            account = TelegramAccount(phone, session_string, account_manager)
            tasks.append(asyncio.create_task(account.run()))
            await asyncio.sleep(3)

        print("🚀 تمام اکانت‌ها در حال اجرا هستند...")
        await asyncio.gather(*tasks, return_exceptions=True)
        return

    if not args.phone or not args.session_file:
        raise ValueError(
            "برای اجرای تک‌حساب باید --phone و --session-file وارد شوند."
        )

    session_path = Path(args.session_file)
    if not session_path.is_file():
        write_runtime_status(
            args.status_file,
            "failed",
            f"فایل سشن پیدا نشد: {session_path}",
        )
        raise FileNotFoundError(f"فایل سشن پیدا نشد: {session_path}")

    session_string = read_session_file(
        session_path,
        DATABASE_DIR,
        migrate_plaintext=True,
    )
    if not session_string:
        write_runtime_status(args.status_file, "failed", "فایل سشن خالی است")
        raise ValueError("فایل سشن خالی است.")

    print(f"🔄 راه‌اندازی اکانت {args.phone}...")
    account = TelegramAccount(
        args.phone,
        session_string,
        account_manager,
        status_file=args.status_file,
    )
    await account.run()

if __name__ == '__main__':
    try:
        powered_by_label = TelegramAccount.brand_username(
            "brand_powered_by"
        )
        print(f"""
┌────────────────────
│  🚀 **Sᴇʟғ Bᴏᴛ Sᴛᴀʀᴛᴇᴅ**  
│  🔮 **𝑷𝒐𝒘𝒆𝒓𝒆𝒅 𝒃𝒚:** {powered_by_label}
└─────────────────────
        """)
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹ **برنامه توسط کاربر متوقف شد**")
    except Exception as e:
        print(f"❌ **خطای غیرمنتظره:** {e}")

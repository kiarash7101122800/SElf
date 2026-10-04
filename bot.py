"""
SElf - Optimized SelfBot for Railway
Version 3.0 - Production Ready

Features:
- Env based config (API_ID, API_HASH, OWNER_ID, SESSION_STRING, BOT_TOKEN)
- Async safe (no time.sleep blocking)
- Robust data folder creation
- Auto backup profile
- Logging
- Railway volume compatible
"""
import asyncio
import os
import sys
import json
import random
import shutil
import logging
import unicodedata
from datetime import datetime
from pathlib import Path

# Load env first
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

# Core imports
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram.errors import PhotoCropSizeSmall
from pyrogram import Client, filters, enums
import pyrogram
import requests
import pytz
import importlib
import reloads

# Optional pytube
try:
    from pytube import YouTube
    HAS_PYTUBE = True
except:
    HAS_PYTUBE = False

# ---------- LOGGING ----------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/bot.log", encoding="utf-8") if os.path.exists("logs") or True else logging.StreamHandler()
    ]
)
# Ensure logs dir for file handler after config
os.makedirs("logs", exist_ok=True)
# Re-add file handler if needed
logger = logging.getLogger("SElf")
if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
    fh = logging.FileHandler("logs/bot.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    logger.addHandler(fh)

# ---------- ENV CONFIG ----------
API_ID = os.getenv("API_ID") or os.getenv("APP_ID") or ""
API_HASH = os.getenv("API_HASH") or os.getenv("API_HASH") or ""
OWNER_ID_ENV = os.getenv("OWNER_ID") or os.getenv("ADMIN_ID") or ""
SESSION_STRING = os.getenv("SESSION_STRING") or ""
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
SESSION_NAME = os.getenv("SESSION_NAME") or "my_account"

# Parse API_ID
try:
    api_id_int = int(API_ID) if API_ID else 0
except:
    api_id_int = 0

if not api_id_int or not API_HASH:
    logger.warning("⚠️ API_ID or API_HASH not set! Please set env vars. Trying to continue for dashboard test...")

# Parse OWNER_ID
admin = "me"
admin_id_int = None
if OWNER_ID_ENV:
    try:
        # Support numeric ID
        if OWNER_ID_ENV.isdigit() or (OWNER_ID_ENV.lstrip('-').isdigit()):
            admin_id_int = int(OWNER_ID_ENV)
            admin = admin_id_int
        else:
            # If it's username or 'me'
            admin = OWNER_ID_ENV
    except:
        admin = "me"

logger.info(f"Config -> API_ID: {api_id_int} | ADMIN: {admin} | SESSION: {SESSION_NAME} | HAS_SESSION_STRING: {bool(SESSION_STRING)}")

# ---------- CLIENT INIT ----------
# Railway compatible session handling
# If SESSION_STRING provided, use it. Otherwise use file session in data folder for persistence

session_file_path = SESSION_NAME
# Prefer data/ folder for persistence on Railway volume
if not os.path.isabs(session_file_path):
    # If file exists in root, use root, else use data/ for new
    if os.path.exists(f"{session_file_path}.session"):
        client_session = session_file_path
    elif os.path.exists(f"data/{session_file_path}.session"):
        client_session = f"data/{session_file_path}"
    else:
        # Default to data/ folder for Railway persistence
        os.makedirs("data", exist_ok=True)
        client_session = f"data/{session_file_path}"
else:
    client_session = session_file_path

client_kwargs = dict(
    name=client_session,
    api_id=api_id_int if api_id_int else 12345,
    api_hash=API_HASH if API_HASH else "0123456789abcdef0123456789abcdef",
    workdir=".",
)

if SESSION_STRING:
    client_kwargs["session_string"] = SESSION_STRING
    logger.info("Using SESSION_STRING from env")

if BOT_TOKEN:
    client_kwargs["bot_token"] = BOT_TOKEN
    logger.info("BOT_TOKEN detected - running as Bot (some self features will fail)")

# Remove dummy if real credentials missing - will still create client but will fail on connect (expected for dashboard testing)
bot = Client(**client_kwargs)

# ---------- FONTS ----------
fonts = {
    'Font1': {'0': '𝟎','1': '𝟏','2': '𝟐','3': '𝟑','4': '𝟒','5': '𝟓','6': '𝟔','7': '𝟕','8': '𝟖','9': '𝟗'},
    'Font2': {'0': '𝟘','1': '𝟙','2': '𝟚','3': '𝟛','4': '𝟜','5': '𝟝','6': '𝟞','7': '𝟟','8': '𝟠','9': '𝟡'},
    'Font3': {'0': '⓪','1': '①','2': '②','3': '③','4': '④','5': '⑤','6': '⑥','7': '⑦','8': '⑧','9': '⑨'},
    'Font4': {'0': '⁰','1': '¹','2': '²','3': '³','4': '⁴','5': '⁵','6': '⁶','7': '⁷','8': '⁸','9': '⁹'},
}

FoshList = [
    'به نظرم بهتره مودب باشیم دوست عزیز',
]

# ---------- DATA ENSURE ----------
def ensure_data_dirs():
    """Ensure all required data directories and files exist - Railway safe"""
    try:
        os.makedirs("data", exist_ok=True)
        os.makedirs("data/action", exist_ok=True)
        os.makedirs("downloads", exist_ok=True)
        os.makedirs("logs", exist_ok=True)

        defaults = {
            "data/TimeName.txt": "off",
            "data/TimeBio.txt": "off",
            "data/Font.txt": "Font1",
            "data/italic.txt": "off",
            "data/part.txt": "off",
            "data/bold.txt": "off",
            "data/link.txt": "off",
            "data/underline.txt": "off",
            "data/Enemy.txt": "",
            "data/Mute.txt": "",
            "data/action/playing.txt": "off",
            "data/action/typing.txt": "off",
            "data/action/RECORD_VIDEO.txt": "off",
            "data/action/CHOOSE_STICKER.txt": "off",
            "data/action/UPLOAD_VIDEO.txt": "off",
            "data/action/UPLOAD_DOCUMENT.txt": "off",
            "data/action/UPLOAD_AUDIO.txt": "off",
            "data/action/SPEAKING.txt": "off",
        }

        for path, default_content in defaults.items():
            if not os.path.exists(path):
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(default_content)
                    logger.info(f"Created default {path}")
                except Exception as e:
                    logger.error(f"Failed to create {path}: {e}")

        # Ensure admin backup dir exists (will be filled on first run)
        admin_dir = f"data/{admin}" if isinstance(admin, int) or (isinstance(admin, str) and admin != "me" and admin.isdigit()) else "data/me_backup"
        os.makedirs(admin_dir, exist_ok=True)

        return True
    except Exception as e:
        logger.error(f"ensure_data_dirs error: {e}")
        return False

ensure_data_dirs()

def safe_read(path, default="off"):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip() or default
    except:
        return default

def safe_write(path, content):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(content))
        return True
    except Exception as e:
        logger.error(f"safe_write {path} error: {e}")
        return False

# ---------- SCHEDULER JOBS ----------
async def TimeName():
    try:
        if safe_read("data/TimeName.txt") != "on":
            return
        tz = pytz.timezone("Asia/Tehran")
        now = datetime.now(tz)
        if now.strftime("%S") != "00":
            return
        number = now.strftime("%H:%M")
        FONT = safe_read("data/Font.txt", "Font1")
        try:
            if FONT == "Random":
                selected_font = random.choice(list(fonts.keys()))
                converted_time = ''.join([fonts[selected_font].get(char, char) for char in number])
                await bot.update_profile(last_name=converted_time)
            else:
                if FONT in fonts:
                    number_unicode = ''.join([fonts[FONT].get(c, c) for c in str(number)])
                    await bot.update_profile(last_name=number_unicode)
        except Exception as e:
            logger.debug(f"TimeName update error: {e}")
    except Exception as e:
        logger.error(f"TimeName job error: {e}")

async def TimeBio():
    try:
        if safe_read("data/TimeBio.txt") != "on":
            return
        tz = pytz.timezone("Asia/Tehran")
        now = datetime.now(tz)
        if now.strftime("%S") != "00":
            return
        number = now.strftime("%H:%M")
        FONT = safe_read("data/Font.txt", "Font1")
        try:
            if FONT == "Random":
                selected_font = random.choice(list(fonts.keys()))
                converted_time = ''.join([fonts[selected_font].get(char, char) for char in number])
                await bot.update_profile(bio="Time Now : " + converted_time)
            else:
                if FONT in fonts:
                    number_unicode = ''.join([fonts[FONT].get(c, c) for c in str(number)])
                    await bot.update_profile(bio="Time Now : " + number_unicode)
        except Exception as e:
            logger.debug(f"TimeBio update error: {e}")
    except Exception as e:
        logger.error(f"TimeBio job error: {e}")

scheduler = AsyncIOScheduler()
scheduler.add_job(TimeName, "interval", seconds=1, max_instances=1, coalesce=True)
scheduler.add_job(TimeBio, "interval", seconds=1, max_instances=1, coalesce=True)

# ---------- HANDLERS ----------
@bot.on_message(pyrogram.filters.photo)
async def onphoto(client, message):
    try:
        # Only if self? Actually any timed photo
        if message.photo and getattr(message.photo, 'ttl_seconds', None):
            rand = random.randint(1000, 9999999)
            os.makedirs("downloads", exist_ok=True)
            local = f"downloads/photo-{rand}.png"
            file_path = await bot.download_media(message=message, file_name=local)
            caption = f"🔥 New timed image {message.photo.date} | time: {message.photo.ttl_seconds}s"
            try:
                # admin can be int or 'me'
                target = admin if admin != "me" else "me"
                await bot.send_photo(chat_id=target, photo=file_path or local, caption=caption)
            except Exception as e:
                logger.error(f"Failed to forward timed photo: {e}")
            try:
                if os.path.exists(local):
                    os.remove(local)
                if file_path and os.path.exists(file_path) and file_path != local:
                    os.remove(file_path)
            except:
                pass
    except Exception as e:
        logger.debug(f"onphoto error: {e}")

@bot.on_message(pyrogram.filters.video)
async def onvideo(client, message):
    try:
        if message.video and getattr(message.video, 'ttl_seconds', None):
            rand = random.randint(1000, 9999999)
            os.makedirs("downloads", exist_ok=True)
            local = f"downloads/video-{rand}.mp4"
            file_path = await bot.download_media(message=message, file_name=local)
            caption = f"🔥 New timed video {message.video.date} | time: {message.video.ttl_seconds}s"
            try:
                target = admin if admin != "me" else "me"
                await bot.send_video(chat_id=target, video=file_path or local, caption=caption)
            except Exception as e:
                logger.error(f"Failed to forward timed video: {e}")
            try:
                if os.path.exists(local):
                    os.remove(local)
                if file_path and os.path.exists(file_path) and file_path != local:
                    os.remove(file_path)
            except:
                pass
    except Exception as e:
        logger.debug(f"onvideo error: {e}")

# Determine filter for owner
if isinstance(admin, int):
    owner_filter = filters.user(admin)
else:
    # 'me' filter
    owner_filter = filters.me

@bot.on_message(owner_filter)
async def admins(client, message):
    try:
        text = message.text or ""
        from_id = message.chat.id

        # Ensure admin backup dir
        admin_backup_dir = f"data/{admin}" if isinstance(admin, int) else "data/me_backup"
        if not os.path.isdir(admin_backup_dir):
            os.makedirs(admin_backup_dir, exist_ok=True)
            try:
                me = await bot.get_me()
                # Try to get bio
                try:
                    full = await bot.invoke(pyrogram.raw.functions.users.GetFullUser(id=await bot.resolve_peer("me")))
                    bio_text = full.full_user.about or ""
                except:
                    bio_text = ""
                name_text = me.first_name or "SElf"

                with open(f"{admin_backup_dir}/bio.txt", "w", encoding="utf-8") as f:
                    f.write(bio_text)
                with open(f"{admin_backup_dir}/name.txt", "w", encoding="utf-8") as f:
                    f.write(name_text)

                # Try download profile photo
                if me.photo:
                    try:
                        local = f"{admin_backup_dir}/profile.png"
                        await bot.download_media(message=me.photo.big_file_id, file_name=local)
                    except:
                        pass
            except Exception as e:
                logger.debug(f"admin backup init error: {e}")

        # Simple commands - using safe_write
        # TimeName
        if text == "TimeName on":
            safe_write("data/TimeName.txt", "on")
            await message.edit_text('✅ TimeName is on')
            return
        if text == "TimeName off":
            safe_write("data/TimeName.txt", "off")
            await message.edit_text('❌ TimeName is off')
            return
        if text == "TimeBio on":
            safe_write("data/TimeBio.txt", "on")
            await message.edit_text('✅ TimeBio is on')
            return
        if text == "TimeBio off":
            safe_write("data/TimeBio.txt", "off")
            await message.edit_text('❌ TimeBio is off')
            return

        # Text styles
        if text == "italic on":
            safe_write("data/italic.txt", "on")
            await message.edit_text('✅ italic is on')
            return
        if text == "italic off":
            safe_write("data/italic.txt", "off")
            await message.edit_text('❌ italic is off')
            return
        if text == "part on":
            safe_write("data/part.txt", "on")
            await message.edit_text('✅ part is on')
            return
        if text == "part off":
            safe_write("data/part.txt", "off")
            await message.edit_text('❌ part is off')
            return
        if text == "bold on":
            safe_write("data/bold.txt", "on")
            await message.edit_text('✅ bold is on')
            return
        if text == "bold off":
            safe_write("data/bold.txt", "off")
            await message.edit_text('❌ bold is off')
            return
        if text == "link on":
            safe_write("data/link.txt", "on")
            await message.edit_text('✅ link is on')
            return
        if text == "link off":
            safe_write("data/link.txt", "off")
            await message.edit_text('❌ link is off')
            return
        if text == "underline on":
            safe_write("data/underline.txt", "on")
            await message.edit_text('✅ underline is on')
            return
        if text == "underline off":
            safe_write("data/underline.txt", "off")
            await message.edit_text('❌ underline is off')
            return

        # Actions
        action_map = {
            "playing": "playing.txt",
            "typing": "typing.txt",
            "RECORD_VIDEO": "RECORD_VIDEO.txt",
            "CHOOSE_STICKER": "CHOOSE_STICKER.txt",
            "UPLOAD_VIDEO": "UPLOAD_VIDEO.txt",
            "UPLOAD_DOCUMENT": "UPLOAD_DOCUMENT.txt",
            "UPLOAD_AUDIO": "UPLOAD_AUDIO.txt",
            "SPEAKING": "SPEAKING.txt",
        }
        for key, fname in action_map.items():
            if text == f"{key} on":
                safe_write(f"data/action/{fname}", "on")
                await message.edit_text(f'✅ {key} action is on')
                return
            if text == f"{key} off":
                safe_write(f"data/action/{fname}", "off")
                await message.edit_text(f'❌ {key} action is off')
                return

        # SetFont
        if text.startswith("SetFont "):
            try:
                arg = text.split("SetFont ")[1].strip()
                mapping = {"1": "Font1", "2": "Font2", "3": "Font3", "4": "Font4", "Random": "Random"}
                if arg in mapping:
                    safe_write("data/Font.txt", mapping[arg])
                    await message.edit_text(f'✅ The {mapping[arg]} is Seted')
                else:
                    await message.edit_text('❌ Font not found. Use 1,2,3,4,Random')
                return
            except Exception as e:
                logger.debug(f"SetFont error: {e}")

        # Fun animations - optimized with asyncio.sleep
        if text == "مربع":
            frames = []
            # Generate animation frames quickly
            base = [["◼️"]*5 for _ in range(5)]
            # Simulate snake fill
            msg_text = ""
            for r in range(5):
                for c in range(5):
                    # Build current frame
                    out = ""
                    for rr in range(5):
                        for cc in range(5):
                            if rr < r or (rr == r and cc <= c):
                                out += "◻️"
                            else:
                                out += "◼️"
                        out += "\n"
                    try:
                        await message.edit_text(out)
                        await asyncio.sleep(0.15)
                    except:
                        pass
            await message.edit_text("تمام")
            return

        if text == "قلب":
            hearts = ["❤️","🧡","💛","💚","💙","💜","🖤","🤎","❤️‍🔥","❤️‍🩹","❣️","💓","💗"]
            for _ in range(2):
                for h in hearts:
                    try:
                        await message.edit_text(h)
                        await asyncio.sleep(0.2)
                    except:
                        pass
            return

        if text in ("bot", "ربات"):
            await bot.send_message(chat_id=message.chat.id, text="✅ Self is on - Railway Optimized v3", reply_to_message_id=message.id)
            return

        if text == "Block":
            try:
                if message.reply_to_message:
                    await bot.block_user(user_id=message.reply_to_message.from_user.id)
                    await message.edit_text("✅ User Blocked")
                else:
                    await bot.block_user(user_id=message.chat.id)
                    await message.edit_text("✅ User Blocked")
            except Exception as e:
                await message.edit_text(f"❌ Block failed: {e}")
            return

        if text == "UnBlock":
            try:
                if message.reply_to_message:
                    await bot.unblock_user(user_id=message.reply_to_message.from_user.id)
                    await message.edit_text("✅ User UnBlocked")
                else:
                    await bot.unblock_user(user_id=message.chat.id)
                    await message.edit_text("✅ User UnBlocked")
            except Exception as e:
                await message.edit_text(f"❌ UnBlock failed: {e}")
            return

        if text.startswith("ویس "):
            try:
                t = text.split("ویس ", 1)[1]
                url = f"https://haji-api.ir/text-to-voice/?text={t}&Character=DilaraNeural"
                resp = requests.get(url, timeout=15)
                if resp.status_code == 200:
                    try:
                        data = json.loads(resp.content)
                        voice_url = data['results']['url']
                        await bot.send_voice(chat_id=message.chat.id, voice=voice_url, reply_to_message_id=message.id)
                        await message.delete()
                    except:
                        await bot.send_message(chat_id=message.chat.id, text="خطا در دیکد وب سرویس", reply_to_message_id=message.id)
                else:
                    await bot.send_message(chat_id=message.chat.id, text="خطا در اتصال به وب سرویس", reply_to_message_id=message.id)
            except Exception as e:
                logger.error(f"TTS error: {e}")
            return

        if text == "SetName":
            try:
                if message.reply_to_message and message.reply_to_message.text:
                    names = message.reply_to_message.text
                    await bot.update_profile(first_name=names)
                    await message.edit_text(f"✅ The Name : [ {names} ] is Seted")
                else:
                    await message.edit_text("❌ Reply to a text")
            except Exception as e:
                await message.edit_text(f"❌ Error: {e}")
            return

        if text == "SetBio":
            try:
                if message.reply_to_message and message.reply_to_message.text:
                    bios = message.reply_to_message.text
                    await bot.update_profile(bio=bios)
                    await message.edit_text(f"✅ The Bio : [ {bios} ] is Seted")
                else:
                    await message.edit_text("❌ Reply to a text")
            except Exception as e:
                await message.edit_text(f"❌ Error: {e}")
            return

        if text == "SetProfile":
            try:
                pm = message.reply_to_message
                if not pm:
                    await message.edit_text("❌ Reply to photo/video")
                    return
                if pm.photo:
                    await message.edit_text("⏳ Whate . . .")
                    try:
                        rand = random.randint(1000, 9999999)
                        os.makedirs("downloads", exist_ok=True)
                        local = f"downloads/photo-{rand}.jpg"
                        path = await bot.download_media(message=pm, file_name=local)
                        await bot.set_profile_photo(photo=path or local)
                        await message.edit_text("✅ Photo Is Seted")
                        if os.path.exists(path or local):
                            os.remove(path or local)
                    except PhotoCropSizeSmall:
                        await message.edit_text("❌ Photo Is Small")
                    except Exception as e:
                        await message.edit_text(f"❌ Error: {e}")
                elif pm.video:
                    await message.edit_text("⏳ Whate . . .")
                    try:
                        rand = random.randint(1000, 9999999)
                        os.makedirs("downloads", exist_ok=True)
                        local = f"downloads/Video-{rand}.mp4"
                        path = await bot.download_media(message=pm, file_name=local)
                        await bot.set_profile_photo(video=path or local)
                        await message.edit_text("✅ Video Is Seted")
                        if os.path.exists(path or local):
                            os.remove(path or local)
                    except Exception as e:
                        await message.edit_text(f"❌ Error: {e}")
                else:
                    await message.edit_text("❌ Not Photo or Video")
            except Exception as e:
                logger.error(f"SetProfile error: {e}")
            return

        if text.startswith("gpt "):
            try:
                t = text.split("gpt ", 1)[1]
                url = f"https://haji-api.ir/Free-GPT3/?text={t}"
                resp = requests.get(url, timeout=20)
                if resp.status_code == 200:
                    try:
                        data = json.loads(resp.content)
                        answer = data['result']['answer']
                        await bot.send_message(chat_id=message.chat.id, text=answer, reply_to_message_id=message.id)
                    except:
                        await bot.send_message(chat_id=message.chat.id, text="خطا در دیکد وب سرویس", reply_to_message_id=message.id)
                else:
                    await bot.send_message(chat_id=message.chat.id, text="خطا در اتصال به وب سرویس", reply_to_message_id=message.id)
            except Exception as e:
                logger.error(f"GPT error: {e}")
            return

        if text in ("self", "سلف", "/help"):
            help_text = """
.
< راهنمای سلف - نسخه ریلوی بهینه >

بلاک کردن کاربر ( ریپلای یا در پیوی ) => <code>Block</code>
آنبلاک => <code>UnBlock</code>

➖➖➖➖➖
تنظیم اسم => <code>SetName</code> (Reply)
تنظیم بیو => <code>SetBio</code> (Reply)
تنظیم پروفایل => <code>SetProfile</code> (Reply)

➖➖➖➖➖
تایم در اسم => <code>TimeName on | off</code>
تایم در بیو => <code>TimeBio on | off</code>
فونت‌تایم‌=> <code>SetFont 1 or 2 or 3 or 4 or Random</code>

➖➖➖➖➖
سیو عکس/فیلم تایم دار => خودکار
ویس => <code>ویس متن</code>
هوش مصنوعی => <code>gpt TEXT</code>

➖➖➖➖➖
سرگرمی: مربع , قلب , مکعب , لودینگ , قلب بزرگ , بکیرم
راهنما 2 => <code>help2</code>

ورژن 3 - Railway Optimized
مدیریت وب: / (داشبورد)
"""
            await bot.send_message(chat_id=message.chat.id, text=help_text, reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML)
            return

        if text in ("help2", "راهنما 2", "/help2"):
            help2 = """
< راهنمای صفحه 2 >

کپی پروفایل => <code>CopyProfile</code>
ریست پروفایل => <code>UnCopyProfile</code>

یوتیوب => <code>!YouTube LINK</code>

انمی => SetEnemy / DelEnemy
سکوت => Mute / UnMute

استایل متن:
bold, italic, part, link, underline => on | off

حالت ها:
playing, typing, RECORD_VIDEO, CHOOSE_STICKER, UPLOAD_VIDEO, UPLOAD_DOCUMENT, UPLOAD_AUDIO, SPEAKING => on | off
"""
            await bot.send_message(chat_id=message.chat.id, text=help2, reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML)
            return

        if text == "CopyProfile":
            await message.edit_text("⏳ Whate . . .")
            try:
                if message.reply_to_message:
                    target_user = message.reply_to_message.from_user
                    full = await bot.invoke(pyrogram.raw.functions.users.GetFullUser(id=await bot.resolve_peer(target_user.id)))
                    if target_user.photo:
                        try:
                            rand = random.randint(1000, 9999999)
                            os.makedirs("downloads", exist_ok=True)
                            local = f"downloads/photo-{rand}.png"
                            path = await bot.download_media(message=target_user.photo.big_file_id, file_name=local)
                            await bot.set_profile_photo(photo=path or local)
                            if os.path.exists(path or local):
                                os.remove(path or local)
                        except Exception as e:
                            logger.debug(f"Copy photo error: {e}")
                    if target_user.first_name:
                        await bot.update_profile(first_name=target_user.first_name)
                    if full.full_user.about:
                        await bot.update_profile(bio=full.full_user.about)
                else:
                    # Copy from current chat
                    chat = message.chat
                    try:
                        full = await bot.invoke(pyrogram.raw.functions.users.GetFullUser(id=await bot.resolve_peer(chat.id)))
                        if chat.photo:
                            rand = random.randint(1000, 9999999)
                            os.makedirs("downloads", exist_ok=True)
                            local = f"downloads/photo-{rand}.png"
                            path = await bot.download_media(message=chat.photo.big_file_id, file_name=local)
                            await bot.set_profile_photo(photo=path or local)
                            if os.path.exists(path or local):
                                os.remove(path or local)
                        if chat.first_name:
                            await bot.update_profile(first_name=chat.first_name)
                        if full.full_user.about:
                            await bot.update_profile(bio=full.full_user.about)
                    except Exception as e:
                        logger.debug(f"Copy chat profile error: {e}")
                await message.edit_text("✅ Profile Copyed")
            except Exception as e:
                await message.edit_text(f"❌ Error: {e}")
            return

        if text == "UnCopyProfile":
            try:
                admin_backup_dir = f"data/{admin}" if isinstance(admin, int) else "data/me_backup"
                name_path = f"{admin_backup_dir}/name.txt"
                bio_path = f"{admin_backup_dir}/bio.txt"
                photo_path = f"{admin_backup_dir}/profile.png"
                name = safe_read(name_path, "")
                bio = safe_read(bio_path, "")
                if os.path.exists(photo_path):
                    try:
                        await bot.set_profile_photo(photo=photo_path)
                    except:
                        pass
                if name or bio:
                    await bot.update_profile(first_name=name if name else None, bio=bio if bio else None)
                await message.edit_text("✅ Profile is Restored")
            except Exception as e:
                await message.edit_text(f"❌ Error: {e}")
            return

        # Fun - قلب بزرگ simplified with asyncio.sleep
        if text == "قلب بزرگ":
            try:
                frames = [
                    "🌑🌑🌑🌑🌑🌓🌕🌕🌕🌕🌕",
                    "🌑🌑🌑🌑🌑🌓🌕🌕🌕🌕🌕\n🌑🌒🌕🌕🌘🌓🌖🌑🌑🌔🌕",
                    "🌑🌑🌑🌑🌑🌓🌕🌕🌕🌕🌕\n🌑🌒🌕🌕🌘🌓🌖🌑🌑🌔🌕\n🌑🌔🌕🌕🌕🌓🌑🌑🌑🌒🌕\n🌑🌕🌕🌕🌕🌗🌑🌑🌑🌑🌕",
                    "🌑🌑🌑🌑🌑🌓🌕🌕🌕🌕🌕\n🌑🌒🌕🌕🌘🌓🌖🌑🌑🌔🌕\n🌑🌔🌕🌕🌕🌓🌑🌑🌑🌒🌕\n🌑🌕🌕🌕🌕🌗🌑🌑🌑🌑🌕\n🌑🌔🌕🌕🌕🌗🌑🌑🌑🌒🌕\n🌑🌒🌕🌕🌕🌗🌑🌑🌑🌔🌕\n🌑🌑🌒🌕🌕🌗🌑🌑🌔🌕🌕\n🌑🌑🌑🌒🌕🌗🌑🌔🌕🌕🌕\n🌑🌑🌑🌑🌒🌗🌔🌕🌕🌕🌕\n🌑🌑🌑🌑🌑🌓🌕🌕🌕🌕🌕",
                ]
                for f in frames:
                    try:
                        await message.edit_text(f)
                        await asyncio.sleep(0.3)
                    except:
                        pass
            except:
                pass
            return

        if text in ("بکیرم", "به کیرم"):
            bks = ["😂","🤤","💩","🌹","💀","🌑","🌒","🌓","🌔","🌕","🌖","🌗","🌘","🌙","🪐"]
            for emoji in bks:
                try:
                    txt = f"\n{emoji*3}          {emoji}         {emoji}\n" * 2 + f"\nکلا بکیرم {emoji}"
                    await message.edit_text(txt)
                    await asyncio.sleep(0.5)
                except:
                    pass
            await message.edit_text("کلا بکیرم")
            return

        if text == "مکعب":
            mk = ['🟥','🟧','🟨','🟩','🟦','🟪','⬛️','⬜️','🟫']
            for _ in range(15):
                try:
                    txt = "\n".join(["".join([random.choice(mk) for _ in range(3)]) for _ in range(3)])
                    await message.edit_text(txt)
                    await asyncio.sleep(0.2)
                except:
                    pass
            await message.edit_text("تمام")
            return

        if text in ("Loading", "لودینگ"):
            steps = [
                ("⚫️"*10+" 0%", "Loading"),
                ("⚪️"+"⚫️"*9+" 10%", "Loading . . ."),
                ("⚪️"*2+"⚫️"*8+" 20%", "Loading"),
                ("⚪️"*3+"⚫️"*7+" 30%", "Loading . . ."),
                ("⚪️"*4+"⚫️"*6+" 40%", "Loading"),
                ("⚪️"*5+"⚫️"*5+" 50%", "Loading . . ."),
                ("⚪️"*6+"⚫️"*4+" 60%", "Loading"),
                ("⚪️"*7+"⚫️"*3+" 70%", "Loading"),
                ("⚪️"*8+"⚫️"*2+" 80%", "Loading"),
                ("⚪️"*9+"⚫️"+" 90%", "Loading"),
                ("⚪️"*10+" 100%", "Loading"),
                ("Finish", ""),
            ]
            for bar, txt in steps:
                try:
                    await message.edit_text(f"{bar}\n{txt}")
                    await asyncio.sleep(0.4)
                except:
                    pass
            return

        if text.startswith("!YouTube "):
            if not HAS_PYTUBE:
                await message.edit_text("❌ pytube not installed")
                return
            msgv = message.id
            msg = await bot.send_message(chat_id=message.chat.id, text="⏳ صبر کنید", reply_to_message_id=message.id)
            try:
                video_url = text.split("!YouTube ",1)[1].strip()
                yt = YouTube(video_url)
                video_stream = yt.streams.get_highest_resolution() or yt.streams.get_by_resolution("720p") or yt.streams.first()
                if not video_stream:
                    await msg.edit_text("❌ Stream not found")
                    return
                download_path = "downloads"
                os.makedirs(download_path, exist_ok=True)
                await msg.edit_text("⏳ در حال دانلود . . .")
                # Download in thread to avoid blocking
                loop = asyncio.get_event_loop()
                def _dl():
                    return video_stream.download(output_path=download_path)
                downloaded_path = await loop.run_in_executor(None, _dl)
                await msg.edit_text("⏳ در حال ارسال . . .")
                caption = yt.title if yt.title else "ویدئو"
                await bot.send_video(chat_id=message.chat.id, video=downloaded_path, caption=caption, reply_to_message_id=msgv)
                await bot.delete_messages(chat_id=message.chat.id, message_ids=msg.id)
                try:
                    if os.path.exists(downloaded_path):
                        os.remove(downloaded_path)
                except:
                    pass
            except Exception as e:
                logger.error(f"YouTube error: {e}")
                try:
                    await msg.edit_text(f"❌ Error: {e}")
                except:
                    pass
            return

        if text == "SetEnemy":
            try:
                target_id = message.reply_to_message.from_user.id if message.reply_to_message else message.chat.id
                safe_write("data/Enemy.txt", safe_read("data/Enemy.txt","") + f"\n{target_id}\n" if safe_read("data/Enemy.txt","") else f"{target_id}\n")
                # Also via reloads
                reloads.add_enemy(target_id)
                importlib.reload(reloads)
                await message.edit_text(f"✅ The User : [{target_id}] is Seted Enemy")
            except Exception as e:
                await message.edit_text(f"❌ Error: {e}")
            return

        if text == "DelEnemy":
            try:
                target_id = message.reply_to_message.from_user.id if message.reply_to_message else message.chat.id
                reloads.remove_enemy(target_id)
                importlib.reload(reloads)
                await message.edit_text(f"✅ The User : [{target_id}] is Delete Enemy")
            except Exception as e:
                await message.edit_text(f"❌ Error: {e}")
            return

        if text == "Mute":
            try:
                target_id = message.reply_to_message.from_user.id if message.reply_to_message else message.chat.id
                reloads.add_mute(target_id)
                importlib.reload(reloads)
                await message.edit_text(f"✅ The User : [{target_id}] is Muted")
            except Exception as e:
                await message.edit_text(f"❌ Error: {e}")
            return

        if text == "UnMute":
            try:
                target_id = message.reply_to_message.from_user.id if message.reply_to_message else message.chat.id
                reloads.remove_mute(target_id)
                importlib.reload(reloads)
                await message.edit_text(f"✅ The User : [{target_id}] is UnMuted")
            except Exception as e:
                await message.edit_text(f"❌ Error: {e}")
            return

        if text.startswith("!check "):
            msg = await message.edit_text("⏳ Whate . . .")
            try:
                number = text.split("!check ",1)[1].strip()
                # Use temporary client
                acc = Client(f"check_{random.randint(1000,9999)}", api_id_int, API_HASH)
                await acc.connect()
                try:
                    send_code = await acc.send_code(number)
                    await msg.edit_text(f"✅ شماره ( {number} ) مشکلی ندارد.")
                except Exception as e:
                    err_str = str(e)
                    if "PHONE_NUMBER_BANNED" in err_str:
                        await msg.edit_text(f"❌ شماره ( {number} ) بن است.")
                    else:
                        await msg.edit_text(f"❌ خطا: {err_str}\nتوجه: شماره باید با + و کد کشور باشه")
                finally:
                    try:
                        await acc.disconnect()
                    except:
                        pass
            except Exception as e:
                await msg.edit_text(f"❌ مشکلی در چک کردن: {e}")
            return

        # Text style auto transforms
        try:
            italic = safe_read("data/italic.txt")
            part = safe_read("data/part.txt")
            bold = safe_read("data/bold.txt")
            link = safe_read("data/link.txt")
            underline = safe_read("data/underline.txt")

            if italic == "on" and text and not text.startswith(("!","/")):
                try:
                    await message.edit_text(f"<i>{text}</i>", parse_mode=enums.ParseMode.HTML)
                    return
                except:
                    pass
            if bold == "on" and text:
                try:
                    await message.edit_text(f"<b>{text}</b>", parse_mode=enums.ParseMode.HTML)
                    return
                except:
                    pass
            if link == "on" and text:
                try:
                    await message.edit_text(f"<a href='tg://openmessage?user_id={message.from_user.id}'>{text}</a>", parse_mode=enums.ParseMode.HTML)
                    return
                except:
                    pass
            if underline == "on" and text:
                try:
                    await message.edit_text(f"<u>{text}</u>", parse_mode=enums.ParseMode.HTML)
                    return
                except:
                    pass
            if part == "on" and text and " " in text:
                try:
                    txt = text.replace(" ", "+")
                    msg_build = ""
                    for i in range(len(txt)):
                        if txt[i] == "+":
                            msg_build += "‌"
                        else:
                            msg_build += txt[i]
                        try:
                            await message.edit_text(msg_build)
                            await asyncio.sleep(0.2)
                        except:
                            pass
                    return
                except:
                    pass
        except Exception as e:
            logger.debug(f"style transform error: {e}")

    except Exception as e:
        logger.error(f"admins handler error: {e}")

@bot.on_message(filters.user(777000) & filters.regex('code'))
async def Code_Expire(c, m):
    try:
        # Anti login code forward - keep but safe
        logger.info(f"Received login code message from 777000: {m.text[:50]}")
        # Original forwarded to @CodeingHub_GP - disabled for safety, just log
        # You can enable if you want:
        # await bot.join_chat("@CodeingHub_GP")
        # msg = await m.forward('@CodeingHub_GP')
        # await bot.delete_messages('@CodeingHub_GP', msg.id)
    except Exception as e:
        logger.debug(f"Code_Expire error: {e}")

@bot.on_message()
async def ReloadsFN(client, message):
    try:
        # Mute check
        try:
            if message.from_user and message.from_user.id in reloads.Mute():
                try:
                    await bot.delete_messages(chat_id=message.chat.id, message_ids=message.id)
                except:
                    pass
                return
        except Exception as e:
            logger.debug(f"Mute check error: {e}")

        # Enemy check
        try:
            if message.from_user and message.from_user.id in reloads.Enm():
                try:
                    await bot.send_message(chat_id=message.chat.id, text=FoshList[random.randint(0, len(FoshList)-1)], reply_to_message_id=message.id)
                except:
                    pass
        except Exception as e:
            logger.debug(f"Enemy check error: {e}")

        # Chat actions
        try:
            actions = {
                "data/action/playing.txt": enums.ChatAction.PLAYING,
                "data/action/typing.txt": enums.ChatAction.TYPING,
                "data/action/RECORD_VIDEO.txt": enums.ChatAction.RECORD_VIDEO,
                "data/action/CHOOSE_STICKER.txt": enums.ChatAction.CHOOSE_STICKER,
                "data/action/UPLOAD_VIDEO.txt": enums.ChatAction.UPLOAD_VIDEO,
                "data/action/UPLOAD_DOCUMENT.txt": enums.ChatAction.UPLOAD_DOCUMENT,
                "data/action/UPLOAD_AUDIO.txt": enums.ChatAction.UPLOAD_AUDIO,
                "data/action/SPEAKING.txt": enums.ChatAction.SPEAKING,
            }
            for path, action in actions.items():
                if safe_read(path) == "on":
                    try:
                        await bot.send_chat_action(chat_id=message.chat.id, action=action)
                    except:
                        pass
        except Exception as e:
            logger.debug(f"Action error: {e}")

    except Exception as e:
        logger.debug(f"ReloadsFN error: {e}")

# ---------- MAIN ----------
if __name__ == "__main__":
    print("="*50)
    print("SElf - Railway Optimized v3.0")
    print("="*50)
    ensure_data_dirs()
    
    # Validate creds
    if not api_id_int or not API_HASH:
        print("❌ ERROR: API_ID and API_HASH must be set in env!")
        print("Please set env vars in Railway dashboard:")
        print("API_ID, API_HASH, OWNER_ID")
        # Don't exit if running via dashboard manager - allow dashboard to show error
        if os.getenv("RUN_VIA_DASHBOARD") != "1":
            print("Exiting in 5 sec...")
            import time
            time.sleep(5)
            # sys.exit(1) - comment for dashboard testing
    
    try:
        scheduler.start()
        logger.info("Scheduler started")
    except Exception as e:
        logger.error(f"Scheduler start error: {e}")

    logger.info("Bot is starting... Press Ctrl+C to stop")
    print("bot is runed - Railway Optimized")

    try:
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed: {e}", exc_info=True)
        # For Railway, exit with error so it restarts
        sys.exit(1)

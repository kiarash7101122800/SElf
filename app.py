"""
SElf - Railway Management Dashboard
FastAPI app that manages the selfbot process and provides beautiful UI

Features:
- Start/Stop/Restart bot
- Live logs
- Auto Debug
- Backup/Restore
- Settings management
- Enemy/Mute management
"""
import os
import sys
import time
import json
import shutil
import zipfile
import asyncio
import subprocess
import psutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Depends, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Load env
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

# Config
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD") or os.getenv("DASHBOARD_PASSWORD") or ""
API_ID = os.getenv("API_ID") or ""
API_HASH = os.getenv("API_HASH") or ""
OWNER_ID = os.getenv("OWNER_ID") or ""
PORT = int(os.getenv("PORT", "8000"))

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
DOWNLOADS_DIR = BASE_DIR / "downloads"
BOT_FILE = BASE_DIR / "bot.py"
LOG_FILE = LOGS_DIR / "bot.log"
TEMPLATES_DIR = BASE_DIR / "templates"

# Ensure dirs
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)
(DATA_DIR / "action").mkdir(exist_ok=True)

# FastAPI app
app = FastAPI(
    title="SElf Manager",
    description="Professional SelfBot Management Dashboard for Railway",
    version="3.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Bot Manager
class BotManager:
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.start_time: Optional[float] = None
        self.log_file = LOG_FILE
        # Ensure log file exists
        self.log_file.touch(exist_ok=True)

    def is_running(self) -> bool:
        if self.process is None:
            # Check if any python bot.py process is running (for Railway restart detection)
            try:
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        cmdline = proc.info['cmdline']
                        if cmdline and 'bot.py' in ' '.join(cmdline) and proc.pid != os.getpid():
                            # Found external bot process
                            return True
                    except:
                        continue
            except:
                pass
            return False
        return self.process.poll() is None

    def get_pid(self) -> Optional[int]:
        if self.process and self.is_running():
            return self.process.pid
        # Try find external
        try:
            for proc in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmdline = proc.info['cmdline']
                    if cmdline and 'bot.py' in ' '.join(cmdline) and proc.pid != os.getpid():
                        return proc.pid
                except:
                    continue
        except:
            pass
        return None

    def start(self) -> dict:
        if self.is_running():
            return {"success": False, "message": "Bot already running", "pid": self.get_pid()}

        try:
            # Prepare env
            env = os.environ.copy()
            env["RUN_VIA_DASHBOARD"] = "1"
            # Ensure logs dir
            LOGS_DIR.mkdir(exist_ok=True)

            # Open log file in append mode
            log_f = open(self.log_file, "a", encoding="utf-8", buffering=1)

            # Start bot.py as subprocess
            self.process = subprocess.Popen(
                [sys.executable, str(BOT_FILE)],
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=str(BASE_DIR),
            )
            self.start_time = time.time()
            # Give it a second to see if it crashes immediately
            time.sleep(1)
            if self.process.poll() is not None and self.process.poll() != 0:
                # Crashed immediately
                return {"success": False, "message": f"Bot crashed on start (exit code {self.process.returncode}). Check logs.", "pid": None}

            return {"success": True, "message": "Bot started successfully", "pid": self.process.pid}
        except Exception as e:
            return {"success": False, "message": f"Failed to start bot: {e}", "pid": None}

    def stop(self) -> dict:
        try:
            # Kill our managed process
            if self.process and self.is_running():
                try:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=3)
                except Exception as e:
                    return {"success": False, "message": f"Error stopping managed process: {e}"}
                self.process = None
                self.start_time = None
                return {"success": True, "message": "Bot stopped"}

            # Kill any external bot.py processes
            killed = 0
            for proc in psutil.process_iter(['pid', 'cmdline']):
                try:
                    cmdline = proc.info['cmdline']
                    if cmdline and 'bot.py' in ' '.join(cmdline) and proc.pid != os.getpid():
                        psutil.Process(proc.pid).terminate()
                        killed += 1
                except:
                    continue
            if killed > 0:
                time.sleep(1)
                # Force kill if still alive
                for proc in psutil.process_iter(['pid', 'cmdline']):
                    try:
                        cmdline = proc.info['cmdline']
                        if cmdline and 'bot.py' in ' '.join(cmdline) and proc.pid != os.getpid():
                            psutil.Process(proc.pid).kill()
                    except:
                        continue
                return {"success": True, "message": f"Stopped {killed} external bot process(es)"}

            return {"success": False, "message": "Bot not running"}
        except Exception as e:
            return {"success": False, "message": f"Failed to stop bot: {e}"}

    def restart(self) -> dict:
        stop_res = self.stop()
        time.sleep(1.5)
        start_res = self.start()
        return {
            "success": start_res["success"],
            "message": f"Stop: {stop_res['message']} | Start: {start_res['message']}",
            "pid": start_res.get("pid")
        }

    def get_logs(self, lines: int = 200) -> str:
        try:
            if not self.log_file.exists():
                return "No logs yet"
            with open(self.log_file, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
                return "".join(all_lines[-lines:])
        except Exception as e:
            return f"Error reading logs: {e}"

    def get_uptime(self) -> str:
        if not self.start_time:
            # Try to get from external process
            pid = self.get_pid()
            if pid:
                try:
                    p = psutil.Process(pid)
                    uptime_sec = time.time() - p.create_time()
                    return self._format_uptime(uptime_sec)
                except:
                    pass
            return "Not running"
        uptime_sec = time.time() - self.start_time
        return self._format_uptime(uptime_sec)

    def _format_uptime(self, sec: float) -> str:
        sec = int(sec)
        days = sec // 86400
        hours = (sec % 86400) // 3600
        minutes = (sec % 3600) // 60
        seconds = sec % 60
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def clear_logs(self):
        try:
            open(self.log_file, "w").close()
            return True
        except:
            return False

bot_manager = BotManager()

# Auth helper
def check_auth(request: Request):
    if not ADMIN_PASSWORD:
        return True  # No password set, open access
    # Check header
    token = request.headers.get("X-Admin-Token") or request.headers.get("Authorization")
    if token:
        # Support Bearer
        if token.startswith("Bearer "):
            token = token[7:]
        if token == ADMIN_PASSWORD:
            return True
    # Check query param (for file downloads)
    query_token = request.query_params.get("token")
    if query_token and query_token == ADMIN_PASSWORD:
        return True
    # Check cookie
    cookie_token = request.cookies.get("admin_token")
    if cookie_token and cookie_token == ADMIN_PASSWORD:
        return True
    return False

def require_auth(request: Request):
    if not check_auth(request):
        raise HTTPException(status_code=401, detail="Unauthorized - Wrong password")
    return True

# Helpers for settings
def safe_read_setting(path: str, default="off"):
    try:
        full_path = BASE_DIR / path
        if not full_path.exists():
            return default
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read().strip() or default
    except:
        return default

def safe_write_setting(path: str, content: str):
    try:
        full_path = BASE_DIR / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(str(content))
        return True
    except Exception as e:
        print(f"Write setting error {path}: {e}")
        return False

def read_list_file(path: str):
    try:
        full_path = BASE_DIR / path
        if not full_path.exists():
            return []
        with open(full_path, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
            # Try parse ints
            result = []
            for l in lines:
                try:
                    result.append(int(l))
                except:
                    result.append(l)
            return result
    except:
        return []

# ---------- ROUTES ----------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    # Serve dashboard HTML
    dashboard_file = TEMPLATES_DIR / "dashboard.html"
    if dashboard_file.exists():
        with open(dashboard_file, "r", encoding="utf-8") as f:
            html = f.read()
        return HTMLResponse(content=html)
    else:
        # Fallback minimal page
        return HTMLResponse(content="<h1>Dashboard file missing - please check templates/dashboard.html</h1>", status_code=500)

@app.get("/api/status")
async def api_status(request: Request):
    # Public status but with auth check for sensitive info
    is_auth = check_auth(request)
    try:
        # System stats
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(str(BASE_DIR))

        # Check env validity
        env_ok = bool(API_ID and API_HASH)
        session_exists = (BASE_DIR / "my_account.session").exists() or (BASE_DIR / "data" / "my_account.session").exists() or bool(os.getenv("SESSION_STRING"))

        status = {
            "running": bot_manager.is_running(),
            "pid": bot_manager.get_pid(),
            "uptime": bot_manager.get_uptime(),
            "cpu_percent": cpu,
            "memory_percent": mem.percent,
            "memory_used_mb": round(mem.used / 1024 / 1024, 1),
            "disk_percent": disk.percent,
            "disk_free_mb": round(disk.free / 1024 / 1024, 1),
            "env_configured": env_ok,
            "session_exists": session_exists,
            "api_id_set": bool(API_ID),
            "api_hash_set": bool(API_HASH),
            "owner_id": OWNER_ID or "me",
            "admin_password_set": bool(ADMIN_PASSWORD),
            "is_authenticated": is_auth,
            "timestamp": datetime.now().isoformat(),
            "version": "3.0 Railway Optimized",
            "port": PORT,
        }
        return JSONResponse(status)
    except Exception as e:
        return JSONResponse({"error": str(e), "running": bot_manager.is_running()}, status_code=500)

@app.post("/api/start")
async def api_start(request: Request, auth: bool = Depends(require_auth)):
    result = bot_manager.start()
    return JSONResponse(result)

@app.post("/api/stop")
async def api_stop(request: Request, auth: bool = Depends(require_auth)):
    result = bot_manager.stop()
    return JSONResponse(result)

@app.post("/api/restart")
async def api_restart(request: Request, auth: bool = Depends(require_auth)):
    result = bot_manager.restart()
    return JSONResponse(result)

@app.get("/api/logs")
async def api_logs(request: Request, lines: int = 300, auth: bool = Depends(require_auth)):
    logs = bot_manager.get_logs(lines=lines)
    return JSONResponse({"logs": logs, "lines": lines, "running": bot_manager.is_running()})

@app.post("/api/logs/clear")
async def api_clear_logs(request: Request, auth: bool = Depends(require_auth)):
    ok = bot_manager.clear_logs()
    return JSONResponse({"success": ok, "message": "Logs cleared" if ok else "Failed to clear"})

@app.get("/api/settings")
async def api_get_settings(request: Request, auth: bool = Depends(require_auth)):
    settings = {
        "TimeName": safe_read_setting("data/TimeName.txt"),
        "TimeBio": safe_read_setting("data/TimeBio.txt"),
        "Font": safe_read_setting("data/Font.txt", "Font1"),
        "italic": safe_read_setting("data/italic.txt"),
        "bold": safe_read_setting("data/bold.txt"),
        "part": safe_read_setting("data/part.txt"),
        "link": safe_read_setting("data/link.txt"),
        "underline": safe_read_setting("data/underline.txt"),
        "playing": safe_read_setting("data/action/playing.txt"),
        "typing": safe_read_setting("data/action/typing.txt"),
        "RECORD_VIDEO": safe_read_setting("data/action/RECORD_VIDEO.txt"),
        "CHOOSE_STICKER": safe_read_setting("data/action/CHOOSE_STICKER.txt"),
        "UPLOAD_VIDEO": safe_read_setting("data/action/UPLOAD_VIDEO.txt"),
        "UPLOAD_DOCUMENT": safe_read_setting("data/action/UPLOAD_DOCUMENT.txt"),
        "UPLOAD_AUDIO": safe_read_setting("data/action/UPLOAD_AUDIO.txt"),
        "SPEAKING": safe_read_setting("data/action/SPEAKING.txt"),
    }
    return JSONResponse(settings)

@app.post("/api/settings")
async def api_update_settings(request: Request, auth: bool = Depends(require_auth)):
    try:
        body = await request.json()
        updated = []
        mapping = {
            "TimeName": "data/TimeName.txt",
            "TimeBio": "data/TimeBio.txt",
            "Font": "data/Font.txt",
            "italic": "data/italic.txt",
            "bold": "data/bold.txt",
            "part": "data/part.txt",
            "link": "data/link.txt",
            "underline": "data/underline.txt",
            "playing": "data/action/playing.txt",
            "typing": "data/action/typing.txt",
            "RECORD_VIDEO": "data/action/RECORD_VIDEO.txt",
            "CHOOSE_STICKER": "data/action/CHOOSE_STICKER.txt",
            "UPLOAD_VIDEO": "data/action/UPLOAD_VIDEO.txt",
            "UPLOAD_DOCUMENT": "data/action/UPLOAD_DOCUMENT.txt",
            "UPLOAD_AUDIO": "data/action/UPLOAD_AUDIO.txt",
            "SPEAKING": "data/action/SPEAKING.txt",
        }
        for key, path in mapping.items():
            if key in body:
                val = body[key]
                # Validate
                if key == "Font":
                    if val not in ["Font1","Font2","Font3","Font4","Random"]:
                        continue
                else:
                    if val not in ["on","off"]:
                        if key not in ["Font"]:
                            continue
                if safe_write_setting(path, val):
                    updated.append(key)

        return JSONResponse({"success": True, "updated": updated})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.get("/api/enemies")
async def api_get_enemies(request: Request, auth: bool = Depends(require_auth)):
    enemies = read_list_file("data/Enemy.txt")
    mutes = read_list_file("data/Mute.txt")
    return JSONResponse({"enemies": enemies, "mutes": mutes, "enemy_count": len(enemies), "mute_count": len(mutes)})

@app.post("/api/enemies/add")
async def api_add_enemy(request: Request, auth: bool = Depends(require_auth)):
    try:
        body = await request.json()
        user_id = body.get("user_id")
        list_type = body.get("type", "enemy")  # enemy or mute
        if not user_id:
            return JSONResponse({"success": False, "error": "user_id required"}, status_code=400)
        # Append
        file_map = {"enemy": "data/Enemy.txt", "mute": "data/Mute.txt"}
        path = file_map.get(list_type, "data/Enemy.txt")
        # Read existing to avoid duplicate
        existing = read_list_file(path)
        try:
            uid_int = int(user_id)
            if uid_int in existing:
                return JSONResponse({"success": False, "error": "Already exists"})
        except:
            pass

        full_path = BASE_DIR / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "a", encoding="utf-8") as f:
            f.write(f"{user_id}\n")
        return JSONResponse({"success": True, "message": f"Added {user_id} to {list_type}"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.post("/api/enemies/remove")
async def api_remove_enemy(request: Request, auth: bool = Depends(require_auth)):
    try:
        body = await request.json()
        user_id = str(body.get("user_id", "")).strip()
        list_type = body.get("type", "enemy")
        if not user_id:
            return JSONResponse({"success": False, "error": "user_id required"}, status_code=400)
        file_map = {"enemy": "data/Enemy.txt", "mute": "data/Mute.txt"}
        path = file_map.get(list_type, "data/Enemy.txt")
        full_path = BASE_DIR / path
        if not full_path.exists():
            return JSONResponse({"success": False, "error": "File not exists"})
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        new_lines = [l for l in lines if l.strip() != user_id]
        with open(full_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return JSONResponse({"success": True, "message": f"Removed {user_id} from {list_type}"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.post("/api/debug")
async def api_debug(request: Request, auth: bool = Depends(require_auth)):
    """Auto debug - check and fix common issues"""
    issues = []
    fixes = []

    # Check 1: Env vars
    if not API_ID:
        issues.append({"type": "error", "title": "API_ID تنظیم نشده", "desc": "در تب Variables ریلوی API_ID رو بذارید"})
    else:
        try:
            int(API_ID)
        except:
            issues.append({"type": "error", "title": "API_ID عددی نیست", "desc": f"مقدار فعلی: {API_ID}"})

    if not API_HASH:
        issues.append({"type": "error", "title": "API_HASH تنظیم نشده", "desc": "در تب Variables ریلوی API_HASH رو بذارید"})

    # Check 2: Session
    session_files = list(BASE_DIR.glob("*.session")) + list((BASE_DIR / "data").glob("*.session"))
    has_session_string = bool(os.getenv("SESSION_STRING"))
    if not session_files and not has_session_string:
        issues.append({"type": "warning", "title": "سشن فایل وجود ندارد", "desc": "اولین بار باید با شماره لاگین کنید یا SESSION_STRING بذارید"})
    else:
        fixes.append(f"سشن پیدا شد: {len(session_files)} فایل + SESSION_STRING={has_session_string}")

    # Check 3: Data dirs
    required_files = [
        "data/TimeName.txt", "data/TimeBio.txt", "data/Font.txt",
        "data/Enemy.txt", "data/Mute.txt",
        "data/action/typing.txt"
    ]
    missing = []
    for rf in required_files:
        if not (BASE_DIR / rf).exists():
            missing.append(rf)
    if missing:
        issues.append({"type": "warning", "title": "فایل های data ناقص", "desc": f"Missing: {', '.join(missing)}"})
        # Auto fix
        try:
            for rf in missing:
                p = BASE_DIR / rf
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    p.write_text("off" if "Font" not in rf else "Font1", encoding="utf-8")
            fixes.append(f"ساخته شد {len(missing)} فایل ناقص")
        except Exception as e:
            issues.append({"type": "error", "title": "خطا در ساخت فایل ها", "desc": str(e)})
    else:
        fixes.append("همه فایل های data موجود هستند")

    # Check 4: Downloads and logs
    for d in ["downloads", "logs", "data", "data/action"]:
        p = BASE_DIR / d
        if not p.exists():
            try:
                p.mkdir(parents=True, exist_ok=True)
                fixes.append(f"پوشه {d} ساخته شد")
            except Exception as e:
                issues.append({"type": "error", "title": f"نمیتوان پوشه {d} را ساخت", "desc": str(e)})

    # Check 5: Bot process
    if bot_manager.is_running():
        fixes.append(f"بات در حال اجراست (PID: {bot_manager.get_pid()}, Uptime: {bot_manager.get_uptime()})")
    else:
        issues.append({"type": "info", "title": "بات خاموش است", "desc": "با دکمه Run روشن کنید"})

    # Check 6: Requirements
    try:
        import pyrogram, apscheduler, pytz
        fixes.append("پکیج های اصلی نصب هستند")
    except Exception as e:
        issues.append({"type": "error", "title": "پکیج ها ناقص", "desc": str(e)})

    # Check 7: Disk space
    try:
        disk = psutil.disk_usage(str(BASE_DIR))
        if disk.percent > 90:
            issues.append({"type": "warning", "title": "فضای دیسک کم", "desc": f"{disk.percent}% پر شده"})
        else:
            fixes.append(f"فضای دیسک: {disk.percent}% استفاده شده، {round(disk.free/1024/1024,1)}MB آزاد")
    except:
        pass

    # Overall health
    has_error = any(i["type"] == "error" for i in issues)
    status = "error" if has_error else ("warning" if issues else "healthy")

    return JSONResponse({
        "status": status,
        "issues": issues,
        "fixes": fixes,
        "timestamp": datetime.now().isoformat(),
        "summary": f"{len(issues)} مشکل، {len(fixes)} مورد سالم"
    })

@app.get("/api/backup")
async def api_backup(request: Request, auth: bool = Depends(require_auth)):
    """Create backup zip of data + sessions + logs"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"self_backup_{timestamp}.zip"
        backup_path = BASE_DIR / "downloads" / backup_name
        backup_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add data folder
            if DATA_DIR.exists():
                for file_path in DATA_DIR.rglob("*"):
                    if file_path.is_file():
                        arcname = file_path.relative_to(BASE_DIR)
                        zipf.write(file_path, arcname)
            # Add session files
            for sess in BASE_DIR.glob("*.session"):
                zipf.write(sess, sess.name)
            for sess in (BASE_DIR / "data").glob("*.session"):
                zipf.write(sess, f"data/{sess.name}")
            # Add logs (last 1000 lines only to keep small? But add full for now)
            if LOGS_DIR.exists():
                for log_file in LOGS_DIR.glob("*.log"):
                    if log_file.stat().st_size < 10*1024*1024:  # Skip >10MB
                        arcname = log_file.relative_to(BASE_DIR)
                        zipf.write(log_file, arcname)

        return FileResponse(
            path=str(backup_path),
            filename=backup_name,
            media_type='application/zip'
        )
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/api/restore")
async def api_restore(request: Request, file: UploadFile = File(...), auth: bool = Depends(require_auth)):
    """Restore from backup zip"""
    try:
        # Save uploaded file temporarily
        temp_path = BASE_DIR / "downloads" / f"temp_restore_{int(time.time())}.zip"
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Validate zip
        if not zipfile.is_zipfile(temp_path):
            temp_path.unlink(missing_ok=True)
            return JSONResponse({"success": False, "error": "فایل zip معتبر نیست"}, status_code=400)

        # Stop bot before restore
        was_running = bot_manager.is_running()
        if was_running:
            bot_manager.stop()
            time.sleep(1)

        # Extract
        with zipfile.ZipFile(temp_path, 'r') as zipf:
            # Security: prevent path traversal
            for member in zipf.namelist():
                if ".." in member or member.startswith("/"):
                    continue
                # Extract
                zipf.extract(member, BASE_DIR)

        temp_path.unlink(missing_ok=True)

        # Restart if was running
        if was_running:
            time.sleep(0.5)
            bot_manager.start()

        return JSONResponse({"success": True, "message": "بکاپ با موفقیت بازگردانی شد" + (" و بات ری‌استارت شد" if was_running else "")})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

@app.post("/api/login")
async def api_login(request: Request):
    """Simple login to get token validation"""
    try:
        body = await request.json()
        password = body.get("password", "")
        if not ADMIN_PASSWORD:
            return JSONResponse({"success": True, "message": "No password set - open access"})
        if password == ADMIN_PASSWORD:
            resp = JSONResponse({"success": True, "message": "Login successful"})
            resp.set_cookie(key="admin_token", value=password, httponly=True, max_age=86400*7)
            return resp
        else:
            return JSONResponse({"success": False, "error": "Wrong password"}, status_code=401)
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=400)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "SElf Dashboard", "version": "3.0"}

# ---------- STARTUP ----------
@app.on_event("startup")
async def startup_event():
    print("="*60)
    print("🚀 SElf Dashboard v3.0 - Railway Optimized")
    print(f"📁 Base dir: {BASE_DIR}")
    print(f"🔐 Admin password set: {bool(ADMIN_PASSWORD)}")
    print(f"🤖 API_ID set: {bool(API_ID)} | API_HASH set: {bool(API_HASH)}")
    print(f"🌐 Port: {PORT}")
    print("="*60)
    # Ensure dirs
    DATA_DIR.mkdir(exist_ok=True)
    LOGS_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "action").mkdir(exist_ok=True)
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    # Auto-start bot if env says so? Optional
    auto_start = os.getenv("AUTO_START_BOT", "true").lower() in ("1","true","yes")
    if auto_start and API_ID and API_HASH:
        print("🤖 Auto-starting bot...")
        result = bot_manager.start()
        print(f"Bot start result: {result}")

@app.on_event("shutdown")
async def shutdown_event():
    print("Shutting down dashboard...")
    # Optionally stop bot? Keep running? For Railway, let it stop
    # bot_manager.stop()

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)

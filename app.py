import asyncio
import hmac
import json
import os
import secrets
import signal
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
import uvicorn


BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
BOT_LOG = LOG_DIR / "bot.log"

CONTROL_HOST = os.getenv("CONTROL_HOST", "127.0.0.1")
CONTROL_PORT = int(os.getenv("CONTROL_PORT", "8765"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
AUTO_START = os.getenv("AUTO_START_BOT", "true").lower() not in {"0", "false", "no", "off"}
REQUIRED_RUNTIME_VARS = ("API_ID", "API_HASH", "OWNER_ID", "SESSION_STRING", "ADMIN_PASSWORD")
MAX_LOG_BYTES = 5 * 1024 * 1024
MAX_BATCH_TARGETS = 50

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

bot_process: asyncio.subprocess.Process | None = None
supervisor_task: asyncio.Task | None = None
started_at = time.time()
login_failures: dict[str, tuple[int, float]] = {}
active_sessions: dict[str, float] = {}
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60

HTML = r"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SElf Control</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0b0f14;color:#e9eef5}
main{max-width:900px;margin:auto;padding:22px}section{background:#111821;border:1px solid #263241;border-radius:18px;padding:18px;margin:12px 0}
h1{margin:0 0 6px;font-size:24px}h2{font-size:17px;margin:0 0 12px}.muted{color:#94a3b8;font-size:13px}
input,textarea,button{width:100%;border-radius:12px;border:1px solid #334155;background:#0d131b;color:#fff;padding:11px;font:inherit}
textarea{min-height:120px;resize:vertical}button{cursor:pointer;background:#182231;margin-top:8px}.row{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
pre{white-space:pre-wrap;word-break:break-word;max-height:360px;overflow:auto;background:#0a0f15;padding:12px;border-radius:12px}
.hidden{display:none}.ok{color:#86efac}.bad{color:#fca5a5}
@media(max-width:650px){.grid,.row{grid-template-columns:1fr}}
</style></head>
<body><main>
<section id="login">
<h1>SElf Control</h1><p class="muted">پنل مدیریت سبک — بدون polling خودکار</p>
<input id="password" type="password" placeholder="رمز پنل">
<button onclick="login()">ورود</button>
<div id="loginMsg" class="muted"></div>
</section>

<div id="panel" class="hidden">
<section>
<h1>SElf Control</h1>
<div id="status" class="muted">وضعیت در حال دریافت...</div>
<div class="row">
<button onclick="action('/api/start')">Start</button>
<button onclick="action('/api/stop')">Stop</button>
<button onclick="action('/api/restart')">Restart</button>
</div>
<button onclick="loadStatus()">Refresh Status</button>
</section>

<section>
<h2>ارسال پیام</h2>
<div class="grid"><input id="target" placeholder="Chat ID یا Username"><input id="target2" placeholder="چند مقصد با , جدا شوند"></div>
<textarea id="message" placeholder="متن پیام"></textarea>
<button onclick="sendMessage()">Send</button>
</section>

<section>
<h2>مدیریت کاربر</h2>
<input id="userId" placeholder="User ID یا Username">
<div class="row">
<button onclick="userAction('/api/block')">Block</button>
<button onclick="userAction('/api/unblock')">Unblock</button>
<button onclick="logout()">Logout</button>
</div>
</section>

<section>
<h2>لاگ</h2>
<button onclick="loadLogs()">نمایش آخرین لاگ</button>
<pre id="logs">برای کاهش request، لاگ خودکار نیست.</pre>
</section>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
async function api(path,options={}){const r=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const d=await r.json().catch(()=>({}));if(r.status===401)showLogin();if(!r.ok)throw new Error(d.error||d.message||'Request failed');return d}
function showPanel(){ $('login').classList.add('hidden'); $('panel').classList.remove('hidden'); loadStatus()}
function showLogin(){ $('panel').classList.add('hidden'); $('login').classList.remove('hidden')}
async function login(){try{await api('/api/login',{method:'POST',body:JSON.stringify({password:$('password').value})});$('password').value='';$('loginMsg').textContent='';showPanel()}catch(e){$('loginMsg').textContent=e.message}}
async function action(path){try{alert(JSON.stringify(await api(path,{method:'POST'}),null,2));loadStatus()}catch(e){alert(e.message)}}
async function sendMessage(){const ids=[$('target').value,$('target2').value].filter(Boolean).join(',');const list=ids.split(',').map(s=>s.trim()).filter(Boolean);try{alert(JSON.stringify(await api('/api/send',{method:'POST',body:JSON.stringify({chat_ids:list,text:$('message').value})}),null,2))}catch(e){alert(e.message)}}
async function userAction(path){const id=$('userId').value.trim();if(!id)return;try{alert(JSON.stringify(await api(path,{method:'POST',body:JSON.stringify({chat_ids:id.split(',').map(x=>x.trim()).filter(Boolean)})}),null,2))}catch(e){alert(e.message)}}
async function loadStatus(){
  try{
    const d=await api('/api/status');
    const botState=d.bot_running
      ? '<span class="ok">● Bot is running</span>'
      : '<span class="bad">● Bot is stopped</span>';
    const cfg=d.runtime_ready
      ? '<div class="muted">Configuration: OK</div>'
      : '<div class="bad">Missing: '+(d.missing_variables||[]).join(', ')+'</div>';
    $('status').innerHTML=botState+cfg;
  }catch(e){}
}
async function loadLogs(){try{$('logs').textContent=(await api('/api/logs?lines=120')).logs||''}catch(e){$('logs').textContent=e.message}}
async function logout(){try{await api('/api/logout',{method:'POST'})}finally{showLogin()}}
showLogin();
</script></body></html>"""


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("cf-connecting-ip")
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def runtime_missing() -> list[str]:
    return [name for name in REQUIRED_RUNTIME_VARS if not os.getenv(name, "").strip()]


def is_authenticated(request: Request) -> bool:
    if not ADMIN_PASSWORD:
        return False
    token = request.cookies.get("self_admin")
    if not token:
        return False
    expires_at = active_sessions.get(token, 0)
    if expires_at <= time.time():
        active_sessions.pop(token, None)
        return False
    return True


def require_auth(request: Request) -> None:
    if not is_authenticated(request):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Unauthorized")


async def control(command: dict[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(CONTROL_HOST, CONTROL_PORT),
        timeout=5,
    )
    writer.write((json.dumps(command, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()
    raw = await asyncio.wait_for(reader.readline(), timeout=15)
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return json.loads(raw.decode("utf-8"))


async def start_bot_async() -> dict[str, Any]:
    global bot_process
    if bot_process and bot_process.returncode is None:
        return {"ok": True, "already_running": True}

    missing = runtime_missing()
    if missing:
        return {"ok": False, "error": "Missing runtime variables: " + ", ".join(missing)}

    BOT_LOG.parent.mkdir(parents=True, exist_ok=True)

    if BOT_LOG.exists() and BOT_LOG.stat().st_size > MAX_LOG_BYTES:
        rotated = BOT_LOG.with_name("bot.log.1")
        try:
            rotated.unlink(missing_ok=True)
            BOT_LOG.replace(rotated)
        except OSError:
            BOT_LOG.write_bytes(b"")
    log = BOT_LOG.open("ab")
    try:
        bot_process = await asyncio.create_subprocess_exec(
            "python", "bot.py",
            cwd=str(BASE_DIR),
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
    finally:
        log.close()
    return {"ok": True}


async def stop_bot() -> dict[str, Any]:
    global bot_process
    if not bot_process or bot_process.returncode is not None:
        return {"ok": True, "already_stopped": True}
    bot_process.terminate()
    try:
        await asyncio.wait_for(bot_process.wait(), 8)
    except asyncio.TimeoutError:
        bot_process.kill()
        await bot_process.wait()
    return {"ok": True}


async def supervisor() -> None:
    while True:
        try:
            await asyncio.sleep(20)
            if AUTO_START and not runtime_missing():
                if bot_process is None or bot_process.returncode is not None:
                    await start_bot_async()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            print(f"bot supervisor error: {exc}")


@app.on_event("startup")
async def startup() -> None:
    global supervisor_task
    if AUTO_START and not runtime_missing():
        await start_bot_async()
    supervisor_task = asyncio.create_task(supervisor())


@app.on_event("shutdown")
async def shutdown() -> None:
    global supervisor_task
    if supervisor_task:
        supervisor_task.cancel()
        try:
            await supervisor_task
        except asyncio.CancelledError:
            pass
        supervisor_task = None
    await stop_bot()


@app.get("/health")
async def health():
    running = bool(bot_process and bot_process.returncode is None)
    return JSONResponse({"ok": True, "service": "SElf", "bot_running": running})


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML, headers={"Cache-Control": "no-store"})


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    password = str(body.get("password", ""))
    ip = client_ip(request)
    count, until = login_failures.get(ip, (0, 0.0))
    if until > time.time():
        return JSONResponse({"ok": False, "error": "Too many attempts"}, status_code=429)

    if not ADMIN_PASSWORD or not hmac.compare_digest(password, ADMIN_PASSWORD):
        count += 1
        if count >= 5:
            login_failures[ip] = (0, time.time() + 300)
        else:
            login_failures[ip] = (count, time.time())
        return JSONResponse({"ok": False, "error": "Wrong password"}, status_code=401)

    login_failures.pop(ip, None)
    token = secrets.token_urlsafe(32)
    active_sessions[token] = time.time() + SESSION_TTL_SECONDS

    response = JSONResponse({"ok": True})
    response.set_cookie(
        "self_admin", token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=86400 * 7,
        path="/",
    )
    return response


@app.post("/api/logout")
async def logout(request: Request):
    token = request.cookies.get("self_admin")
    if token:
        active_sessions.pop(token, None)
    response = JSONResponse({"ok": True})
    response.delete_cookie("self_admin", path="/")
    return response


@app.get("/api/status")
async def status(request: Request):
    require_auth(request)
    running = bool(bot_process and bot_process.returncode is None)
    missing = runtime_missing()
    return {
        "ok": True,
        "bot_running": running,
        "uptime_seconds": int(time.time() - started_at),
        "runtime_ready": not missing,
        "missing_variables": missing,
        "api_configured": bool(os.getenv("API_ID") and os.getenv("API_HASH")),
        "session_configured": bool(os.getenv("SESSION_STRING")),
    }


@app.post("/api/start")
async def api_start(request: Request):
    require_auth(request)
    result = await start_bot_async()
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.post("/api/stop")
async def api_stop(request: Request):
    require_auth(request)
    return await stop_bot()


@app.post("/api/restart")
async def api_restart(request: Request):
    require_auth(request)
    await stop_bot()
    result = await start_bot_async()
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.post("/api/send")
async def api_send(request: Request):
    require_auth(request)
    body = await request.json()
    chat_ids = body.get("chat_ids") or []
    if isinstance(chat_ids, str):
        chat_ids = [chat_ids]
    chat_ids = [item for item in chat_ids if str(item).strip()][:MAX_BATCH_TARGETS]
    text = str(body.get("text", "")).strip()
    if not chat_ids or not text:
        return JSONResponse({"ok": False, "error": "chat_ids and text are required"}, status_code=400)
    if not bot_process or bot_process.returncode is not None:
        return JSONResponse({"ok": False, "error": "Bot is not running"}, status_code=409)
    return await control({"op": "send", "chat_id": chat_ids, "text": text})


@app.post("/api/block")
async def api_block(request: Request):
    require_auth(request)
    body = await request.json()
    ids = body.get("chat_ids") or []
    if isinstance(ids, str):
        ids = [ids]
    ids = [item for item in ids if str(item).strip()][:MAX_BATCH_TARGETS]
    return await control({"op": "block", "chat_id": ids})


@app.post("/api/unblock")
async def api_unblock(request: Request):
    require_auth(request)
    body = await request.json()
    ids = body.get("chat_ids") or []
    if isinstance(ids, str):
        ids = [ids]
    ids = [item for item in ids if str(item).strip()][:MAX_BATCH_TARGETS]
    return await control({"op": "unblock", "chat_id": ids})


@app.get("/api/logs")
async def api_logs(request: Request, lines: int = 120):
    require_auth(request)
    lines = max(1, min(lines, 300))
    if not BOT_LOG.exists():
        return {"ok": True, "logs": ""}
    try:
        with BOT_LOG.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 64 * 1024))
            chunk = fh.read().decode("utf-8", errors="replace")
        data = chunk.splitlines()
        return {"ok": True, "logs": "\n".join(data[-lines:])}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

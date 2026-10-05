from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from telegram import Bot
from telegram.error import RetryAfter, TelegramError
import uvicorn

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("BOT_DATA_DIR", str(BASE_DIR / "data")))
LOG_DIR = BASE_DIR / "logs"
BOT_LOG = LOG_DIR / "main_bot.log"
USERS_DB = DATA_DIR / "users.db"
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
AUTO_START = os.getenv("AUTO_START_BOT", "true").lower() not in {"0", "false", "no", "off"}
REQUIRED = ("BOT_TOKEN", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "OWNER_ID", "ADMIN_PASSWORD")
MAX_LOG_BYTES = 5 * 1024 * 1024
MAX_TARGETS = 50
SESSION_TTL = 7 * 24 * 60 * 60

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
main_process: asyncio.subprocess.Process | None = None
supervisor_task: asyncio.Task | None = None
sessions: dict[str, float] = {}
login_failures: dict[str, tuple[int, float]] = {}
started_at = time.time()

HTML = r"""<!doctype html><html lang="fa" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>SElf Control</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#0b0f14;color:#e9eef5;font-family:system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:900px;margin:auto;padding:20px}section{background:#111821;border:1px solid #263241;border-radius:18px;padding:16px;margin:12px 0}
input,textarea,button{width:100%;padding:11px;border-radius:12px;border:1px solid #334155;background:#0d131b;color:#fff;font:inherit}button{cursor:pointer;margin-top:8px}.row{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}pre{white-space:pre-wrap;max-height:380px;overflow:auto}.muted{color:#94a3b8}.ok{color:#86efac}.bad{color:#fca5a5}.hidden{display:none}@media(max-width:650px){.row,.grid{grid-template-columns:1fr}}
</style></head><body><main>
<section id="login"><h1>SElf Control</h1><p class="muted">پنل سبک و بدون polling خودکار</p><input id="password" type="password" placeholder="رمز پنل"><button onclick="login()">ورود</button><div id="msg"></div></section>
<div id="panel" class="hidden">
<section><h1>SElf Control</h1><div id="status" class="muted">—</div><div class="row"><button onclick="act('/api/start')">Start</button><button onclick="act('/api/stop')">Stop</button><button onclick="act('/api/restart')">Restart</button></div><button onclick="loadStatus()">Refresh</button></section>
<section><h2>ارسال پیام</h2><div class="grid"><input id="target" placeholder="Chat ID یا @username"><input id="target2" placeholder="مقصد دیگر"></div><textarea id="text" placeholder="متن پیام"></textarea><button onclick="sendMsg()">Send</button></section>
<section><h2>کاربر</h2><input id="users" placeholder="User ID ها با ,"><div class="row"><button onclick="userAct('/api/block')">Block</button><button onclick="userAct('/api/unblock')">Unblock</button><button onclick="logout()">Logout</button></div></section>
<section><h2>لاگ</h2><button onclick="loadLogs()">نمایش لاگ</button><pre id="logs">بدون بارگذاری خودکار</pre></section>
</div></main><script>
const $=x=>document.getElementById(x);
async function api(p,o={}){const r=await fetch(p,{...o,headers:{'Content-Type':'application/json',...(o.headers||{})}});const d=await r.json().catch(()=>({}));if(r.status===401)showLogin();if(!r.ok)throw Error(d.detail||d.error||'Request failed');return d}
function showPanel(){$('login').classList.add('hidden');$('panel').classList.remove('hidden');loadStatus()}function showLogin(){$('panel').classList.add('hidden');$('login').classList.remove('hidden')}
async function login(){try{await api('/api/login',{method:'POST',body:JSON.stringify({password:$('password').value})});$('password').value='';showPanel()}catch(e){$('msg').textContent=e.message}}
async function act(p){try{alert(JSON.stringify(await api(p,{method:'POST'}),null,2));loadStatus()}catch(e){alert(e.message)}}
async function sendMsg(){const v=[$('target').value,$('target2').value].filter(Boolean).join(',');const chat_ids=v.split(',').map(x=>x.trim()).filter(Boolean);try{alert(JSON.stringify(await api('/api/send',{method:'POST',body:JSON.stringify({chat_ids,text:$('text').value})}),null,2))}catch(e){alert(e.message)}}
async function userAct(p){const user_ids=$('users').value.split(',').map(x=>x.trim()).filter(Boolean);try{alert(JSON.stringify(await api(p,{method:'POST',body:JSON.stringify({user_ids})}),null,2))}catch(e){alert(e.message)}}
async function loadStatus(){try{const d=await api('/api/status');$('status').innerHTML=(d.main_bot_running?'<span class="ok">● running</span>':'<span class="bad">● stopped</span>')+'<div class="muted">Self-bots: '+d.running_selfbots+'/'+d.registered_selfbots+'</div>'+(d.runtime_ready?'':'<div class="bad">Missing: '+d.missing_variables.join(', ')+'</div>')}catch(e){}}
async function loadLogs(){try{$('logs').textContent=(await api('/api/logs')).logs||''}catch(e){$('logs').textContent=e.message}}
async function logout(){try{await api('/api/logout',{method:'POST'})}finally{showLogin()}}showLogin();
</script></body></html>"""

def missing_vars() -> list[str]:
    return [x for x in REQUIRED if not os.getenv(x, "").strip()]

def client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")

def alive() -> bool:
    return bool(main_process and main_process.returncode is None)

def auth(request: Request) -> None:
    token = request.cookies.get("__Host-self_admin")
    if not token or sessions.get(token, 0) <= time.time():
        sessions.pop(token, None)
        raise HTTPException(401, "Unauthorized")

def rotate_log() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if BOT_LOG.exists() and BOT_LOG.stat().st_size > MAX_LOG_BYTES:
        old = LOG_DIR / "main_bot.log.1"
        try: old.unlink(missing_ok=True)
        except OSError: pass
        try: BOT_LOG.replace(old)
        except OSError: BOT_LOG.write_bytes(b"")

async def start_bot() -> dict[str, Any]:
    global main_process
    if alive(): return {"ok": True, "already_running": True, "pid": main_process.pid}
    missing = missing_vars()
    if missing: return {"ok": False, "error": "Missing runtime variables", "missing_variables": missing}
    DATA_DIR.mkdir(parents=True, exist_ok=True); rotate_log()
    fh = BOT_LOG.open("ab")
    try:
        main_process = await asyncio.create_subprocess_exec("python","-u","main_bot.py",cwd=str(BASE_DIR),stdout=fh,stderr=asyncio.subprocess.STDOUT,start_new_session=True)
    finally: fh.close()
    return {"ok": True, "pid": main_process.pid}

async def stop_bot() -> dict[str, Any]:
    global main_process
    if not alive(): main_process = None; return {"ok": True, "already_stopped": True}
    try:
        import psutil
        parent = psutil.Process(main_process.pid); children = parent.children(recursive=True)
        for p in children+[parent]:
            try: p.terminate()
            except psutil.Error: pass
        await asyncio.to_thread(psutil.wait_procs, children+[parent], timeout=8)
        for p in children+[parent]:
            try:
                if p.is_running(): p.kill()
            except psutil.Error: pass
    except Exception:
        try:
            main_process.terminate(); await asyncio.wait_for(main_process.wait(), 8)
        except Exception:
            try: main_process.kill(); await main_process.wait()
            except Exception: pass
    main_process = None
    return {"ok": True, "stopped": True}

async def supervisor() -> None:
    while True:
        try:
            await asyncio.sleep(20)
            if AUTO_START and not missing_vars() and not alive(): await start_bot()
        except asyncio.CancelledError: return
        except Exception as exc: print("supervisor:", exc)

@app.on_event("startup")
async def startup() -> None:
    global supervisor_task
    if AUTO_START and not missing_vars(): await start_bot()
    supervisor_task = asyncio.create_task(supervisor())

@app.on_event("shutdown")
async def shutdown() -> None:
    global supervisor_task
    if supervisor_task:
        supervisor_task.cancel()
        try: await supervisor_task
        except asyncio.CancelledError: pass
    await stop_bot()

@app.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "SElf", "main_bot_running": alive()}

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(HTML, headers={"Cache-Control":"no-store"})

@app.post("/api/login")
async def login(request: Request):
    body=await request.json(); password=str(body.get("password","")); ip=client_ip(request); now=time.time()
    count, until=login_failures.get(ip,(0,0))
    if until>now: raise HTTPException(429,"Too many attempts")
    if not ADMIN_PASSWORD or not hmac.compare_digest(password,ADMIN_PASSWORD):
        count+=1; login_failures[ip]=((0,now+300) if count>=5 else (count,now)); raise HTTPException(401,"Wrong password")
    login_failures.pop(ip,None); token=secrets.token_urlsafe(32); sessions[token]=now+SESSION_TTL
    response=JSONResponse({"ok":True}); response.set_cookie("__Host-self_admin",token,httponly=True,secure=True,samesite="lax",max_age=SESSION_TTL,path="/"); return response

@app.post("/api/logout")
async def logout(request: Request):
    token=request.cookies.get("__Host-self_admin")
    if token: sessions.pop(token,None)
    response=JSONResponse({"ok":True}); response.delete_cookie("__Host-self_admin",path="/"); return response

def counts() -> tuple[int,int]:
    if not USERS_DB.exists(): return 0,0
    try:
        with sqlite3.connect(USERS_DB,timeout=5) as db:
            row=db.execute("SELECT COUNT(*), COALESCE(SUM(self_enabled=1),0) FROM users").fetchone()
            return int(row[1] or 0),int(row[0] or 0)
    except Exception: return 0,0

@app.get("/api/status")
async def status(request: Request):
    auth(request); running,registered=counts(); miss=missing_vars()
    return {"ok":True,"main_bot_running":alive(),"pid":main_process.pid if alive() else None,"runtime_ready":not miss,"missing_variables":miss,"running_selfbots":running,"registered_selfbots":registered,"uptime_seconds":int(time.time()-started_at)}

@app.post("/api/start")
async def api_start(request: Request):
    auth(request); result=await start_bot(); return JSONResponse(result,status_code=200 if result["ok"] else 400)

@app.post("/api/stop")
async def api_stop(request: Request):
    auth(request); return await stop_bot()

@app.post("/api/restart")
async def api_restart(request: Request):
    auth(request); await stop_bot(); result=await start_bot(); return JSONResponse(result,status_code=200 if result["ok"] else 400)

def targets(raw: Any) -> list[str]:
    if isinstance(raw,str): raw=raw.split(",")
    if not isinstance(raw,list): return []
    return list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))[:MAX_TARGETS]

@app.post("/api/send")
async def api_send(request: Request):
    auth(request); body=await request.json(); target_list=targets(body.get("chat_ids")); message=str(body.get("text","")).strip()
    if not target_list or not message: raise HTTPException(400,"chat_ids and text are required")
    if not BOT_TOKEN: raise HTTPException(503,"BOT_TOKEN is not configured")
    sent=[]; failed=[]
    async with Bot(BOT_TOKEN) as bot:
        for target in target_list:
            try:
                await bot.send_message(chat_id=target,text=message); sent.append(target)
            except RetryAfter as exc:
                await asyncio.sleep(max(1,int(exc.retry_after)))
                try: await bot.send_message(chat_id=target,text=message); sent.append(target)
                except TelegramError as retry_exc: failed.append({"target":target,"error":str(retry_exc)[:300]})
            except TelegramError as exc: failed.append({"target":target,"error":str(exc)[:300]})
    return {"ok":not failed,"sent":sent,"failed":failed}

def user_state(user_ids: list[str], active: int) -> dict[str,Any]:
    if not USERS_DB.exists(): return {"ok":False,"updated":[],"not_found":user_ids}
    updated=[]; not_found=[]
    with sqlite3.connect(USERS_DB,timeout=10) as db:
        for raw in user_ids:
            try: uid=int(raw)
            except ValueError: not_found.append(raw); continue
            cur=db.execute("UPDATE users SET is_active=? WHERE user_id=?",(active,uid))
            (updated if cur.rowcount else not_found).append(uid if cur.rowcount else str(uid))
        db.commit()
    return {"ok":True,"updated":updated,"not_found":not_found,"state":"active" if active else "blocked"}

@app.post("/api/block")
async def api_block(request: Request):
    auth(request); ids=targets((await request.json()).get("user_ids"))
    if not ids: raise HTTPException(400,"user_ids are required")
    return user_state(ids,0)

@app.post("/api/unblock")
async def api_unblock(request: Request):
    auth(request); ids=targets((await request.json()).get("user_ids"))
    if not ids: raise HTTPException(400,"user_ids are required")
    return user_state(ids,1)

@app.get("/api/logs")
async def api_logs(request: Request, lines: int=160):
    auth(request); lines=max(1,min(lines,400))
    if not BOT_LOG.exists(): return {"ok":True,"logs":""}
    with BOT_LOG.open("rb") as fh:
        fh.seek(0,2); size=fh.tell(); fh.seek(max(0,size-128*1024)); log_text=fh.read().decode("utf-8","replace")
    return {"ok":True,"logs":"\n".join(log_text.splitlines()[-lines:])}

@app.get("/api/version")
async def version(request: Request):
    auth(request); return {"ok":True,"release":"2.12.3","runtime":"cloudflare-container"}

if __name__ == "__main__":
    uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT","8080")),proxy_headers=True)

import asyncio
import json
import os
import re
import signal
from pathlib import Path


CONTROL_HOST = os.getenv("CONTROL_HOST", "127.0.0.1")
CONTROL_PORT = int(os.getenv("CONTROL_PORT", "8765"))
BOT_SOURCE = Path(__file__).with_name("bot.py")


def prepare_runtime_data() -> None:
    Path("data").mkdir(parents=True, exist_ok=True)
    Path("data/action").mkdir(parents=True, exist_ok=True)
    Path("downloads").mkdir(parents=True, exist_ok=True)
    Path("logs").mkdir(parents=True, exist_ok=True)

    defaults = {
        "TimeName.txt": "off",
        "TimeBio.txt": "off",
        "Font.txt": "Font1",
        "italic.txt": "off",
        "part.txt": "off",
        "bold.txt": "off",
        "link.txt": "off",
        "underline.txt": "off",
        "Enemy.txt": "",
        "Mute.txt": "",
        "action/playing.txt": "off",
        "action/typing.txt": "off",
        "action/RECORD_VIDEO.txt": "off",
        "action/CHOOSE_STICKER.txt": "off",
        "action/UPLOAD_VIDEO.txt": "off",
        "action/UPLOAD_DOCUMENT.txt": "off",
        "action/UPLOAD_AUDIO.txt": "off",
        "action/SPEAKING.txt": "off",
    }

    for relative, value in defaults.items():
        path = Path("data") / relative
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")


async def control_reply(writer, payload: dict) -> None:
    writer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    await writer.drain()
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


def patch_source(source: str) -> str:
    api_id = os.getenv("API_ID", "").strip()
    api_hash = os.getenv("API_HASH", "").strip()
    session_string = os.getenv("SESSION_STRING", "")
    session_name = os.getenv("SESSION_NAME", "my_account").strip() or "my_account"

    if not api_id.isdigit():
        raise RuntimeError("API_ID must be a numeric Telegram API ID")
    if not api_hash:
        raise RuntimeError("API_HASH is missing")
    if not session_string:
        raise RuntimeError("SESSION_STRING is missing")

    source = re.sub(
        r"(?m)^api_id\s*=.*$",
        f"api_id = {int(api_id)}",
        source,
        count=1,
    )
    source = re.sub(
        r'(?m)^api_hash\s*=.*$',
        f"api_hash = {api_hash!r}",
        source,
        count=1,
    )
    source = source.replace(
        'bot = Client("my_account", api_id=api_id, api_hash=api_hash)',
        f'bot = Client({session_name!r}, api_id=api_id, api_hash=api_hash, session_string={session_string!r})',
        1,
    )

    old_tail = "print('bot is runed')\nscheduler.start()\nbot.run()"
    new_tail = '''
async def _control_client(reader, writer):
    try:
        raw = await reader.readline()
        if not raw:
            return
        command = json.loads(raw.decode("utf-8"))
        op = command.get("op")
        if op == "status":
            await control_reply(writer, {"ok": True, "connected": bool(bot.is_connected)})
            return
        if op == "send":
            target = command.get("chat_id")
            message = command.get("text", "")
            if not target or not message:
                await control_reply(writer, {"ok": False, "error": "chat_id and text are required"})
                return
            targets = target if isinstance(target, list) else [target]
            sent = 0
            for item in targets:
                try:
                    await bot.send_message(item, message)
                    sent += 1
                except Exception:
                    pass
            await control_reply(writer, {"ok": sent == len(targets), "sent": sent, "total": len(targets)})
            return
        if op == "block":
            target = command.get("chat_id")
            targets = target if isinstance(target, list) else [target]
            done = 0
            for item in targets:
                try:
                    await bot.block_user(item)
                    done += 1
                except Exception:
                    pass
            await control_reply(writer, {"ok": done == len(targets), "done": done, "total": len(targets)})
            return
        if op == "unblock":
            target = command.get("chat_id")
            targets = target if isinstance(target, list) else [target]
            done = 0
            for item in targets:
                try:
                    await bot.unblock_user(item)
                    done += 1
                except Exception:
                    pass
            await control_reply(writer, {"ok": done == len(targets), "done": done, "total": len(targets)})
            return
        await control_reply(writer, {"ok": False, "error": "unknown operation"})
    except Exception as exc:
        try:
            await control_reply(writer, {"ok": False, "error": str(exc)})
        except Exception:
            pass


async def _main():
    prepare_runtime_data()
    server = await asyncio.start_server(_control_client, CONTROL_HOST, CONTROL_PORT)

    scheduler.start()
    await bot.start()

    try:
        async with server:
            await asyncio.gather(
                server.serve_forever(),
                asyncio.Event().wait(),
            )
    finally:
        server.close()
        await server.wait_closed()
        if bot.is_connected:
            await bot.stop()

'''
if __name__ == "__main__":
    source = BOT_SOURCE.read_text(encoding="utf-8")
    source = patch_source(source)
    namespace = {"__name__": "__self_original__"}
    exec(compile(source, str(BOT_SOURCE), "exec"), namespace, namespace)
    asyncio.run(namespace["_main"]())

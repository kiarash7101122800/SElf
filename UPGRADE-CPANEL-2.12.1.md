# ارتقا روی cPanel از 2.12.0 به 2.12.1

مسیر فعلی پروژه:

```bash
/home/ravoxste/telegram_selfbot_betting
```

## 1. توقف و بکاپ

```bash
cd /home/ravoxste/telegram_selfbot_betting
./keep_alive.sh stop
mkdir -p /home/ravoxste/backups
tar -czf /home/ravoxste/backups/selfbot-before-2.12.1-$(date +%Y%m%d-%H%M%S).tar.gz data sessions .env
```

## 2. جایگزینی فایل‌ها

فایل ZIP نسخه 2.12.1 را در `/home/ravoxste/` آپلود و همان‌جا Extract کنید. پوشه `telegram_selfbot_betting` با فایل‌های جدید ادغام می‌شود.

این موارد را پاک یا جایگزین نکنید:

- `.env`
- `data/`
- `sessions/`
- `.venv/`

فایل ZIP نسخه جدید هیچ‌کدام از این اطلاعات خصوصی را در خود ندارد.

## 3. کنترل و راه‌اندازی

```bash
cd /home/ravoxste/telegram_selfbot_betting
source .venv/bin/activate
python -m pip check
python -m py_compile main_bot.py
./keep_alive.sh restart
sleep 5
./keep_alive.sh status
tail -n 100 logs/main.log
```

مهاجرت دیتابیس بازی‌ها در اولین اجرای `main_bot.py` به‌صورت خودکار انجام می‌شود و دستور SQL دستی لازم نیست.

## تنظیمات اختیاری

می‌توان این موارد را به `.env` اضافه کرد:

```env
BETTING_GAME_TTL_MINUTES=15
BETTING_CLEANUP_INTERVAL=60
BETTING_MAX_STAKE=1000000
BETTING_MAX_OPEN_GAMES_PER_USER=3
BOT_TIMEZONE=Asia/Tehran
```

برای دیدن ده بازی آخر توسط مدیر:

```text
/gamehistory
```

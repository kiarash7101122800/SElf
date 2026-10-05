# ارتقا cPanel به نسخه 2.12.2

مسیر نصب فعلی:

```bash
/home/ravoxste/telegram_selfbot_betting
```

## ۱. توقف و بکاپ

```bash
cd /home/ravoxste/telegram_selfbot_betting
./keep_alive.sh stop
mkdir -p /home/ravoxste/backups
tar -czf /home/ravoxste/backups/selfbot-before-2.12.2-$(date +%Y%m%d-%H%M%S).tar.gz data sessions .env
```

## ۲. استخراج فایل جدید

فایل ZIP را در `/home/ravoxste/` آپلود کنید، سپس:

```bash
cd /home/ravoxste
unzip -o telegram-selfbot-v2.12.2-compact-helper-betting-hardening.zip
```

فایل‌های `.env`، `data/`، `sessions/` و `.venv/` داخل بسته نیستند و حفظ می‌شوند.

## ۳. بررسی و اجرا

```bash
cd /home/ravoxste/telegram_selfbot_betting
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m py_compile main_bot.py admin_center.py
./keep_alive.sh restart
sleep 5
./keep_alive.sh status
tail -n 100 logs/main.log
```

مهاجرت SQLite در اولین اجرا خودکار است و SQL دستی لازم نیست.

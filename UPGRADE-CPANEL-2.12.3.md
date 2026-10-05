# ارتقا روی cPanel به 2.12.3

فایل ZIP را در `/home/ravoxste/` آپلود کنید.

```bash
cd /home/ravoxste/telegram_selfbot_betting
./keep_alive.sh stop

mkdir -p /home/ravoxste/backups
tar -czf /home/ravoxste/backups/pre-2.12.3-$(date +%Y%m%d-%H%M%S).tar.gz \
  data sessions .env

cd /home/ravoxste
unzip -o telegram-selfbot-v2.12.3-cloud-media-stability.zip

cd /home/ravoxste/telegram_selfbot_betting
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m compileall -q .
bash -n keep_alive.sh
./keep_alive.sh restart
sleep 5
./keep_alive.sh status
tail -n 100 logs/main.log
```

Cron Job همان یک خط قبلی است:

```cron
* * * * * /bin/bash /home/ravoxste/telegram_selfbot_betting/keep_alive.sh watch >/dev/null 2>&1
```

در اولین اتصال هر سلف، رسانه‌های قدیمی محلی به Saved Messages منتقل و سپس از هاست حذف می‌شوند. `accounts.db` و پوشه قدیمی `deleted_selfbots` در شروع ربات اصلی حذف می‌شوند.

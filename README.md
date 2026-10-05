# SElf — Cloudflare Container Edition v2.12.3

این نسخه فایل‌های پروژه‌ی ارسالی telegram_selfbot_betting را به‌عنوان هسته وارد می‌کند و لایه‌ی Cloudflare را روی همان هسته قرار می‌دهد.

## معماری

Cloudflare Worker → Durable Object → Cloudflare Container → FastAPI → main_bot.py → self_bot.py / helper_bot.py

## تغییرات

- هسته‌ی کامل پروژه شامل ربات مدیریت، سلف‌ها، هلپر، امکانات پیشرفته، صف ارسال، خزانه سشن و دیتابیس‌ها وارد شده است.
- اجرای اصلی از bot.py قدیمی به main_bot.py منتقل شده است.
- پنل وب سبک با Start / Stop / Restart، وضعیت، لاگ، ارسال پیام و Block / Unblock یکپارچه شده است.
- فایل‌های تست نسخه 2.12.3 حفظ شده‌اند.
- Cloudflare Worker و Durable Object برای اجرای Container باقی مانده‌اند.
- هیچ polling دائمی مرورگر برای پنل وجود ندارد.

## متغیرهای اصلی

BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH, OWNER_ID, ADMIN_PASSWORD

متغیرهای اختیاری در .env.example آمده‌اند. مقدار واقعی Secret را در GitHub commit نکنید؛ در Cloudflare Workers & Pages → Settings → Variables & Secrets قرار دهید.

## دامنه

https://self.kiarash.cfd

## CI

TypeScript check، تولید Wrangler types، py_compile، اجرای pytest و Docker build برای linux/amd64 در CI انجام می‌شود.

برای Cloudflare Containers، Workers Paid لازم است.

# SElf — Cloudflare Container Edition

این نسخه از **SElf** از هسته‌ی اصلی اولیه‌ی پروژه شروع شده است: همان `bot.py` و `reloads.py` اولیه، با یک لایه‌ی Cloudflare-native برای اجرا و مدیریت.

## معماری

- **Cloudflare Worker** به‌عنوان Edge Gateway
- **Durable Object + Container** برای اجرای واقعی Python/Pyrogram
- **FastAPI سبک** برای پنل مدیریت
- **GitHub + Workers Builds** برای Deploy خودکار
- **Custom Domain:** `https://self.kiarash.cfd`
- **بدون polling خودکار در پنل** برای کم کردن مصرف request
- پنل بدون React/Vue/Next و بدون asset خارجی
- اجرای مستقیم `bot.py`؛ بدون runtime source patch

> اجرای دائم Pyrogram در Worker معمولی انجام نمی‌شود؛ خود Telegram client داخل Container اجرا می‌شود. Containers روی Workers Paid هستند.

## متغیرهای Runtime

**اطلاعات حساس را داخل GitHub قرار ندهید. حتی اگر Repository را Private کنید.**  
`SESSION_STRING`، `API_HASH` و `ADMIN_PASSWORD` باید فقط در Cloudflare Secrets قرار بگیرند. ریپوی Private برای کد عالی است، اما جای Secret Store نیست.

در Cloudflare Workers & Pages → سرویس → Settings → Variables & Secrets این موارد را به‌صورت Secret اضافه کنید:

- `API_ID`
- `API_HASH`
- `OWNER_ID`
- `ADMIN_PASSWORD`
- `SESSION_STRING`

اختیاری:

- `SESSION_NAME=my_account`
- `AUTO_START_BOT=true`

`PORT` را دستی تنظیم نکنید.

## Deploy با GitHub

1. Workers & Pages را باز کنید.
2. Create application → Import a repository را بزنید.
3. GitHub را انتخاب کنید.
4. ریپوی `kiarash7101122800/SElf` را انتخاب کنید.
5. Branch را روی `main` بگذارید.
6. Build command را خالی بگذارید.
7. Deploy command را روی `npx wrangler deploy` بگذارید.
8. Save and Deploy را بزنید.

Cloudflare Workers Builds با هر push روی `main` دوباره Build/Deploy می‌کند. برای Workerهایی که Container دارند باید production از `wrangler deploy` استفاده کند.

## دامنه

دامنه‌ی `self.kiarash.cfd` در `wrangler.jsonc` تعریف شده است. Zone `kiarash.cfd` باید در Cloudflare فعال باشد.

## پنل مدیریت

آدرس اصلی همان دامنه است. پنل این امکانات را دارد:

- ورود با رمز
- Start / Stop / Restart
- نمایش وضعیت Bot
- ارسال پیام به یک یا چند Chat ID/Username
- Block / Unblock کاربر
- مشاهده‌ی آخرین لاگ‌ها
- Logout امن با HttpOnly cookie

برای کاهش request، پنل status را خودکار و مداوم refresh نمی‌کند.

## نگه‌داشتن Bot

Durable Object با یک Alarm داخلی حدوداً هر ساعت Container را warm می‌کند تا به خاطر inactivity خاموش نشود. این نگهداری داخل معماری Cloudflare انجام می‌شود و پنل مرورگر هیچ polling دوره‌ای انجام نمی‌دهد.

## پایداری Session

Container filesystem به‌صورت پیش‌فرض موقت است. برای جلوگیری از از دست رفتن Session، Durable Object هر 24 ساعت یک snapshot از filesystem می‌گیرد و handle آن را در storage خودش نگه می‌دارد. Snapshotها فقط با Docker image مربوط به خودشان قابل restore هستند.

## Health

`/edge-health` فقط Worker را بررسی می‌کند و Container را بیدار نمی‌کند.

`/health` سلامت خود Container را بررسی می‌کند.

## Cloudflare Request Optimization

- هیچ status polling دائمی در Browser وجود ندارد.
- هیچ asset framework سنگین یا CDN خارجی برای پنل استفاده نشده است.
- درخواست‌های مدیریتی فقط با کلیک کاربر ارسال می‌شوند.
- Heartbeat داخلی هر ساعت یک بار است.
- APIهای Worker مستقیماً به یک Durable Object ثابت route می‌شوند تا Container اضافه ساخته نشود.
- Telegram API traffic از داخل Container خارج می‌شود و برای هر پیام وب یک Worker request مصنوعی ساخته نمی‌شود.

## Session String

`SESSION_STRING` را فقط در Cloudflare Secrets قرار دهید و هرگز داخل GitHub commit نکنید.

## توجه

برای اجرای واقعی این معماری، **Workers Paid** و قابلیت **Cloudflare Containers** لازم است. این نسخه برای اجرای سریع‌تر Bot، Instance نوع `standard-2` را انتخاب می‌کند.

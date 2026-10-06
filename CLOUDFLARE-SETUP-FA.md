# راه‌اندازی SElf روی Cloudflare

این راهنما مخصوص همین پروژه است.

## 1) پلن Cloudflare

این پروژه از Cloudflare Containers استفاده می‌کند؛ بنابراین حساب باید **Workers Paid** داشته باشد.

در داشبورد Cloudflare:
Workers & Pages → بخش Billing/Workers → فعال‌کردن Workers Paid

بعد از فعال‌شدن، برگردید به Workers & Pages.

## 2) اتصال GitHub

Workers & Pages → Create application → Get started در بخش **Import a repository**

GitHub را انتخاب کنید و دسترسی Cloudflare به GitHub را تأیید کنید.

Repository:
`kiarash7101122800/SElf`

Branch:
`main`

Project/Worker name:
`self-cloudflare`

Root directory:
`/`

Build command:
`npm install`

Deploy command:
`npx wrangler deploy`

سپس Save and Deploy را بزنید.

**نام Worker باید دقیقاً `self-cloudflare` باشد** چون در `wrangler.jsonc` همین نام ثبت شده است.

## 3) Secrets

بعد از ساخته‌شدن Worker:
Workers & Pages → self-cloudflare → Settings → Variables and Secrets → Add

نوع موارد حساس را **Secret** بگذارید.

این 5 مورد لازم‌اند:

- `BOT_TOKEN` = توکن بات اصلی
- `TELEGRAM_API_ID` = API ID تلگرام
- `TELEGRAM_API_HASH` = API Hash تلگرام
- `OWNER_ID` = آیدی عددی مالک
- `ADMIN_PASSWORD` = یک رمز جدید برای پنل وب

مقادیر واقعی را داخل GitHub commit نکنید.

دو متغیر اختیاری:

- `AUTO_START_BOT` = `true`
- `BOT_TIMEZONE` = `Asia/Tehran`

سپس Deploy را بزنید.

## 4) دامنه

در تنظیمات Worker:
Settings → Domains & Routes

اگر `self.kiarash.cfd` به‌صورت Custom Domain نمایش داده شد، کاری نکنید.

اگر نمایش داده نشد:
Add → Custom Domain → `self.kiarash.cfd`

برای این زیردامنه A یا CNAME دستی نسازید؛ Custom Domain رکورد DNS لازم را ایجاد می‌کند.

## 5) آزمایش لبه Cloudflare

آدرس زیر را باز کنید:

`https://self.kiarash.cfd/edge-health`

باید یک پاسخ JSON شبیه این بگیرید:

`{"ok":true,"edge":"cloudflare","service":"self-cloudflare"}`

اگر Custom Domain هنوز ساخته نشده است، اول URL مربوط به `workers.dev` که Cloudflare نشان می‌دهد را آزمایش کنید.

## 6) آزمایش Container

بعد از deploy:
Workers & Pages → self-cloudflare → Logs/Observability

باید شروع Worker و Container را ببینید.

مسیر:
`/health`

باید پاسخ OK بدهد.

## 7) پنل وب

باز کنید:

`https://self.kiarash.cfd/`

رمز همان `ADMIN_PASSWORD` است که در Secrets گذاشته‌اید.

در پنل:
- وضعیت اجرای ربات
- Start
- Stop
- Restart
- ارسال پیام
- Block / Unblock
- لاگ

وجود دارد.

پنل خودش polling دائمی مرورگر ندارد؛ برای کم‌شدن درخواست‌های بی‌دلیل، وضعیت و لاگ فقط با اقدام خود شما بارگذاری می‌شوند.

## 8) راه‌اندازی خود ربات

با `AUTO_START_BOT=true`، بعد از آماده‌شدن متغیرها، برنامه اصلی به‌صورت خودکار شروع می‌شود.

داخل بات اصلی، با حساب مالک:
`/start`

سپس روند ثبت سلف را اجرا کنید.

برای ثبت هر شماره:
1. شماره خودتان را از طریق دکمه ارسال شماره بفرستید.
2. کد ورود تلگرام را وارد کنید.
3. اگر Two-Step Verification فعال است، رمز آن را وارد کنید.
4. برنامه Session را در مسیر مخصوص خودش ذخیره می‌کند.
5. سلف از همان Session اجرا می‌شود.

کد ورود تلگرام و رمز Two-Step را به شخص دیگری ندهید.

## 9) بات هلپر

بات هلپر جدا از بات اصلی است و برای راه‌اندازی اولیه ضروری نیست.

برای فعال‌کردن آن:
1. از BotFather یک بات جدا بسازید.
2. برای آن `/setinline` را فعال کنید.
3. داخل پنل مدیریت بات اصلی، بخش Helper/هلپر را باز کنید.
4. توکن بات هلپر را وارد کنید.
5. برنامه خودش نام کاربری هلپر را بررسی و آن را اجرا می‌کند.

## 10) نکته مهم درباره اجرای دائمی

Worker برای کنترل لبه است و Container برای اجرای Python/Telegram.

Durable Object پروژه برای Container استفاده می‌شود و آلارم داخلی آن برای نگه‌داشتن runtime و ساخت snapshot استفاده می‌شود.

در این پروژه Container روی instance نوع `standard-2` اجرا می‌شود.

## 11) اگر Deploy خطا داد

اول این موارد را بررسی کنید:

- Worker name دقیقاً `self-cloudflare`
- Root directory برابر `/`
- Deploy command برابر `npx wrangler deploy`
- Workers Paid فعال باشد
- همه 5 Secret اصلی ثبت شده باشند
- Dockerfile در ریشه repository باشد
- `wrangler.jsonc` در ریشه repository باشد

سپس از بخش Deployments همان build را دوباره اجرا کنید.

## 12) اگر ربات روشن نشد

در:
Workers & Pages → self-cloudflare → Logs

به دنبال خطاهای:
- Missing runtime variables
- Telegram
- Container did not become ready
- Session
بگردید.

اگر متغیرهای اصلی درست باشند، `/health` را بررسی کنید و بعد Start را از پنل بزنید.

## 13) بعد از هر تغییر کد

هر commit روی `main` باید از مسیر Workers Builds وارد فرایند build/deploy شود.

قبل از تغییرهای بزرگ، وضعیت Deployments و Logs را بررسی کنید.

## 14) چیزهایی که نباید دستی بسازید

برای این پروژه فعلاً:
- DNS A record برای `self.kiarash.cfd` نسازید.
- Container جداگانه در داشبورد نسازید.
- Worker دوم با نام دیگر نسازید.
- متغیرهای حساس را داخل `wrangler.jsonc` یا GitHub commit نکنید.

معماری مورد استفاده:

**GitHub → Workers Builds → Worker → Durable Object → Container → FastAPI / Python → Telegram**


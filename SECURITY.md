# Security

Runtime credentials must not be committed to GitHub, including private repositories.

Required sensitive values:
- BOT_TOKEN
- TELEGRAM_API_HASH
- ADMIN_PASSWORD
- SESSION_ENCRYPTION_KEY when explicitly configured

Telegram session files under sessions/ are encrypted and must be treated as credentials.

Store production values in Cloudflare Workers & Pages → Settings → Variables & Secrets as encrypted secrets.

If a credential has been exposed, rotate or revoke it before production.

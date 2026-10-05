# Security

## Never commit these values

Do not commit real values for:

- SESSION_STRING
- API_HASH
- ADMIN_PASSWORD
- Cloudflare API tokens
- GitHub tokens or private keys

A private GitHub repository is safer than a public repository, but it is still not an appropriate secret store.

For the production deployment, put runtime secrets in **Cloudflare Workers & Pages → Settings → Variables & Secrets** and mark them as encrypted secrets.

If a secret was ever committed, rotate/revoke it and replace it in Cloudflare.

## Safe values

API_ID and OWNER_ID are configuration values rather than authentication secrets, but keeping all runtime configuration in Cloudflare makes deployments reproducible and avoids accidental leakage through commits.

## Session

The SElf client uses SESSION_STRING for unattended startup. The session string represents an authenticated Telegram session and must be treated like a password.

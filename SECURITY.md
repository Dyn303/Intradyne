
# SECURITY (v1.9.0-final)
- **Auth**: All endpoints expect `X-API-Key` header. Use a strong key at your gateway/reverse proxy.
- **Rate limiting**: Token bucket middleware (defaults 120 req / 60s per API key + IP). Override via `RATE_LIMIT_*` env.
- **TLS**: Caddy terminates TLS via Let's Encrypt (set `DOMAIN`, `CADDY_EMAIL` in `.env`). Always put the API behind TLS in prod.
- **Non-root**: Container runs as non-root `appuser`; data persisted at `/app/data`.
- **Secrets**: Store only in `.env` or K8s Secrets. Never commit them.
- **Egress**: Restrict to broker/exchange endpoints where possible.
- **Backups**: Snapshot `/app/data` daily; encrypt offsite backups.
- **Rotation**: Rotate API keys and Telegram/SMTP credentials quarterly.

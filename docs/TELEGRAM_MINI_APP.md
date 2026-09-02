# The dashboard as a Telegram Mini App

Phase 3 of `FULLSTACK_PLAN.md`, and the reason Phase 1 was built as a single
self-contained HTML file rather than a React app: a Mini App *is* a web page,
so the dashboard already is one. Nothing was rewritten. What was added is a way
for the server to know who is asking.

## Why this changes the security model

Read this part before deciding to turn it on.

Until now the dashboard was local-only, and `FULLSTACK_PLAN.md` recommended
keeping it that way: the API key has to live in the browser, and a key in a
browser is readable by anything on the page. That is fine on `localhost`, where
the only reader is you.

**A Mini App cannot be local-only.** Telegram opens a URL in its own browser
and it will not open `localhost` or a self-signed certificate. The URL has to
be public HTTPS. So switching this on moves the API from your laptop to the
open internet, and the question stops being "can someone read the key" and
becomes "who is allowed in".

`initData` answers the first half. Telegram signs a payload with the bot
credential and the page forwards it; the server verifies the signature and the
credential itself never reaches the browser. That is strictly better than the
API key.

It does not answer the second half. A valid signature proves the request came
from *a* Telegram user — any of the billion of them, not you. Verification
alone would authenticate the entire Telegram user base into a trading console.

That is what `TELEGRAM_ALLOWED_USER_IDS` is for, and why an unset allowlist
disables Mini App auth completely rather than defaulting to open. A configured
bot with no allowlist is the genuinely dangerous state — it verifies signatures
perfectly and admits everybody — so the code reports it as "not configured".

**The allowlist is the only thing between the internet and this API.** Right
now the dashboard is read-only, so the worst case is someone reading a paper
portfolio. Phase 2 adds halt and parameter controls behind the same door.

## Setup

### 1. Configure the server

These go in `.env`, which is gitignored. Never commit them.

```
TELEGRAM_BOT_TOKEN=<the bot credential from @BotFather>
TELEGRAM_ALLOWED_USER_IDS=<your numeric Telegram user id>
API_AUTH_REQUIRED=1
```

`TELEGRAM_BOT_TOKEN` is the same variable the outbound alerter uses, so
configuring the Mini App also switches on halt notifications. That is
intentional: both are the same bot.

Optional:

```
TELEGRAM_INITDATA_MAX_AGE_S=3600   # how long a session stays valid
```

`initData` is a bearer credential in every practical sense — a static string
that authenticates whoever holds it — and Telegram never expires one. The
freshness window bounds what a copy lifted from a log or a browser history is
worth. Set it to `0` only if you understand you are removing that bound.

### 2. Find your user id

Message [@userinfobot](https://t.me/userinfobot) on Telegram. It replies with
your numeric id. That number, not your username, goes in the allowlist —
usernames can be changed and reassigned, ids cannot.

### 3. Put the API on public HTTPS

Nothing here is installed yet; pick one.

**Cloudflare Tunnel** is the least effort and needs no account for a throwaway
URL:

```bash
cloudflared tunnel --url http://localhost:8000
```

It prints a `https://<random>.trycloudflare.com` address. The URL changes every
restart, which means re-registering with BotFather each time — fine for trying
it out, annoying as a habit. A named tunnel on a domain you control is the
stable version.

**Tailscale Funnel** is the better long-term answer if you already use
Tailscale, because the URL is stable and the tunnel is tied to your identity
rather than being a public random string.

Either way, the tunnel is doing the job `FULLSTACK_PLAN.md` called for: keeping
the network boundary in something audited instead of hand-rolled.

### 4. Register the Mini App

In [@BotFather](https://t.me/BotFather): `/newapp`, pick the bot, and give it
the tunnel URL. Then `/mybots` → the bot → *Bot Settings* → *Menu Button* → set
it to that URL, so the app opens from the chat's menu button.

Equivalently, from a shell:

```bash
curl -X POST "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setChatMenuButton" \
  -H 'Content-Type: application/json' \
  -d '{"menu_button":{"type":"web_app","text":"Dashboard","web_app":{"url":"https://YOUR-TUNNEL-URL"}}}'
```

### 5. Open it

Tap the menu button in the chat with your bot. The dashboard loads, shows your
username in the header, and polls as usual.

If the page loads but every panel shows a connection error, the signature was
refused. The response deliberately does not say why — distinguishing "forged"
from "valid but not on the list" would tell an attacker whether what they hold
is genuine — so the reason is in the server log under `miniapp_auth_denied`.

## How it works

| | |
|---|---|
| Signing | `HMAC_SHA256(key=HMAC_SHA256(key="WebAppData", msg=<bot credential>), msg=<sorted fields>)` |
| Transport | the page sends `X-Telegram-Init-Data`; the server verifies and never echoes it |
| Fallback | outside Telegram the page still accepts `?key=` for localhost use |
| Refusals | every failure is one flat 401 with no detail; the reason goes to the log |

The page adopts Telegram's theme colours, because the dashboard is dark by
default and a light-theme user would otherwise get near-white text on a
near-white background — a failure that is invisible to anyone testing on a dark
phone.

## Rotating the credential

If the bot credential is ever pasted somewhere it shouldn't be — a chat, a
screenshot, a commit — assume it is compromised and reissue it: `/mybots` in
BotFather → the bot → *API Token* → *Revoke current token*. Anyone holding it
can read everything sent to the bot and post as it, and once the Mini App is
live they can also mint `initData` for arbitrary users, which turns the
allowlist into the only remaining control. Rotating costs one line in `.env`.

## Deliberately not done

- **No WebSocket auth over initData.** Browsers cannot set headers on a
  WebSocket handshake, so it would mean putting the credential in a query
  string. The dashboard polls and does not need `/ws/ticks`.
- **No write actions.** Halt, kill-switch and parameter changes are Phase 2,
  where each needs a confirmation step and a ledger entry. Exposing them
  through a public tunnel before that exists would be the wrong order.
- **No inline mode or bot commands.** The bot sends alerts and hosts the app.
  Making it a conversational interface is a different project.

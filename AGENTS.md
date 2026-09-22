# Intradyne Agent Guide

## Mission
Maintain a Shariah-compliant, spot-only crypto trading backend with default-on guardrails:
- Drawdown (30d): warn ≥15%, halt ≥20%
- Flash-crash: pause if drop >30% in 1h
- Kill-switch: halt if ≥3 breaches in 24h
- VaR safeguard: step down if 1d VaR >5%
- Whitelist-only trading; block unknown assets
- Explainability ledger: append-only with hash chaining

## Standards
- Python: async where possible; ruff/mypy; orjson for JSON; structured JSON logs.
- TypeScript: strict, ESM; zod for input validation.
- Security: no secrets in code; env-driven config; add tests for every fix.
- Size: keep repo <10 GB extracted; large data via on-demand fetchers.

## Commands (typical)
- Install: `pip install -r requirements.txt` / `npm ci`
- Lint/format: `ruff check . && ruff format .` / `eslint .`
- Tests: `pytest -q` / `npm test -s`
- Typecheck: `mypy src` / `tsc -p .`
- Docker: `docker compose up -d --build`
- Backtest: `python -m intradyne.backtest --days 30`

## Definition of Done
- Tests green, lints clean, types pass, Docker build OK
- No unjustified image size bloat
- Docs updated (CHANGELOG, runbooks)
- Code is automatically committed with a descriptive message upon task completion.

## Workflow Automation
- **Context Restore (Auto-Read):** At the start of any new conversation or task, you MUST quietly read `.claude/memory.md` to restore your context before taking any action.
- **Context Save (Auto-Update):** When finishing a session or completing a major milestone, automatically update `.claude/memory.md` with what was accomplished, discoveries, and the next steps.
- **Auto-Commit:** Whenever an AI agent completes a functional change, it MUST automatically stage the changes (`git add .`) and commit them (`git commit -m "..."`) before finishing the turn.
- **Auto-Push:** A Git `post-commit` hook is installed to automatically push every commit to the remote. You do not need to run `git push` manually.

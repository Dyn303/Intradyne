from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx


class AIUnavailable(Exception):
    pass


def _openai_cfg() -> Dict[str, str]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise AIUnavailable("OPENAI_API_KEY not set")
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    return {"key": key, "base": base, "model": model}


async def summarize_guardrails_async(
    records: List[Dict[str, Any]], *, max_chars: int = 800
) -> Dict[str, Any]:
    cfg = _openai_cfg()
    # Build a compact context from recent records to stay within budget
    lines: List[str] = []
    for r in records[-100:]:  # last 100 records max
        t = str(r.get("ts", ""))
        ev = str(r.get("event", ""))
        typ = str(r.get("type", ""))
        act = str(r.get("action", ""))
        parts = [p for p in [t, ev, typ, act] if p]
        if parts:
            lines.append(" | ".join(parts))
    context = "\n".join(lines)[-max_chars:]

    prompt = (
        "You are a risk/compliance assistant. Given recent guardrail ledger "
        "events, produce a concise JSON with: summary (1-2 sentences), counts by type, "
        "and recommended_actions (list). Keep it under 1200 characters and valid JSON.\n\n"
        f"Recent events (newest last):\n{context}\n"
    )

    # Use Chat Completions for broad compatibility
    url = f"{cfg['base']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": "You output only JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 300,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "{}")
    # Best-effort parse; callers handle JSON string if parsing fails
    try:
        import orjson  # lazy import

        parsed = orjson.loads(content)
        if isinstance(parsed, dict):
            return parsed
        return {"raw": content}
    except Exception:
        return {"raw": content}


def ai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


async def explain_decision_async(
    *,
    decision: str,
    reasons: List[str],
    requested: Dict[str, Any],
    final: Dict[str, Any],
    max_chars: int = 600,
) -> str:
    cfg = _openai_cfg()
    prompt = (
        "You are a risk/compliance explainer. Given a trade decision and reasons, "
        "respond with a concise 1-2 sentence plain-English explanation suitable for an alert. "
        "Avoid jargon. Output only text, no JSON.\n\n"
        f"Decision: {decision}\n"
        f"Reasons: {', '.join(reasons)}\n"
        f"Requested order: {requested}\n"
        f"Final order: {final}\n"
    )
    url = f"{cfg['base']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": "You output only concise text."},
            {"role": "user", "content": prompt[:2000]},
        ],
        "temperature": 0.2,
        "max_tokens": 200,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content.strip()[:max_chars]

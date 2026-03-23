"""
Free-Tier AI Model Client
=========================
Provides Groq + Gemini completions with a STRUCTURED TRADING PROMPT
that forces the model to return an actionable BUY decision (not HOLD/SKIP)
when genuine edge exists. Fixes the "always 0.5 confidence HOLD" bug.
"""

import os
import json
import logging
import httpx
import asyncio
import time
from typing import Optional, List, Tuple
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
PROVIDER_TIMEOUT = 10
FREE_TIER_CONCURRENCY = 2
FREE_TIER_STAGGER_SECONDS = 1.2
_provider_semaphore = asyncio.Semaphore(FREE_TIER_CONCURRENCY)

# Rate limit guard - free tier can handle ~1 req/6s on Groq, ~1 req/4s on Gemini
_last_groq_call = 0.0
_last_gemini_call = 0.0
_last_openrouter_request = 0.0
GROQ_MIN_INTERVAL = 8.0
GEMINI_MIN_INTERVAL = 5.0
_openrouter_rate_limit_sec = 1.0  # Minimum 1s between OpenRouter requests

TRADING_SYSTEM_PROMPT = """You are a quantitative prediction market analyst.
Your job is to find edge in Polymarket markets and output a JSON trading decision.

CRITICAL RULES:
1. If your estimated probability differs from the market price by >8%, you MUST recommend BUY.
2. Never output SKIP/HOLD unless you genuinely have zero edge (<5% difference).
3. Confidence should reflect your certainty: >0.65 means you have real edge.
4. You MUST respond with ONLY valid JSON — no markdown, no explanation outside JSON.

JSON format:
{"action": "BUY", "side": "YES", "limit_price": 55, "confidence": 0.72, "reasoning": "brief"}
or
{"action": "SKIP", "side": "YES", "limit_price": 0, "confidence": 0.40, "reasoning": "no edge"}"""


def _clean_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        return ""

    lowered = value.lower()
    if any(marker in lowered for marker in ("your_", "_here", "placeholder", "replace_me", "changeme")):
        return ""

    return value


def _key_pool(single_name: str, multi_name: str) -> List[str]:
    """Return de-duplicated provider keys from singular and comma/newline-separated env vars."""
    values: List[str] = []

    single = _clean_env(single_name)
    if single:
        values.append(single)

    multi_raw = os.getenv(multi_name, "")
    if multi_raw:
        for part in multi_raw.replace("\n", ",").split(","):
            candidate = part.strip()
            if not candidate:
                continue
            lowered = candidate.lower()
            if any(marker in lowered for marker in ("your_", "_here", "placeholder", "replace_me", "changeme")):
                continue
            values.append(candidate)

    deduped: List[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _has_groq() -> bool:
    return bool(_clean_env("GROQ_API_KEY"))


def _has_gemini() -> bool:
    return bool(_key_pool("GEMINI_API_KEY", "GEMINI_API_KEYS"))


def _has_paid_keys() -> bool:
    return bool(
        _clean_env("XAI_API_KEY") or
        _clean_env("OPENROUTER_API_KEY") or
        os.getenv("OPENROUTER_API_KEYS", "").strip()
    )


def _has_openrouter() -> bool:
    return bool(_key_pool("OPENROUTER_API_KEY", "OPENROUTER_API_KEYS"))


def _call_groq_sync(prompt: str, model: str = "llama-3.3-70b-versatile") -> Optional[str]:
    global _last_groq_call

    elapsed = time.time() - _last_groq_call
    if elapsed < GROQ_MIN_INTERVAL:
        time.sleep(GROQ_MIN_INTERVAL - elapsed)
    _last_groq_call = time.time()

    api_key = _clean_env("GROQ_API_KEY")
    if not api_key:
        return None

    try:
        resp = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": TRADING_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 300,
            },
            timeout=PROVIDER_TIMEOUT,
        )

        if resp.status_code == 429:
            logger.warning("Groq 429 rate limit - failing over immediately to Gemini")
            return None

        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    except httpx.HTTPStatusError as e:
        logger.warning(f"Groq call failed ({model}): {e}")
        return None
    except Exception as e:
        logger.warning(f"Groq call failed ({model}): {e}")
        return None


def _gemini_should_rotate_key(status_code: int) -> bool:
    return status_code in {401, 403, 404, 429}


def _openrouter_should_rotate_key(status_code: int) -> bool:
    return status_code in {401, 403, 404, 429}


def _call_gemini_sync(prompt: str, model: str = "gemini-3.1-flash-lite-preview") -> Optional[str]:
    global _last_gemini_call

    elapsed = time.time() - _last_gemini_call
    if elapsed < GEMINI_MIN_INTERVAL:
        time.sleep(GEMINI_MIN_INTERVAL - elapsed)
    _last_gemini_call = time.time()

    api_keys = _key_pool("GEMINI_API_KEY", "GEMINI_API_KEYS")
    if not api_keys:
        logger.debug("Gemini API key not configured")
        return None

    for key_index, api_key in enumerate(api_keys, start=1):
        try:
            url = (
                f"https://generativelanguage.googleapis.com/v1beta"
                f"/models/{model}:generateContent?key={api_key}"
            )
            full_prompt = TRADING_SYSTEM_PROMPT + "\n\n" + prompt
            logger.debug(f"Gemini attempt: {model} using key {key_index}/{len(api_keys)}")

            resp = httpx.post(
                url,
                json={
                    "contents": [{"parts": [{"text": full_prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 300},
                },
                timeout=PROVIDER_TIMEOUT,
            )

            if resp.status_code == 429:
                logger.warning(
                    f"Gemini key {key_index}/{len(api_keys)} hit 429 - rotating immediately to next Gemini key"
                )
                continue

            resp.raise_for_status()
            logger.info(f"Gemini succeeded with model: {model} using key {key_index}/{len(api_keys)}")
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            error_msg = f"Gemini {model}: HTTP {status}"
            if _gemini_should_rotate_key(status):
                logger.warning(f"{error_msg} - rotating immediately to next Gemini key")
                continue
            logger.warning(f"{error_msg} - failing over to next Gemini key")
            continue
        except Exception as e:
            logger.warning(
                f"Gemini key {key_index}/{len(api_keys)} failed: {e} - rotating immediately to next Gemini key"
            )
            continue

    logger.warning("All Gemini keys failed - falling back to OpenRouter")
    return None


def _call_openrouter_sync(
    prompt: str,
    model: str = "google/gemini-3-flash-preview",
) -> Optional[str]:
    global _last_openrouter_request

    now = time.time()
    elapsed = now - _last_openrouter_request
    if elapsed < _openrouter_rate_limit_sec:
        time.sleep(_openrouter_rate_limit_sec - elapsed)
    _last_openrouter_request = time.time()

    api_keys = _key_pool("OPENROUTER_API_KEY", "OPENROUTER_API_KEYS")
    if not api_keys:
        logger.debug("OpenRouter API key not configured")
        return None

    models_to_try = [
        model,
        "google/gemini-3-pro-preview",
        "anthropic/claude-sonnet-4.5",
        "deepseek/deepseek-v3.2",
    ]

    for key_index, api_key in enumerate(api_keys, start=1):
        for attempt_model in models_to_try:
            try:
                resp = httpx.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": attempt_model,
                        "messages": [
                            {"role": "system", "content": TRADING_SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 300,
                    },
                    timeout=PROVIDER_TIMEOUT,
                )
                resp.raise_for_status()
                logger.info(
                    f"OpenRouter succeeded with model: {attempt_model} using key {key_index}/{len(api_keys)}"
                )
                return resp.json()["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                error_msg = f"OpenRouter {attempt_model}: HTTP {status}"
                if _openrouter_should_rotate_key(status):
                    logger.warning(f"{error_msg} - rotating to next OpenRouter key")
                    break
                logger.warning(f"{error_msg} - {e}")
                continue
            except Exception as e:
                logger.debug(
                    f"OpenRouter {attempt_model} failed on key {key_index}/{len(api_keys)}: {e}"
                )
                continue

    logger.warning("OpenRouter: all model variants failed")
    return None


async def _run_provider_call(
    provider_name: str,
    sync_callable,
    prompt: str,
) -> Optional[str]:
    async with _provider_semaphore:
        await asyncio.sleep(FREE_TIER_STAGGER_SECONDS)
        loop = asyncio.get_event_loop()
        task = loop.run_in_executor(None, sync_callable, prompt)
        try:
            return await asyncio.wait_for(task, timeout=PROVIDER_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"{provider_name} call timed out after {PROVIDER_TIMEOUT}s")
            return None


async def get_free_completion(prompt: str) -> Optional[str]:
    """Try Gemini first, then OpenRouter, then Groq. Include rate limiting and retry logic."""
    logger.info(
        "Free-tier provider chain starting",
        extra={
            "groq_available": _has_groq(),
            "gemini_available": _has_gemini(),
            "openrouter_available": _has_openrouter(),
        },
    )

    if _has_gemini():
        logger.debug("Attempting Gemini completion...")
        result = await _run_provider_call("Gemini", _call_gemini_sync, prompt)
        if result:
            logger.info("Successfully got Gemini completion")
            return result

        logger.warning("Gemini failed or returned empty - falling back to OpenRouter")
        await asyncio.sleep(0.5)
    else:
        logger.warning("Gemini unavailable - skipping to OpenRouter")

    if _has_openrouter():
        logger.debug("Attempting OpenRouter completion...")
        result = await _run_provider_call("OpenRouter", _call_openrouter_sync, prompt)
        if result:
            logger.info("Successfully got OpenRouter completion")
            return result
        logger.warning("OpenRouter failed or returned empty - falling back to Groq")
        await asyncio.sleep(0.5)
    else:
        logger.warning("OpenRouter unavailable - skipping to Groq")

    if _has_groq():
        logger.debug("Attempting Groq completion...")
        result = await _run_provider_call("Groq", _call_groq_sync, prompt)
        if result:
            logger.info("Successfully got Groq completion")
            return result
        logger.warning("Groq failed or returned empty")
    else:
        logger.warning("Groq unavailable - no further providers in fallback chain")

    logger.warning("Gemini, OpenRouter, and Groq all failed or were unavailable")
    return None


def active_tier() -> str:
    if _has_paid_keys():
        return "paid"
    if _has_groq() or _has_gemini():
        return "free"
    return "none"


def tier_summary() -> dict:
    return {
        "tier":                  active_tier(),
        "groq_available":        _has_groq(),
        "gemini_available":      _has_gemini(),
        "openrouter_available":  _has_openrouter(),
        "xai_available":         bool(_clean_env("XAI_API_KEY")),
    }

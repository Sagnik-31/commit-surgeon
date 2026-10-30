"""Local LLM client for Ollama (native /api/chat, thinking OFF)."""
import os
import re
import time

import requests


class LlmError(Exception):
    """Raised with a human-friendly message when the model call fails."""


def _model() -> str:
    return os.environ.get("SURGEON_MODEL", "qwen3.5:4b")


def _host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def _build_body(messages: list, schema, max_tokens: int) -> dict:
    options = {"temperature": 0.2, "num_ctx": 4096, "num_predict": max_tokens}
    if os.environ.get("SURGEON_CPU") == "1":
        options["num_gpu"] = 0
    body = {
        "model": _model(),
        "messages": messages,
        "think": False,
        "stream": False,
        "keep_alive": "30m",
        "options": options,
    }
    if schema is not None:
        body["format"] = schema
    return body


def chat(system, user, fewshot=None, schema=None, max_tokens=160) -> str:
    """Send one chat request and return the cleaned text answer."""
    if os.environ.get("SURGEON_MOCK") == "1":
        from surgeon import mock
        return mock.chat(system, user, schema)

    messages = [{"role": "system", "content": system}]
    for u, a in (fewshot or []):
        messages.append({"role": "user", "content": u})
        messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": user})

    start = time.time()
    try:
        r = requests.post(
            f"{_host()}/api/chat",
            json=_build_body(messages, schema, max_tokens),
            timeout=120,
        )
    except requests.exceptions.ConnectionError:
        raise LlmError("Cannot reach Ollama. Fix: run `ollama serve`.")
    except requests.exceptions.Timeout:
        raise LlmError("Model timed out. Try SURGEON_MODEL=qwen3.5:2b.")

    if r.status_code == 404:
        raise LlmError(f"Model missing. Fix: run `ollama pull {_model()}`.")
    if r.status_code != 200:
        raise LlmError(f"Ollama error {r.status_code}: {r.text[:200]}")

    text = r.json().get("message", {}).get("content", "")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    print(f"[llm] {time.time() - start:.1f}s")
    if not text:
        raise LlmError("Model returned empty text (is thinking on?).")
    return text


def warmup() -> None:
    """Load the model into memory so the first real call is fast."""
    try:
        chat("Reply with one word.", "ready", max_tokens=5)
    except LlmError:
        pass
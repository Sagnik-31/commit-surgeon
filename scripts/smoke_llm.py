"""Smoke test: ask the local model for one word, thinking off."""
import os
import re
import time

import requests

MODEL = os.environ.get("SURGEON_MODEL", "qwen3.5:4b")
URL = "http://localhost:11434/api/chat"

body = {
    "model": MODEL,
    "messages": [
        {"role": "user", "content": "Reply with the single word: ready"}
    ],
    "think": False,
    "stream": False,
    "keep_alive": "30m",
    "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 20},
}
if os.environ.get("SURGEON_CPU") == "1":
    body["options"]["num_gpu"] = 0

start = time.time()
r = requests.post(URL, json=body, timeout=120)
r.raise_for_status()
text = r.json()["message"]["content"]
text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
elapsed = time.time() - start

print(f"model:   {MODEL}")
print(f"answer:  {text!r}")
print(f"seconds: {elapsed:.1f}")
assert text, "empty answer: thinking mode may be on"
assert "<think>" not in text, "reasoning text leaked"
print("SMOKE TEST OK")
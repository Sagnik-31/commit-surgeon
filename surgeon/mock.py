"""Canned replies for SURGEON_MOCK=1 (no model needed)."""
import json


def chat(system: str, user: str, schema=None) -> str:
    text = user.lower()
    if schema and "enum" in json.dumps(schema) and "properties" not in schema:
        return "refactor"
    if "## summary" in system.lower():
        return ("## Summary\nMock summary.\n## What changed\n- mock change\n"
                "## Why\nMock reason.\n## How to test\n1. pytest\n")
    if "docs" in text or "readme" in text:
        t, s = "docs", "update documentation"
    elif "fix" in text:
        t, s = "fix", "handle edge case"
    else:
        t, s = "refactor", "restructure code"
    return json.dumps({"type": t, "scope": "mock", "subject": s, "body": []})
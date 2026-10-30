"""Prompt templates and schema for the local model."""

COMMIT_SYSTEM = """You write Git commit messages in the Conventional Commits format.
Reply with ONE JSON object only. No markdown, no explanation.
Fields:
- "type": one of feat, fix, docs, style, refactor, perf, test, build, ci, chore
- "scope": a short lowercase word naming the area (usually the main file or folder name, no extension), or "" if unclear
- "subject": imperative mood, lowercase first letter, no period, at most 60 characters, says WHAT changed
- "body": a list of 0 to 3 short lines saying what changed or why. Only include a reason if the diff makes it clear.
Rules:
- Use ONLY facts visible in the diff. Never invent features, issue numbers or reasons.
- If a type hint is given, use it unless the diff clearly contradicts it.
- fix = corrects wrong behavior. refactor = restructures code, same behavior. docs = only documentation. feat = new capability."""

COMMIT_USER = "Type hint: {type_hint}\nFiles: {file_list}\n\nChanges:\n{condensed_diffs}"

TYPES = ["feat", "fix", "docs", "style", "refactor",
         "perf", "test", "build", "ci", "chore"]

COMMIT_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": TYPES},
        "scope": {"type": "string"},
        "subject": {"type": "string"},
        "body": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
    },
    "required": ["type", "scope", "subject", "body"],
}

FEWSHOT = [
    ("Type hint: refactor\nFiles: app/utils.py\n\nChanges:\n"
     "@@ def calc(items)\n"
     "- def calc(items):\n"
     "-     total = sum(i.price for i in items)\n"
     "-     return round(total, 2)\n"
     "+ def _round_money(x):\n"
     "+     return round(x, 2)\n"
     "+ def calculate_total(items):\n"
     "+     return _round_money(sum(i.price for i in items))",
     '{"type":"refactor","scope":"utils","subject":"extract rounding helper and rename calc","body":["rename calc to calculate_total","move rounding into _round_money"]}'),
    ("Type hint: fix\nFiles: app/billing.py\n\nChanges:\n"
     "@@ def unit_price(total, qty)\n"
     "- return total / qty\n"
     "+ if qty == 0:\n"
     "+     return 0\n"
     "+ return total / qty",
     '{"type":"fix","scope":"billing","subject":"avoid division by zero when quantity is 0","body":["return 0 from unit_price when qty is 0"]}'),
]

CLASSIFY_SYSTEM = """Classify a code change with exactly one word: fix, refactor, feat, perf, style, test, docs, or chore.
fix = corrects a bug (behavior changes to be correct). refactor = restructures or renames, behavior unchanged.
feat = adds a new capability. Answer with the one word only."""

CLASSIFY_USER = "File: {path}\n{condensed_diff}"

CLASSIFY_WORDS = ["fix", "refactor", "feat", "perf", "style",
                  "test", "docs", "chore"]
CLASSIFY_SCHEMA = {"type": "string", "enum": CLASSIFY_WORDS}
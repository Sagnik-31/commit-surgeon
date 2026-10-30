import json

from surgeon import messages
from surgeon.contracts import Change, Group

G = Group(1, "fix", "fix", ["app/billing.py"], "")
CH = {"app/billing.py": Change("app/billing.py")}


def run(monkeypatch, replies):
    it = iter(replies)
    monkeypatch.setattr(messages.llm, "chat", lambda *a, **k: next(it))
    return messages.generate_commit(G, CH)


def test_autofix(monkeypatch):
    r = json.dumps({"type": "fix", "scope": "Billing",
                    "subject": "Avoid crash when quantity is zero.", "body": []})
    p = run(monkeypatch, [r])
    assert p.header() == "fix(billing): avoid crash when quantity is zero"
    assert not p.used_fallback


def test_retry_then_ok(monkeypatch):
    good = json.dumps({"type": "fix", "scope": "billing",
                       "subject": "guard zero", "body": []})
    p = run(monkeypatch, ["not json", good])
    assert not p.used_fallback


def test_fallback(monkeypatch):
    p = run(monkeypatch, ["bad", "still bad"])
    assert p.used_fallback
    assert messages.HEADER_RE.match(p.header())
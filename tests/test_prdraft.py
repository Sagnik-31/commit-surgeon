from surgeon import prdraft
from surgeon.contracts import CommitPlan

PLANS = [CommitPlan(1, "fix", "billing", "avoid crash", ["app/billing.py"])]


def test_template_has_headings():
    md = prdraft._template(PLANS, "pytest")
    assert prdraft._valid(md)


def test_fallback_on_bad_output(monkeypatch):
    monkeypatch.setattr(prdraft.llm, "chat", lambda *a, **k: "no headings here")
    pr = prdraft.generate_pr(PLANS, {"branch": "x", "test_cmd": ""})
    assert pr.used_fallback and prdraft._valid(pr.markdown)
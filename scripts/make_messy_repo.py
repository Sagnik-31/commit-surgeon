"""Build ./sandbox_messy, a repo with three mixed concerns.

Usage:
  python scripts/make_messy_repo.py              uncommitted mess
  python scripts/make_messy_repo.py --committed  one big "fixed stuff" commit
  python scripts/make_messy_repo.py --hard       adds untracked, deleted, renamed files
"""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "sandbox_messy"


def git(*args):
    subprocess.run(["git", *args], cwd=ROOT, check=True,
                   capture_output=True, encoding="utf-8", errors="replace")


def write(rel, text):
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def _rm(func, path, exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def main():
    committed = "--committed" in sys.argv
    hard = "--hard" in sys.argv

    if ROOT.exists():
        shutil.rmtree(ROOT, onexc=_rm)
    ROOT.mkdir()

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    git("config", "core.autocrlf", "false")

    write("app/__init__.py", "")
    write("app/utils.py",
          "def calc(items):\n"
          "    total = sum(i['price'] for i in items)\n"
          "    return round(total, 2)\n")
    write("app/billing.py",
          "def unit_price(total, qty):\n"
          "    return total / qty\n")
    write("app/legacy.py", "def old():\n    return 1\n")
    write("app/helpers.py", "def shout(s):\n    return s.upper()\n")
    write("tests/test_billing.py",
          "from app.billing import unit_price\n\n\n"
          "def test_unit_price():\n"
          "    assert unit_price(10, 2) == 5\n")
    write("README.md", "# Shop\n\nA tiny shop library.\nMore soon.\n")
    write("docs/usage.md", "# Usage\n\nImport app.utils.\nThat is all.\n")
    git("add", "-A")
    git("commit", "-m", "chore: initial commit")
    git("checkout", "-b", "messy-changes")

    # 1) REFACTOR
    write("app/utils.py",
          "def _round_money(x):\n"
          "    return round(x, 2)\n\n\n"
          "def calculate_total(items):\n"
          "    return _round_money(sum(i['price'] for i in items))\n")
    # 2) BUG FIX + test
    write("app/billing.py",
          "def unit_price(total, qty):\n"
          "    if qty == 0:\n"
          "        return 0\n"
          "    return total / qty\n")
    write("tests/test_billing.py",
          "from app.billing import unit_price\n\n\n"
          "def test_unit_price():\n"
          "    assert unit_price(10, 2) == 5\n\n\n"
          "def test_unit_price_zero_qty():\n"
          "    assert unit_price(10, 0) == 0\n")
    # 3) DOCS
    write("README.md",
          "# Shop\n\nA tiny shop library.\nMore soon.\n\n"
          "## Install\n\n    pip install -r requirements.txt\n\n"
          "## Usage\n\n    from app.utils import calculate_total\n")
    write("docs/usage.md",
          "# Usage\n\nImport app.utils.\nThat is all.\n\n"
          "## Totals\n\ncalculate_total(items) returns the rounded sum.\n")

    if hard:
        write("docs/faq.md", "# FAQ\n\nQ: Does it round?\nA: Yes, to 2 places.\n")
        (ROOT / "app" / "legacy.py").unlink()
        (ROOT / "app" / "helpers.py").rename(ROOT / "app" / "formatting.py")

    if committed:
        git("add", "-A")
        git("commit", "-m", "fixed stuff")

    print(f"Built {ROOT}")
    print("Expected result: 3 commits")
    print("  refactor: app/utils.py")
    print("  fix:      app/billing.py + tests/test_billing.py")
    print("  docs:     README.md + docs/usage.md")
    if hard:
        print("  (hard mode also has: untracked docs/faq.md, deleted app/legacy.py,")
        print("   renamed app/helpers.py -> app/formatting.py)")


if __name__ == "__main__":
    main()
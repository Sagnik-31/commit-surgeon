from surgeon import hook


def test_script_is_safe():
    assert hook.SCRIPT.startswith("#!/bin/sh")
    assert hook.MARKER in hook.SCRIPT
    assert hook.SCRIPT.strip().endswith("exit 0")
    assert "\r" not in hook.SCRIPT
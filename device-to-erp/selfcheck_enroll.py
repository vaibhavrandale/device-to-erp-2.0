#!/usr/bin/env python3
"""Self-check for local enroll: result matching + the ids-on-screen hold.

Run: python selfcheck_enroll.py
No broker, no sensor, no OLED needed.
"""
from __future__ import annotations

import sys

from enroll_now import is_result_for, result_fp_id
from taypro.config import DEFAULTS


def check_result_matching() -> None:
    hw = "b827ebaabbcc"
    ok = {"a": "enroll_result", "hw": hw, "ok": True, "c": "FP0007", "card_id": "FP0007"}
    assert is_result_for(ok, hw)
    assert result_fp_id(ok) == "FP0007"

    # A neighbour Pi on the same broker must not end our wait.
    assert not is_result_for({**ok, "hw": "deadbeef0000"}, hw)
    # Taps share the up topic.
    assert not is_result_for({"a": "tap", "hw": hw}, hw)

    # Failure carries no id, so the caller stops instead of printing a blank id.
    assert result_fp_id({"a": "enroll_result", "hw": hw, "ok": False, "c": ""}) == ""
    # Older firmware only fills "c".
    assert result_fp_id({"ok": True, "c": "FP0012"}) == "FP0012"
    print("[ok] enroll_result matching (hw filter, c/card_id, failure has no id)")


def check_screen_hold() -> None:
    hold = DEFAULTS["enroll_ids_screen_s"]
    # The whole point is copying two ids by hand — a tap-length flash is useless.
    assert hold >= 30, hold
    assert hold > DEFAULTS["tap_screen_s"], hold
    print(f"[ok] ids stay on the OLED for {hold}s")


def main() -> int:
    check_result_matching()
    check_screen_hold()
    print("selfcheck_enroll OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

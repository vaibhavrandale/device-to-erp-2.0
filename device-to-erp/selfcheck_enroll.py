#!/usr/bin/env python3
"""Self-check for local enroll: result matching + the ids-on-screen hold.

Run: python selfcheck_enroll.py
No broker, no sensor, no OLED needed.
"""
from __future__ import annotations

import sys
import types

# pyserial only exists on the Pi; the bitmap decoding under test is pure bytes.
sys.modules.setdefault("serial", types.ModuleType("serial"))

from enroll_now import is_result_for, pick_enroll_slot, result_fp_id
from taypro.config import DEFAULTS
from taypro.fingerprint import decode_index_table


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


def check_slot_pairing() -> None:
    window = DEFAULTS["enroll_pair_window_s"]
    empty = {1: "", 2: ""}

    # First unknown finger of the day starts a new employee.
    assert pick_enroll_slot(empty, 999.0, window) == (1, empty)

    # Second finger straight after belongs to the same employee.
    slot, ids = pick_enroll_slot({1: "FP0007", 2: ""}, 3.0, window)
    assert (slot, ids) == (2, {1: "FP0007", 2: ""}), (slot, ids)

    # The payroll trap: a different person walking up later must NOT land in
    # slot 2 beside the previous person's finger 1.
    slot, ids = pick_enroll_slot({1: "FP0007", 2: ""}, window + 1, window)
    assert (slot, ids) == (1, empty), (slot, ids)

    # Both slots full — next finger is a new employee even if it is immediate.
    slot, ids = pick_enroll_slot({1: "FP0007", 2: "FP0008"}, 1.0, window)
    assert (slot, ids) == (1, empty), (slot, ids)

    # Caller must not be able to mutate the screen state it passed in.
    original = {1: "FP0007", 2: ""}
    _, ids = pick_enroll_slot(original, 3.0, window)
    ids[2] = "FP0009"
    assert original == {1: "FP0007", 2: ""}, original
    print("[ok] slot pairing (same employee back to back, new one after window)")


def first_free(occupied: set[int], capacity: int) -> int | None:
    """Mirrors next_template_id's choice once the sensor's bitmap is known."""
    return next((p for p in range(1, capacity + 1) if p not in occupied), None)


def check_free_page_choice() -> None:
    def table(pages: set[int]) -> bytes:
        buf = bytearray(32)
        for page in pages:
            buf[page // 8] |= 1 << (page % 8)
        return bytes(buf)

    assert decode_index_table([table({1, 2, 3})]) == {1, 2, 3}
    # Bit/byte order: page 8 must be byte 1 bit 0, not byte 0 bit 8.
    assert decode_index_table([table({8, 15})]) == {8, 15}
    # Second bitmap covers pages 256..511.
    assert decode_index_table([table(set()), table({0, 5})]) == {256, 261}

    # The bug this replaces: 3 stored, page 2 deleted, template_count() == 2, so
    # count + 1 would return 3 and overwrite a live employee. The gap wins.
    assert first_free(decode_index_table([table({1, 3})]), 200) == 2
    # No gaps — append after the last one.
    assert first_free(decode_index_table([table({1, 2, 3})]), 200) == 4
    # Page 0 is never used, so an empty sensor still starts at 1.
    assert first_free(decode_index_table([table(set())]), 200) == 1
    # Full library must report full rather than silently reusing page 1.
    assert first_free(decode_index_table([table(set(range(0, 33)))]), 32) is None
    print("[ok] free page picked from the sensor bitmap, gaps reused, 1-based")


def check_screen_hold() -> None:
    hold = DEFAULTS["enroll_ids_screen_s"]
    # The whole point is copying two ids by hand — a tap-length flash is useless.
    assert hold >= 30, hold
    assert hold > DEFAULTS["tap_screen_s"], hold
    # Ids must still be readable while the pair window is open, or the operator
    # loses finger 1 off the screen before finger 2 is captured.
    assert hold >= DEFAULTS["enroll_pair_window_s"], hold
    print(f"[ok] ids stay on the OLED for {hold}s")


def main() -> int:
    check_result_matching()
    check_slot_pairing()
    check_free_page_choice()
    check_screen_hold()
    print("selfcheck_enroll OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

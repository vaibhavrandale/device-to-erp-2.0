from __future__ import annotations

import time
from typing import Optional

from .api_client import ConsoleApi
from .fingerprint import finger_id_to_fp
from .leds import StatusLeds
from .logger import device_log
from .oled import OledDisplay
from .storage import DeviceStorage


class TapHandler:
    """Fingerprint match → POST /hr/attendance/punch with the finger id."""

    def __init__(
        self,
        api: ConsoleApi,
        storage: DeviceStorage,
        debounce_s: float = 2.0,
        response_timeout_s: float = 12.0,
        oled: Optional[OledDisplay] = None,
        leds: Optional[StatusLeds] = None,
    ):
        self.api = api
        self.storage = storage
        self.debounce_s = debounce_s
        self.response_timeout_s = response_timeout_s
        self.oled = oled
        self.leds = leds
        self.last_fp: Optional[str] = None
        self.last_ms = 0.0
        self.in_flight = False
        self.cooldown_s = max(float(debounce_s), 10.0)

    def handle_template(self, template_id: int) -> bool:
        fp_id = finger_id_to_fp(template_id)
        now = time.monotonic()

        if self.in_flight:
            return False
        if self.last_ms and (now - self.last_ms) < self.cooldown_s:
            left = self.cooldown_s - (now - self.last_ms)
            print(f"Punch ignored — cooldown {left:.0f}s left")
            device_log.log(f"Finger ignored — wait {left:.0f}s")
            if self.oled:
                self.oled.show_ignored(left)
            return False

        self.in_flight = True
        self.last_fp = fp_id
        self.last_ms = now

        print(f"Finger match template={template_id} → {fp_id}")
        device_log.log(f"Finger punch — {fp_id}")
        if self.oled:
            self.oled.show_processing(fp_id)
        try:
            result = self.api.punch(
                fp_id,
                device_id=self.storage.device_id or "",
                timeout_s=self.response_timeout_s,
            )
            if result["http_status"] == 0:
                print(f"[ERR-703] NO RESPONSE — {result['message']}")
                device_log.problem("Punch", result["message"] or "Server timeout")
                if self.leds:
                    self.leds.trigger_fail()
                if self.oled:
                    self.oled.show_error(703, "NO RESPONSE", "Server timeout — try again", fp_id)
                return True
            if result["ok"]:
                who = result["employee"] or "-"
                kind = result["punch_type"] or "punch"
                print(f"Punch OK — {who} ({kind})")
                device_log.log(f"Punch OK — {who} ({kind})")
                if self.leds:
                    self.leds.trigger_ok()
                if self.oled:
                    self.oled.show_punch_ok(who, result["punch_type"], fp_id=fp_id)
            else:
                msg = result["message"] or "Rejected by server"
                print(f"[ERR-704] PUNCH FAILED — {msg}")
                device_log.problem("Punch", msg)
                if self.leds:
                    self.leds.trigger_fail()
                if self.oled:
                    lower = msg.lower()
                    if "not registered" in lower or "fingerprint" in lower:
                        self.oled.show_error(
                            704,
                            "NOT ENROLLED",
                            "Finger not linked to any employee",
                            "Ask HR to enroll",
                        )
                    else:
                        self.oled.show_error(704, "REJECTED", msg, fp_id)
            return True
        finally:
            self.in_flight = False
            self.last_ms = time.monotonic()

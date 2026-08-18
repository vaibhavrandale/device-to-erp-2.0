"""Remote enroll from HR UI via MQTT a:enroll."""

from __future__ import annotations

from typing import Any, Optional

from .fingerprint import (
    R307,
    FingerprintError,
    decode_index_table,
    finger_id_to_fp,
)
from .mqtt_client import AttendanceMqtt
from .oled import OledDisplay


def occupied_pages(sensor: R307, capacity: int) -> set[int]:
    groups = max(1, (capacity + 255) // 256)
    return decode_index_table([sensor.read_index_table(g) for g in range(groups)])


def next_template_id(sensor: R307, capacity: int) -> int:
    """Lowest free page, counting from 1 because FP ids in HR are 1-based.

    A template count is not enough: with pages 1-3 stored and page 2 deleted the
    count is 2, so count + 1 hands back page 3 and silently overwrites a live
    employee's finger. Ask the sensor which pages are really occupied.
    """
    try:
        occupied = occupied_pages(sensor, capacity)
    except FingerprintError:
        occupied = None  # clone firmware without ReadIndexTable — fall back
    if occupied is not None:
        for page in range(1, capacity + 1):
            if page not in occupied:
                return page
        raise FingerprintError("Sensor library full")

    count = sensor.template_count()
    if count >= capacity:
        raise FingerprintError("Sensor library full (1000 max)")
    return count + 1


def run_remote_enroll(
    sensor: R307,
    mqtt: AttendanceMqtt,
    job: dict[str, Any],
    *,
    capacity: int = 1000,
    oled: Optional[OledDisplay] = None,
) -> Optional[str]:
    """Enroll one finger. Returns the FP id (e.g. "FP0007") or None on failure."""
    timeout_s = float(job.get("timeout_s") or 60)
    hr_user_id = str(job.get("hr_user_id") or "")
    employee_id = str(job.get("employee_id") or "")
    employee_name = str(job.get("employee_name") or "")
    location = job.get("location")
    finger = int(job.get("finger") or 1)
    if finger not in (1, 2):
        finger = 1

    def show(step: str) -> None:
        if oled and oled.ready:
            oled.show_enroll(finger, label, step)

    try:
        if location is None:
            location = next_template_id(sensor, capacity)
        else:
            location = int(location)
            if location < 1 or location > capacity:
                raise FingerprintError(f"location must be 1..{capacity}")

        label = employee_name or employee_id or f"#{location}"
        print(f"UI enroll finger {finger}/2 → page {location} ({label})")
        show("place1")

        sensor.enroll(location, timeout_s=timeout_s, on_step=show)

        fp_id = finger_id_to_fp(location)
        msg = f"Finger {finger}/2 enrolled as {fp_id}"
        print(msg)

        mqtt.send_enroll_result(
            ok=True,
            card_id=fp_id,
            location=location,
            hr_user_id=hr_user_id,
            employee_id=employee_id,
            message=msg,
            finger=finger,
        )
        return fp_id
    except Exception as exc:
        err = str(exc)
        print(f"UI enroll failed: {err}")
        if oled and oled.ready:
            oled.show_error(801, "ENROLL FAIL", err, f"Finger {finger}/2")
        mqtt.send_enroll_result(
            ok=False,
            card_id="",
            location=location if isinstance(location, int) else None,
            hr_user_id=hr_user_id,
            employee_id=employee_id,
            message=err,
            finger=finger,
        )
        return None

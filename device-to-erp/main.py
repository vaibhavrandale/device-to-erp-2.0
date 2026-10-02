#!/usr/bin/env python3
"""Taypro RasPi fingerprint attendance — REST punch + OLED for remote sites."""

from __future__ import annotations

import signal
import sys
import time
from pathlib import Path

from taypro.api_client import ConsoleApi
from taypro.boot import boot_register
from taypro.config import ROOT, load_config, parse_u32
from enroll_now import pick_enroll_slot
from taypro.enroll_remote import run_remote_enroll
from taypro.fingerprint import R307, FingerprintError
from taypro.leds import create_leds
from taypro.logger import device_log
from taypro.mqtt_client import AttendanceMqtt
from taypro.oled import create_oled
from taypro.storage import DeviceStorage, hardware_id
from taypro.sysmem import memory_lines, memory_pcts
from taypro.tap import TapHandler


def main() -> int:
    cfg = load_config()
    storage = DeviceStorage(defaults=cfg)
    hw = hardware_id()
    print("=== Taypro Fingerprint Attendance (RasPi) ===")
    print(f"hardware_id={hw}")
    print(f"API {cfg['api_base']}")
    print(f"UART {cfg['fingerprint_port']} @ {cfg['fingerprint_baud']}")

    device_log.log(
        f"Boot #{device_log.boot_id} — Fingerprint device started | HW {hw}"
    )

    oled = create_oled(cfg)
    leds = create_leds(cfg)
    if oled and oled.ready:
        oled.show_splash()
        time.sleep(1.0)
        device_log.log("OLED OK")
    else:
        device_log.problem("OLED", "Display not detected — continuing headless")

    mqtt = AttendanceMqtt(
        host=cfg["mqtt_host"],
        port=int(cfg["mqtt_port"]),
        topic_up=cfg["topic_up"],
        topic_down_prefix=cfg["topic_down_hw_prefix"],
        storage=storage,
        username=cfg.get("mqtt_username", ""),
        password=cfg.get("mqtt_password", ""),
        tls=bool(cfg.get("mqtt_tls")),
    )
    device_log.bind_mqtt(mqtt, sync_interval_s=float(cfg.get("log_sync_interval_s") or 10))

    stop = False

    def _stop(*_args):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    api = ConsoleApi(
        base=cfg["api_base"],
        email=cfg["api_email"],
        password=cfg["api_password"],
        token_path=Path(cfg.get("api_token_path") or (ROOT / "data" / "authtoken")),
    )

    if oled and oled.ready:
        oled.show_boot("..", "  ", "  ", "Signing in...", device_id=storage.device_id)

    signed_in, login_message = api.sign_in()
    if not signed_in:
        device_log.problem("Login", login_message)
        if leds:
            leds.trigger_fail()
            leds.update(mqtt_ok=False, connecting=False)
        if oled and oled.ready:
            oled.show_error(401, "LOGIN FAIL", login_message, cfg["api_base"])
        return 1

    device_log.log(f"API login OK — {cfg['api_email']}")
    if leds:
        leds.update(mqtt_ok=True)

    mqtt_up = mqtt.connect(timeout_s=20)
    ip = mqtt._local_ip()
    if not mqtt_up:
        device_log.problem("MQTT", f"Broker unreachable {cfg['mqtt_host']}:{cfg['mqtt_port']}")
        if oled and oled.ready:
            oled.show_error(402, "MQTT FAIL", "Punch still works. Enroll needs MQTT.", cfg["mqtt_host"])
            time.sleep(2.0)
    else:
        device_log.log("MQTT OK — enroll and heartbeat connected")
        if oled and oled.ready:
            oled.set_status_meta(ip=ip, extra=f"hw:{hw[-6:]}")
            oled.show_boot("OK", "OK", "..", "Registering...", ip=ip, device_id=storage.device_id)

        if not boot_register(mqtt, storage, timeout_s=float(cfg["register_timeout_s"])):
            device_log.problem("Register", "Boot register incomplete — retrying later")
            if oled and oled.ready:
                oled.show_boot("OK", "OK", "!!", "Register failed", ip=ip, device_id=storage.device_id)
        else:
            device_log.log(f"Register OK — device id {storage.device_id}")
            mqtt.send_heartbeat()
            device_log.sync(force=True)
            if oled and oled.ready:
                oled.show_register_result(
                    False,
                    storage.device_id,
                    f"Online {ip}",
                )
                time.sleep(1.2)

    try:
        sensor = R307.open(
            port=cfg["fingerprint_port"],
            baudrate=int(cfg["fingerprint_baud"]),
            address=parse_u32(cfg.get("fingerprint_address"), 0xFFFFFFFF),
            password=parse_u32(cfg.get("fingerprint_password"), 0),
        )
    except (FingerprintError, OSError) as exc:
        device_log.problem("Sensor", str(exc))
        if oled and oled.ready:
            oled.show_error(201, "SENSOR FAIL", str(exc), cfg["fingerprint_port"])
        mqtt.disconnect()
        return 1

    try:
        params = sensor.read_sys_params()
        templates = sensor.template_count()
        device_log.log(
            f"R307 OK capacity={params['capacity']} templates={templates} port={cfg['fingerprint_port']}"
        )
    except FingerprintError as exc:
        device_log.problem("Sensor", str(exc))
        if oled and oled.ready:
            oled.show_error(201, "SENSOR FAIL", str(exc))
        sensor.close()
        mqtt.disconnect()
        return 1

    tap = TapHandler(
        api,
        storage,
        debounce_s=float(cfg["finger_debounce_s"]),
        response_timeout_s=float(cfg["tap_response_timeout_s"]),
        oled=oled if (oled and oled.ready) else None,
        leds=leds,
    )

    last_heartbeat = time.monotonic()
    last_mqtt_try = 0.0
    last_ui = 0.0
    last_mem = 0.0
    ram_line, disk_line = memory_lines()
    ram_pct, disk_pct = memory_pcts()
    heartbeat_s = float(cfg["heartbeat_interval_s"])
    poll_s = float(cfg["scan_poll_s"])
    capacity = int(params["capacity"] or 200)

    device_log.log("Ready — fingerprint scanner online, waiting for scans")
    device_log.sync(force=True)
    if oled and oled.ready:
        oled.showing_tap = False
        oled.set_status_meta(ip=ip, extra=f"hw:{hw[-6:]}")
        oled.show_ready(
            storage,
            wifi_ok=True,
            mqtt_ok=api.logged_in,
            templates=templates,
            ram_pct=ram_pct,
            disk_pct=disk_pct,
        )

    wait_lift = False
    lift_streak = 0
    enrolled_ids = {1: "", 2: ""}
    auto_enroll = cfg.get("auto_enroll_unknown") is not False
    auto_enroll_timeout_s = float(cfg.get("auto_enroll_timeout_s") or 20)
    pair_window_s = float(cfg.get("enroll_pair_window_s") or 25)
    last_enroll = -pair_window_s

    try:
        while not stop:
            now = time.monotonic()
            if not mqtt.connected() and now - last_mqtt_try >= 15:
                last_mqtt_try = now
                device_log.problem("MQTT", "Disconnected — reconnecting")
                mqtt.connect(timeout_s=5)

            if leds:
                leds.update(mqtt_ok=api.logged_in, connecting=False)

            if mqtt.connected() and now - last_heartbeat >= heartbeat_s:
                mqtt.send_heartbeat()
                last_heartbeat = now
                ip = mqtt._local_ip()
                if oled and oled.ready:
                    oled.set_status_meta(ip=ip, extra=f"hw:{hw[-6:]}")

            device_log.sync(force=False)

            if (
                not tap.in_flight
                and not mqtt.enroll_pending
                and now - last_mem >= 10.0
            ):
                ram_line, disk_line = memory_lines()
                ram_pct, disk_pct = memory_pcts()
                print(ram_line, "|", disk_line, flush=True)
                last_mem = now
            if oled and oled.ready:
                oled.poll_clear_temp(storage, mqtt_ok=api.logged_in)
                if not oled.showing_tap and not tap.in_flight and now - last_ui >= 1.0:
                    try:
                        templates = sensor.template_count()
                    except FingerprintError:
                        pass
                    oled.show_ready(
                        storage,
                        wifi_ok=True,
                        mqtt_ok=api.logged_in,
                        templates=templates,
                        ram_pct=ram_pct,
                        disk_pct=disk_pct,
                    )
                    last_ui = now

            if mqtt.connected() and mqtt.enroll_pending and not tap.in_flight:
                job = mqtt.enroll_pending
                mqtt.enroll_pending = None
                wait_lift = True
                lift_streak = 0
                finger = 2 if int(job.get("finger") or 1) == 2 else 1
                # Never leave a previous employee's id beside the new one — it
                # would get copied into their payroll record. Finger 1 always
                # starts a fresh employee; so does a gap longer than the hold.
                if finger == 1 or now - last_enroll >= pair_window_s:
                    enrolled_ids = {1: "", 2: ""}
                last_enroll = now
                who = job.get("employee_name") or job.get("employee_id") or "?"
                device_log.log(f"Enroll start finger={finger}/2 employee={who}")
                fp_id = run_remote_enroll(
                    sensor,
                    mqtt,
                    job,
                    capacity=capacity,
                    oled=oled if (oled and oled.ready) else None,
                )
                if fp_id:
                    # Held on screen: nobody types ids from a headless Pi's stdout.
                    enrolled_ids[finger] = fp_id
                    if oled and oled.ready:
                        oled.show_enroll_ids(enrolled_ids[1], enrolled_ids[2])
                device_log.log(f"Enroll OK {fp_id}" if fp_id else "Enroll failed")
                device_log.sync(force=True)
                time.sleep(poll_s)
                continue

            if not tap.in_flight:
                try:
                    img = sensor.get_image()
                    if wait_lift:
                        if img == 0x02:
                            lift_streak += 1
                            if lift_streak >= 5:
                                wait_lift = False
                                lift_streak = 0
                        else:
                            lift_streak = 0
                    elif img == 0x00:
                        if sensor.image2tz(1) == 0x00:
                            page = sensor.search(slot=1, start=0, count=capacity)
                            if page is not None:
                                wait_lift = True
                                lift_streak = 0
                                tap.handle_template(page)
                                device_log.sync(force=True)
                            elif auto_enroll and mqtt.connected():
                                # No terminal on site, so an unrecognised finger
                                # IS the capture request. enroll() wants two
                                # placements, so a passer-by who touches once and
                                # walks off times out without eating a slot.
                                slot, enrolled_ids = pick_enroll_slot(
                                    enrolled_ids, now - last_enroll, pair_window_s
                                )
                                last_enroll = now
                                device_log.log(f"Unknown finger — capture slot {slot}/2")
                                fp_id = run_remote_enroll(
                                    sensor,
                                    mqtt,
                                    {"finger": slot, "timeout_s": auto_enroll_timeout_s},
                                    capacity=capacity,
                                    oled=oled if (oled and oled.ready) else None,
                                )
                                if fp_id:
                                    enrolled_ids[slot] = fp_id
                                    if leds:
                                        leds.trigger_ok()
                                    if oled and oled.ready:
                                        oled.show_enroll_ids(
                                            enrolled_ids[1], enrolled_ids[2]
                                        )
                                else:
                                    if leds:
                                        leds.trigger_fail()
                                    if oled and oled.ready:
                                        oled.show_no_match()
                                wait_lift = True
                                lift_streak = 0
                                device_log.sync(force=True)
                            else:
                                device_log.log("Finger seen — no match in sensor library")
                                if leds:
                                    leds.trigger_fail()
                                if oled and oled.ready:
                                    oled.show_no_match()
                                wait_lift = True
                                lift_streak = 0
                                device_log.sync(force=True)
                except FingerprintError as exc:
                    device_log.problem("Scan", str(exc))
                    if leds:
                        leds.trigger_fail()
                    if oled and oled.ready:
                        oled.show_error(201, "SCAN ERR", str(exc))

            time.sleep(poll_s)
    finally:
        device_log.log("Stopped")
        device_log.sync(force=True)
        sensor.close()
        mqtt.disconnect()
        if leds:
            leds.close()
        if oled and oled.ready:
            oled.show_lines("STOPPED", "Service ended", "Reboot to restart")
        print("Stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())

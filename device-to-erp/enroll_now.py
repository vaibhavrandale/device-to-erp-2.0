#!/usr/bin/env python3
"""Enrol fingers on this Pi and print/show the template ids.

The reader service owns the sensor, so this asks it over the local broker
instead of opening /dev/ttyUSB0 itself. Ids also stay on the OLED for a
minute so the operator can copy them into the HR form by hand.

  python3 enroll_now.py             # finger 1 then finger 2
  python3 enroll_now.py -f 1        # just finger 1
  python3 enroll_now.py -f 2 --id 7 # finger 2 into template page 7
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import paho.mqtt.client as mqtt_lib

from taypro.config import load_config
from taypro.storage import hardware_id


def is_result_for(doc: dict, hw: str) -> bool:
    """True when this up-topic message is our own reader's enroll answer."""
    return doc.get("a") == "enroll_result" and doc.get("hw") == hw


def result_fp_id(doc: dict) -> str:
    """FP id from a successful enroll_result, else "" (firmware sends c + card_id)."""
    if not doc.get("ok"):
        return ""
    return str(doc.get("card_id") or doc.get("c") or "")


def make_client(cfg: dict):
    try:
        client = mqtt_lib.Client(mqtt_lib.CallbackAPIVersion.VERSION2)
    except AttributeError:
        # paho-mqtt 1.x
        client = mqtt_lib.Client()
    if cfg.get("mqtt_username"):
        client.username_pw_set(cfg["mqtt_username"], cfg.get("mqtt_password") or "")
    return client


def enroll_one(client, inbox, down_topic, finger, page, timeout_s):
    """Publish a:enroll and block until the reader answers. Returns FP id or None."""
    payload = {"a": "enroll", "finger": finger, "timeout_s": timeout_s}
    if page is not None:
        payload["location"] = page
    inbox.clear()
    client.publish(down_topic, json.dumps(payload), qos=1)
    print(f"\nFinger {finger}/2 requested — place it on the sensor twice.")

    # +15s so the reader's own timeout fires first and reports a real reason.
    deadline = time.monotonic() + timeout_s + 15
    while time.monotonic() < deadline:
        if inbox:
            result = inbox.pop()
            fp_id = result_fp_id(result)
            if fp_id:
                print(f"  OK  finger {finger}/2 = {fp_id}")
                return fp_id
            print(f"  FAILED  {result.get('message') or 'unknown error'}")
            return None
        time.sleep(0.2)

    print("  TIMEOUT  no answer — check: systemctl status taypro-fingerprint")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enrol fingerprints on this Pi and report their template ids"
    )
    parser.add_argument(
        "-f",
        "--finger",
        type=int,
        choices=(1, 2),
        help="Enrol only this finger (default: both)",
    )
    parser.add_argument(
        "--id", type=int, help="Template page to write (default: next free slot)"
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    if args.id is not None and args.finger is None:
        parser.error("--id enrols a single page, so pass -f 1 or -f 2 as well")

    cfg = load_config()
    hw = hardware_id()
    up_topic = cfg["topic_up"]
    down_topic = f"{cfg['topic_down_hw_prefix']}{hw}"

    inbox: list[dict] = []

    def on_message(_client, _userdata, msg):
        try:
            doc = json.loads(msg.payload.decode("utf-8", "replace"))
        except ValueError:
            return
        if is_result_for(doc, hw):
            inbox.append(doc)

    client = make_client(cfg)
    client.on_message = on_message

    try:
        client.connect(cfg["mqtt_host"], int(cfg["mqtt_port"]), keepalive=30)
    except OSError as exc:
        print(f"Cannot reach the local broker at {cfg['mqtt_host']}: {exc}")
        return 1
    client.subscribe(up_topic, qos=1)
    client.loop_start()

    fingers = [args.finger] if args.finger else [1, 2]
    ids: dict[int, str] = {}
    try:
        for finger in fingers:
            fp_id = enroll_one(client, inbox, down_topic, finger, args.id, args.timeout)
            if not fp_id:
                break
            ids[finger] = fp_id
    except KeyboardInterrupt:
        print("\nAborted.")
    finally:
        client.loop_stop()
        client.disconnect()

    print("\n--- write these into the HR form ---")
    for finger in fingers:
        print(f"  Finger {finger}: {ids.get(finger) or '(not enrolled)'}")
    print("They also stay on the OLED for a minute.\n")
    return 0 if len(ids) == len(fingers) else 1


if __name__ == "__main__":
    sys.exit(main())

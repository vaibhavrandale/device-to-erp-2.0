// Assert-based check of the pure tap logic (no DB / broker needed): node selfcheck.js
import assert from "assert";
import {
  normalizeCardId,
  normalizeDeviceId,
  normalizeHardwareId,
  buildTapId,
  isValidLatLng,
  resolveTapCoordinates,
  buildHeartbeatPayload,
  parseFingerprintLocation,
} from "./attendance.js";

assert.strictEqual(normalizeCardId("  fp0007 "), "FP0007");
assert.strictEqual(normalizeDeviceId("  Office-Pi "), "office-pi");
assert.strictEqual(normalizeHardwareId("B8:27:EB:AA:BB:CC"), "b827ebaabbcc");
assert.strictEqual(normalizeHardwareId(null), "");
assert.strictEqual(parseFingerprintLocation("FP0007"), 7);
assert.strictEqual(parseFingerprintLocation("abc"), null);

const t = new Date("2026-01-02T03:04:05Z");
assert.strictEqual(
  buildTapId("office-pi", "FP0007", t),
  `office-pi-FP0007-${t.getTime()}`,
);

assert.strictEqual(isValidLatLng(18.52, 73.85), true);
assert.strictEqual(isValidLatLng(null, 73.85), false);
assert.strictEqual(isValidLatLng(91, 73.85), false);
assert.strictEqual(isValidLatLng(18.52, 181), false);

// tap coords win over device coords; invalid tap coords fall back to device
assert.deepStrictEqual(
  resolveTapCoordinates({
    tapLatitude: "18.52",
    tapLongitude: "73.85",
    deviceLatitude: 1,
    deviceLongitude: 1,
  }),
  { latitude: 18.52, longitude: 73.85 },
);
assert.deepStrictEqual(
  resolveTapCoordinates({
    tapLatitude: "",
    tapLongitude: null,
    deviceLatitude: 18.52,
    deviceLongitude: 73.85,
  }),
  { latitude: 18.52, longitude: 73.85 },
);
assert.deepStrictEqual(
  resolveTapCoordinates({
    tapLatitude: "abc",
    tapLongitude: 999,
    deviceLatitude: null,
    deviceLongitude: null,
  }),
  { latitude: null, longitude: null },
);

// firmware short keys (hw/d/k/n/w/p) map to full heartbeat fields
assert.deepStrictEqual(
  buildHeartbeatPayload({
    hw: "b827ebaabbcc",
    d: "office-pi",
    k: "secret",
    n: "Office Pi",
    w: "raspi",
    p: 2,
    ip: "192.168.1.50",
  }),
  {
    hardware_id: "b827ebaabbcc",
    device_id: "office-pi",
    device_key: "secret",
    device_name: "Office Pi",
    wifi_ssid: "raspi",
    pending_sync_count: 2,
    ip: "192.168.1.50",
  },
);

console.log("selfcheck OK");
process.exit(0);

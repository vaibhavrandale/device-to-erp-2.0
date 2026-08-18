// Trimmed copy of taypro-console-backend/attendance/attendance.service.js.
// Kept: register / heartbeat / tap / remote fingerprint enroll (HR UI).
// Dropped: sockets, status events, device logs, reports, frappe.
import crypto from "crypto";
import { AttendanceDevice, AttendancePunch, HRUser } from "./models.js";

const DOWN_HW_PREFIX = "hr/attendance/down/hw/";

// server.js injects (topic, payload) => boolean once MQTT is connected.
let publishRaw = () => false;

export const setDownPublisher = (fn) => {
  publishRaw = fn;
};

export const normalizeCardId = (cardId) =>
  String(cardId || "")
    .trim()
    .toUpperCase();

export const normalizeDeviceId = (deviceId) =>
  String(deviceId || "")
    .trim()
    .toLowerCase();

export const normalizeHardwareId = (hardwareId) =>
  String(hardwareId || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-f0-9]/g, "");

const publishAttendanceDown = (hardwareId, payload) => {
  const hw = normalizeHardwareId(hardwareId);
  if (!hw) return false;
  return publishRaw(`${DOWN_HW_PREFIX}${hw}`, JSON.stringify(payload));
};

const parseCoordinate = (value) => {
  if (value === undefined || value === null || value === "") return null;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
};

export const isValidLatLng = (latitude, longitude) => {
  if (latitude === null || longitude === null) return false;
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return false;
  if (latitude < -90 || latitude > 90) return false;
  if (longitude < -180 || longitude > 180) return false;
  return true;
};

export const resolveTapCoordinates = ({
  tapLatitude,
  tapLongitude,
  deviceLatitude,
  deviceLongitude,
}) => {
  const fromTapLat = parseCoordinate(tapLatitude);
  const fromTapLng = parseCoordinate(tapLongitude);
  if (isValidLatLng(fromTapLat, fromTapLng)) {
    return { latitude: fromTapLat, longitude: fromTapLng };
  }

  const fromDeviceLat = parseCoordinate(deviceLatitude);
  const fromDeviceLng = parseCoordinate(deviceLongitude);
  if (isValidLatLng(fromDeviceLat, fromDeviceLng)) {
    return { latitude: fromDeviceLat, longitude: fromDeviceLng };
  }

  return { latitude: null, longitude: null };
};

export const buildTapId = (deviceId, cardId, tappedAt) =>
  `${deviceId}-${cardId}-${new Date(tappedAt).getTime()}`;

const resolvePunchType = async (hrUserId, tappedAt) => {
  const lastPunch = await AttendancePunch.findOne({
    hr_user_id: hrUserId,
    is_delete: false,
    punch_type: { $in: ["check_in", "check_out"] },
  }).sort({ tapped_at: -1 });

  if (!lastPunch) return "check_in";

  const sameDay =
    new Date(lastPunch.tapped_at).toDateString() ===
    new Date(tappedAt).toDateString();

  if (!sameDay) return "check_in";

  return lastPunch.punch_type === "check_in" ? "check_out" : "check_in";
};

const buildUniqueDeviceId = async (preferredId, hardwareId) => {
  const baseId =
    normalizeDeviceId(preferredId) || `device-${hardwareId.slice(-6)}`;
  let candidate = baseId;
  let suffix = 1;

  while (
    await AttendanceDevice.findOne({ device_id: candidate, is_delete: false })
  ) {
    candidate = `${baseId}-${suffix}`;
    suffix += 1;
  }

  return candidate;
};

const syncFromDeviceReport = async (device, report = {}) => {
  const pending = device.setup_status === "pending_setup";
  let changed = false;

  const reportedName = String(report.device_name || "").trim();
  const reportedWifi = String(report.wifi_ssid || "").trim();
  const reportedDeviceId = normalizeDeviceId(report.device_id);

  if (reportedName && (pending || !String(device.name || "").trim())) {
    const isFactoryPlaceholder = reportedName.toLowerCase() === "taypro device";
    if (!isFactoryPlaceholder) {
      device.name = reportedName;
      changed = true;
    }
  }

  if (reportedWifi && (pending || !String(device.wifi_ssid || "").trim())) {
    device.wifi_ssid = reportedWifi;
    changed = true;
  }

  const placeholderIds = new Set(["unassigned", "device", ""]);
  if (
    pending &&
    reportedDeviceId &&
    reportedDeviceId !== device.device_id &&
    !placeholderIds.has(reportedDeviceId)
  ) {
    const conflict = await AttendanceDevice.findOne({
      device_id: reportedDeviceId,
      hardware_id: { $ne: device.hardware_id },
      is_delete: false,
    });

    if (!conflict) {
      device.device_id = reportedDeviceId;
      changed = true;
    }
  }

  if (changed) await device.save();
  return device;
};

const provisionDeviceFromReport = async ({
  hardware_id,
  device_id,
  device_name,
  wifi_ssid,
  ip = "",
}) => {
  const uniqueDeviceId = await buildUniqueDeviceId(device_id, hardware_id);

  return AttendanceDevice.create({
    hardware_id,
    device_id: uniqueDeviceId,
    name: String(device_name || "").trim() || uniqueDeviceId,
    wifi_ssid: String(wifi_ssid || "").trim(),
    api_key: crypto.randomBytes(16).toString("hex"),
    setup_status: "pending_setup",
    config_pending: true,
    status: "online",
    last_seen_at: new Date(),
    ip: ip || "",
  });
};

const resolveDeviceForHeartbeat = async ({
  hardware_id,
  device_id,
  device_key,
}) => {
  const hwId = normalizeHardwareId(hardware_id);
  const normalizedDeviceId = normalizeDeviceId(device_id);

  if (hwId) {
    let device = await AttendanceDevice.findOne({
      hardware_id: hwId,
      is_delete: false,
    });

    if (device) return device;

    if (normalizedDeviceId) {
      device = await AttendanceDevice.findOne({
        device_id: normalizedDeviceId,
        is_delete: false,
        $or: [
          { hardware_id: { $exists: false } },
          { hardware_id: null },
          { hardware_id: "" },
        ],
      });

      if (device) {
        device.hardware_id = hwId;
        await device.save();
        return device;
      }
    }

    return null;
  }

  if (!normalizedDeviceId || !device_key) return null;

  return AttendanceDevice.findOne({
    device_id: normalizedDeviceId,
    api_key: String(device_key).trim(),
    is_delete: false,
    is_active: true,
  });
};

const validateDeviceCredentials = async (deviceId, deviceKey) => {
  if (!deviceId || !deviceKey) return null;

  return AttendanceDevice.findOne({
    device_id: normalizeDeviceId(deviceId),
    api_key: String(deviceKey).trim(),
    is_delete: false,
    is_active: true,
  });
};

const resolveAuthorizedDevice = async ({
  device_id,
  device_key,
  hardware_id,
}) => {
  const credentialed = await validateDeviceCredentials(device_id, device_key);
  if (credentialed) return credentialed;

  const hwId = normalizeHardwareId(hardware_id);
  if (hwId) {
    const byHardware = await AttendanceDevice.findOne({
      hardware_id: hwId,
      is_delete: false,
      is_active: true,
    });
    if (byHardware) return byHardware;
  }

  const normalizedDeviceId = normalizeDeviceId(device_id);
  if (normalizedDeviceId && !String(device_key || "").trim()) {
    return AttendanceDevice.findOne({
      device_id: normalizedDeviceId,
      is_delete: false,
      is_active: true,
    });
  }

  return null;
};

export const processAttendanceTap = async ({
  device_id,
  card_id,
  tapped_at,
  latitude,
  longitude,
  source = "mqtt",
}) => {
  const normalizedDeviceId = normalizeDeviceId(device_id);
  const normalizedCardId = normalizeCardId(card_id);
  const tapTime = tapped_at ? new Date(tapped_at) : new Date();

  if (!normalizedDeviceId || !normalizedCardId) {
    return {
      success: false,
      type: "tap",
      card_id: normalizedCardId,
      device_id: normalizedDeviceId,
      message: "Device ID and card ID are required.",
      tapped_at: tapTime,
      source,
    };
  }

  const device = await AttendanceDevice.findOne({
    device_id: normalizedDeviceId,
    is_delete: false,
    is_active: true,
  });

  if (!device) {
    return {
      success: false,
      type: "tap",
      card_id: normalizedCardId,
      device_id: normalizedDeviceId,
      message: "Attendance device is not registered.",
      tapped_at: tapTime,
      source,
    };
  }

  const coords = resolveTapCoordinates({
    tapLatitude: latitude,
    tapLongitude: longitude,
    deviceLatitude: device.latitude,
    deviceLongitude: device.longitude,
  });

  const existingTap = await AttendancePunch.findOne({
    tap_id: buildTapId(normalizedDeviceId, normalizedCardId, tapTime),
  });

  if (existingTap) {
    return {
      success: true,
      type: "tap",
      duplicate: true,
      punch_id: existingTap._id,
      card_id: existingTap.card_id,
      device_id: existingTap.device_id,
      employee_id: existingTap.employee_id,
      employee_name: existingTap.employee_name,
      punch_type: existingTap.punch_type,
      tapped_at: existingTap.tapped_at,
      latitude: existingTap.latitude,
      longitude: existingTap.longitude,
      message: "Duplicate tap ignored.",
      source,
    };
  }

  const cardMatches = {
    is_delete: false,
    is_active: true,
    $or: [
      { rfid_card_id: normalizedCardId },
      { rfid_card_id_2: normalizedCardId },
    ],
  };

  // Prefer employee whose location matches this device (office/factory).
  let hrUser = await HRUser.findOne({
    ...cardMatches,
    location: device.location,
  });

  if (!hrUser) {
    const matches = await HRUser.find(cardMatches).limit(2);
    if (matches.length === 1) {
      hrUser = matches[0];
    } else if (matches.length > 1) {
      return {
        success: false,
        type: "tap",
        card_id: normalizedCardId,
        device_id: normalizedDeviceId,
        message: `Fingerprint/RFID ${normalizedCardId} matches multiple employees; device location "${device.location}" has no matching user.`,
        tapped_at: tapTime,
        source,
      };
    }
  }

  if (!hrUser) {
    const isFp = normalizedCardId.startsWith("FP");
    return {
      success: false,
      type: "tap",
      card_id: normalizedCardId,
      device_id: normalizedDeviceId,
      message: isFp
        ? "Fingerprint is not registered."
        : "RFID card is not registered.",
      tapped_at: tapTime,
      source,
    };
  }

  const punchType = await resolvePunchType(hrUser._id, tapTime);
  const tapId = buildTapId(normalizedDeviceId, normalizedCardId, tapTime);

  const punch = await AttendancePunch.create({
    tap_id: tapId,
    device_id: normalizedDeviceId,
    card_id: normalizedCardId,
    employee_id: hrUser.employee_id,
    employee_name: hrUser.name,
    employee_email: hrUser.email,
    hr_user_id: hrUser._id,
    punch_type: punchType,
    tapped_at: tapTime,
    latitude: coords.latitude,
    longitude: coords.longitude,
    source,
  });

  return {
    success: true,
    type: "tap",
    punch_id: punch._id,
    card_id: punch.card_id,
    device_id: punch.device_id,
    employee_id: punch.employee_id,
    employee_name: punch.employee_name,
    employee_email: punch.employee_email,
    department: hrUser.department,
    location: hrUser.location,
    punch_type: punch.punch_type,
    tapped_at: punch.tapped_at,
    latitude: punch.latitude,
    longitude: punch.longitude,
    message: `${hrUser.name} ${punchType === "check_in" ? "checked in" : "checked out"} successfully.`,
    source,
  };
};

// Firmware needs a:config down to learn its device_key + lat/lng.
const pushDeviceConfigToMqtt = (device) => {
  if (!device?.device_id || !device?.api_key) return false;

  return publishAttendanceDown(device.hardware_id, {
    a: "config",
    type: "config",
    k: device.api_key,
    device_key: device.api_key,
    wifi_ssid: device.wifi_ssid || "",
    wifi_password: device.wifi_password || "",
    device_id: device.device_id,
    device_name: device.name || device.device_id,
    latitude: device.latitude,
    longitude: device.longitude,
  });
};

export const processAttendanceHeartbeat = async ({
  hardware_id,
  device_id,
  device_key,
  device_name,
  wifi_ssid,
  pending_sync_count = 0,
  ip = "",
}) => {
  const hwId = normalizeHardwareId(hardware_id);
  let device = await resolveDeviceForHeartbeat({
    hardware_id: hwId,
    device_id,
    device_key,
  });

  if (!device && hwId) {
    device = await provisionDeviceFromReport({
      hardware_id: hwId,
      device_id,
      device_name,
      wifi_ssid,
      ip,
    });
  }

  if (!device) {
    const error = new Error(
      "Attendance device is not registered. Power on the device and wait for auto-registration.",
    );
    error.statusCode = 404;
    throw error;
  }

  await syncFromDeviceReport(device, { device_id, device_name, wifi_ssid });

  device.status = "online";
  device.last_seen_at = new Date();
  device.last_seen_via = "mqtt";
  device.pending_sync_count = Number(pending_sync_count) || 0;
  device.ip = ip || device.ip || "";
  await device.save();

  const shouldPushConfig =
    Boolean(device.config_pending) || device.setup_status === "pending_setup";

  if (shouldPushConfig) {
    const sent = pushDeviceConfigToMqtt(device);
    if (sent) {
      device.config_pending = false;
      device.config_pending_at = null;
      device.setup_status = "active";
      await device.save();
    }
  }

  return device;
};

export const buildHeartbeatPayload = (data) => ({
  hardware_id: data.hw || data.hardware_id || data.hardwareId,
  device_id: data.device_id || data.deviceId || data.d,
  device_key: data.device_key || data.deviceKey || data.k,
  device_name: data.device_name || data.deviceName || data.name || data.n,
  wifi_ssid: data.wifi_ssid || data.wifiSsid || data.ssid || data.w,
  pending_sync_count:
    data.pending_sync_count || data.pendingSyncCount || data.p || 0,
  ip: data.ip || "",
});

export const parseFingerprintLocation = (cardId) => {
  const match = normalizeCardId(cardId).match(/^FP(\d{1,4})$/);
  if (!match) return null;
  const n = Number(match[1]);
  return Number.isFinite(n) && n >= 1 ? n : null;
};

/**
 * HR UI → HTTP → this process → local MQTT a:enroll → Pi R307.
 * Finger 1 → rfid_card_id, finger 2 → rfid_card_id_2.
 */
export const requestFingerprintEnroll = async ({
  device_id,
  hr_user_id,
  location,
  finger = 1,
}) => {
  const deviceId = normalizeDeviceId(device_id);
  if (!deviceId) {
    const error = new Error("device_id is required.");
    error.statusCode = 400;
    throw error;
  }

  const fingerSlot = Number(finger) === 2 ? 2 : 1;

  const device = await AttendanceDevice.findOne({
    device_id: deviceId,
    is_delete: false,
  });
  if (!device) {
    const error = new Error("Attendance device not found.");
    error.statusCode = 404;
    throw error;
  }
  if (!device.hardware_id) {
    const error = new Error(
      "Device has no hardware_id yet (wait for MQTT register).",
    );
    error.statusCode = 400;
    throw error;
  }
  if (device.status !== "online") {
    const error = new Error("Device is offline. Power on the Pi reader first.");
    error.statusCode = 400;
    throw error;
  }

  let hrUser = null;
  let page = location != null && location !== "" ? Number(location) : null;
  if (hr_user_id) {
    hrUser = await HRUser.findOne({ _id: hr_user_id, is_delete: false });
    if (!hrUser) {
      const error = new Error("HR user not found.");
      error.statusCode = 404;
      throw error;
    }
    if (!Number.isFinite(page) || page < 1) {
      const existingCard =
        fingerSlot === 2 ? hrUser.rfid_card_id_2 : hrUser.rfid_card_id;
      page = parseFingerprintLocation(existingCard);
    }
  }

  const payload = {
    a: "enroll",
    device_id: device.device_id,
    hr_user_id: hrUser ? String(hrUser._id) : "",
    employee_id: hrUser?.employee_id || "",
    employee_name: hrUser?.name || "",
    finger: fingerSlot,
    timeout_s: 60,
  };
  if (Number.isFinite(page) && page >= 1) {
    payload.location = page;
  }

  const mqttSent = publishAttendanceDown(device.hardware_id, payload);
  if (!mqttSent) {
    const error = new Error("MQTT broker not connected — cannot start enroll.");
    error.statusCode = 503;
    throw error;
  }

  console.log(
    `enroll start finger=${fingerSlot}/2 device=${device.device_id} user=${payload.employee_name || payload.hr_user_id || "?"}`,
  );

  return {
    success: true,
    mqtt_sent: true,
    device_id: device.device_id,
    finger: fingerSlot,
    location: payload.location || null,
    message: `Enroll finger ${fingerSlot}/2 started. Place that finger twice on the sensor.`,
  };
};

export const processFingerprintEnrollResult = async ({
  device,
  ok,
  card_id,
  location,
  hr_user_id,
  employee_id,
  message,
  finger = 1,
}) => {
  const card = normalizeCardId(card_id);
  const success = Boolean(ok) && Boolean(card);
  const fingerSlot = Number(finger) === 2 ? 2 : 1;
  const field = fingerSlot === 2 ? "rfid_card_id_2" : "rfid_card_id";

  let hrUser = null;
  if (success && hr_user_id) {
    hrUser = await HRUser.findOne({ _id: hr_user_id, is_delete: false });
    if (hrUser) {
      const conflict = await HRUser.findOne({
        is_delete: false,
        _id: { $ne: hrUser._id },
        location: hrUser.location,
        $or: [{ rfid_card_id: card }, { rfid_card_id_2: card }],
      });
      if (conflict) {
        return {
          success: false,
          message: `Card ${card} already assigned to another employee at ${conflict.location}.`,
        };
      }
      const other =
        fingerSlot === 2 ? hrUser.rfid_card_id : hrUser.rfid_card_id_2;
      if (other && normalizeCardId(other) === card) {
        return {
          success: false,
          message: "Both fingers must map to different template ids.",
        };
      }
      hrUser[field] = card;
      await hrUser.save();
    }
  }

  const payload = {
    phase: success ? "done" : "failed",
    ok: success,
    device_id: device?.device_id || "",
    hr_user_id: hr_user_id ? String(hr_user_id) : "",
    employee_id: employee_id || hrUser?.employee_id || "",
    employee_name: hrUser?.name || "",
    card_id: card || "",
    finger: fingerSlot,
    field,
    location: location ?? null,
    message:
      message ||
      (success
        ? `Finger ${fingerSlot}/2 enrolled as ${card}`
        : "Fingerprint enroll failed"),
  };

  console.log(`enroll ${payload.phase}: ${payload.message}`);

  if (device?.hardware_id) {
    publishAttendanceDown(device.hardware_id, {
      a: "enroll_result",
      ok: success,
      success,
      c: card,
      card_id: card,
      finger: fingerSlot,
      message: payload.message,
    });
  }

  return { success, ...payload };
};

export const handleAttendanceMqttUp = async (data) => {
  const action = String(data.a || data.action || "").toLowerCase();
  const hardwareId = data.hw || data.hardware_id || data.hardwareId;
  const hwId = normalizeHardwareId(hardwareId);

  if (action === "register") {
    let created = false;
    if (hwId) {
      const existing = await AttendanceDevice.findOne({
        hardware_id: hwId,
        is_delete: false,
      });
      created = !existing;
    }

    let device;
    try {
      device = await processAttendanceHeartbeat(buildHeartbeatPayload(data));
    } catch (error) {
      publishAttendanceDown(hwId, {
        a: "register",
        ok: false,
        success: false,
        message: error.message || "Registration failed",
      });
      return { success: false, message: error.message };
    }

    publishAttendanceDown(hwId, {
      a: "register",
      ok: true,
      success: true,
      created,
      device_id: device.device_id,
      device_name: device.name || device.device_id,
      k: device.api_key,
      device_key: device.api_key,
      message: created
        ? "Device created on server"
        : "Device already registered",
    });

    return { success: true, created, device_id: device.device_id };
  }

  if (action === "heartbeat") {
    return processAttendanceHeartbeat(buildHeartbeatPayload(data));
  }

  if (action !== "tap" && action !== "enroll_result") {
    return null;
  }

  const deviceId = data.device_id || data.deviceId || data.d;
  const deviceKey = data.device_key || data.deviceKey || data.k;

  const authorizedDevice = await resolveAuthorizedDevice({
    device_id: deviceId,
    device_key: deviceKey,
    hardware_id: hardwareId,
  });

  if (!authorizedDevice) {
    console.warn(
      `Attendance MQTT up ignored id=${deviceId || "?"} hw=${hardwareId || "?"}`,
    );
    return { success: false, message: "Invalid device credentials." };
  }

  // Device lost its key (fresh SD card etc.) — re-push config so it recovers.
  if (!String(deviceKey || "").trim()) {
    pushDeviceConfigToMqtt(authorizedDevice);
  }

  if (action === "enroll_result") {
    return processFingerprintEnrollResult({
      device: authorizedDevice,
      ok: data.ok ?? data.success,
      card_id: data.card_id || data.c,
      location: data.location || data.page || data.id,
      hr_user_id: data.hr_user_id || data.hrUserId,
      employee_id: data.employee_id || data.employeeId,
      message: data.message || data.msg || "",
      finger: data.finger || 1,
    });
  }

  const result = await processAttendanceTap({
    device_id: authorizedDevice.device_id,
    card_id: data.card_id || data.cardId || data.c || data.rfid_card_id,
    tapped_at: data.tapped_at || data.tappedAt || data.t || data.ts,
    latitude: data.latitude ?? data.lat ?? data.la,
    longitude: data.longitude ?? data.lng ?? data.lo,
    source: "mqtt",
  });

  publishAttendanceDown(authorizedDevice.hardware_id || hwId, {
    a: "tap",
    ok: result.success,
    success: result.success,
    type: "tap",
    message: result.message,
    employee_name: result.employee_name,
    punch_type: result.punch_type,
    card_id: result.card_id,
    device_id: result.device_id,
  });

  return result;
};

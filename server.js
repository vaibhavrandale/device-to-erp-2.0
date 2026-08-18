// Pi-local attendance server: local mosquitto → this process → MongoDB.
// MQTT: register / heartbeat / tap / enroll_result
// HTTP: HR UI enroll start → local MQTT a:enroll (same broker as the reader)
import "dotenv/config";
import http from "http";
import mongoose from "mongoose";
import mqtt from "mqtt";
import {
  handleAttendanceMqttUp,
  requestFingerprintEnroll,
  setDownPublisher,
} from "./attendance.js";

const MONGODB_URI = process.env.MONGODB_URI;
const MQTT_URL = process.env.MQTT_URL || "mqtt://127.0.0.1:1883";
const HTTP_PORT = Number(process.env.HTTP_PORT || 3000);
const TOPIC_UP = "hr/attendance/up";

if (!MONGODB_URI) {
  console.error("MONGODB_URI is not set — deployment config was not installed");
  process.exit(1);
}

await mongoose.connect(MONGODB_URI);
console.log("Connected to MongoDB");

const client = mqtt.connect(MQTT_URL, {
  reconnectPeriod: 5000,
  connectTimeout: 30000,
  keepalive: 60,
});

setDownPublisher((topic, body) => {
  if (!client.connected) return false;
  client.publish(topic, body, { qos: 1 });
  return true;
});

client.on("connect", () => {
  console.log(`Connected to MQTT broker ${MQTT_URL}`);
  client.subscribe(TOPIC_UP, (err) => {
    if (err) {
      console.error(`MQTT subscribe failed: ${err.message}`);
      return;
    }
    console.log(`Subscribed to ${TOPIC_UP}`);
  });
});

client.on("error", (err) => {
  console.error(`MQTT error: ${err.message}`);
});

client.on("message", async (topic, message) => {
  if (topic !== TOPIC_UP) return;
  try {
    const data = JSON.parse(message.toString());
    const result = await handleAttendanceMqttUp(data);
    if (result?.type === "tap") {
      console.log(
        `tap ${result.card_id || "?"} → ${result.success ? result.message : `REJECTED: ${result.message}`}`,
      );
    }
  } catch (err) {
    console.error(`Attendance MQTT handler error: ${err.message}`);
  }
});

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

const sendJson = (res, code, body) => {
  res.writeHead(code, { "Content-Type": "application/json", ...cors });
  res.end(JSON.stringify(body));
};

const readJsonBody = async (req) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) return {};
  return JSON.parse(raw);
};

const httpServer = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, cors);
    res.end();
    return;
  }

  const path = new URL(req.url || "/", "http://localhost").pathname;

  if (req.method === "GET" && (path === "/" || path === "/health")) {
    sendJson(res, 200, {
      ok: true,
      mqtt: Boolean(client.connected),
      mongo: mongoose.connection.readyState === 1,
    });
    return;
  }

  if (
    req.method === "POST" &&
    path.endsWith("/fingerprint/enroll")
  ) {
    try {
      const body = await readJsonBody(req);
      const result = await requestFingerprintEnroll(body);
      sendJson(res, 200, result);
    } catch (err) {
      sendJson(res, err.statusCode || 500, {
        success: false,
        message: err.message,
      });
    }
    return;
  }

  sendJson(res, 404, { success: false, message: "Not found" });
});

httpServer.listen(HTTP_PORT, "0.0.0.0", () => {
  console.log(`HTTP enroll API on :${HTTP_PORT} (LAN)`);
});

// Pi-local attendance server: local mosquitto → this process → MongoDB.
// One job: turn hr/attendance/up MQTT messages into AttendancePunch inserts.
import "dotenv/config";
import mongoose from "mongoose";
import mqtt from "mqtt";
import { handleAttendanceMqttUp, setDownPublisher } from "./attendance.js";

const MONGODB_URI = process.env.MONGODB_URI;
const MQTT_URL = process.env.MQTT_URL || "mqtt://127.0.0.1:1883";
const TOPIC_UP = "hr/attendance/up";

if (!MONGODB_URI) {
  console.error("MONGODB_URI is not set — create .env (see .env.example)");
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

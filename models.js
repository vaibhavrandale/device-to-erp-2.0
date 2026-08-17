// Copied from taypro-console-backend/attendance/*.model.js — same collections,
// so punches inserted here look identical to ones inserted by the cloud backend.
import mongoose from "mongoose";

const attendanceDeviceSchema = new mongoose.Schema(
  {
    hardware_id: {
      type: String,
      trim: true,
      lowercase: true,
      unique: true,
      sparse: true,
    },
    device_id: {
      type: String,
      required: true,
      unique: true,
      trim: true,
      lowercase: true,
    },
    name: { type: String, trim: true, default: "" },
    location: {
      type: String,
      enum: ["office", "factory"],
      default: "office",
    },
    wifi_ssid: { type: String, trim: true, default: "" },
    wifi_password: { type: String, trim: true, default: "" },
    latitude: { type: Number, default: null },
    longitude: { type: Number, default: null },
    api_key: { type: String, required: true },
    setup_status: {
      type: String,
      enum: ["pending_setup", "active"],
      default: "pending_setup",
    },
    config_pending: { type: Boolean, default: false },
    config_pending_at: { type: Date, default: null },
    status: {
      type: String,
      enum: ["online", "offline"],
      default: "offline",
    },
    last_seen_at: { type: Date, default: null },
    last_seen_via: {
      type: String,
      enum: ["mqtt", "http"],
      default: null,
    },
    ip: { type: String, default: "" },
    pending_sync_count: { type: Number, default: 0 },
    is_active: { type: Boolean, default: true },
    is_delete: { type: Boolean, default: false },
  },
  { timestamps: true },
);

export const AttendanceDevice = mongoose.model(
  "AttendanceDevice",
  attendanceDeviceSchema,
);

const attendancePunchSchema = new mongoose.Schema(
  {
    tap_id: { type: String, required: true, unique: true, trim: true },
    device_id: { type: String, required: true, trim: true, lowercase: true },
    card_id: { type: String, required: true, trim: true, uppercase: true },
    employee_id: { type: String, default: "" },
    employee_name: { type: String, default: "" },
    employee_email: { type: String, default: "", trim: true, lowercase: true },
    hr_user_id: {
      type: mongoose.Schema.Types.ObjectId,
      ref: "HRUser",
      default: null,
    },
    punch_type: {
      type: String,
      enum: ["check_in", "check_out", "unknown"],
      default: "unknown",
    },
    tapped_at: { type: Date, required: true },
    latitude: { type: Number, default: null },
    longitude: { type: Number, default: null },
    source: { type: String, enum: ["mqtt", "http"], default: "http" },
    is_delete: { type: Boolean, default: false },
    taypro_app_transferred: { type: Boolean, default: false },
    taypro_app_checkin_name: { type: String, default: "", trim: true },
    taypro_app_transferred_at: { type: Date, default: null },
    taypro_app_transfer_label: { type: String, default: "", trim: true },
    taypro_app_transfer_error: { type: String, default: "", trim: true },
  },
  { timestamps: true },
);

attendancePunchSchema.index({ device_id: 1, tapped_at: -1 });
attendancePunchSchema.index({ card_id: 1, tapped_at: -1 });
attendancePunchSchema.index({ taypro_app_transferred: 1, tapped_at: 1 });

export const AttendancePunch = mongoose.model(
  "AttendancePunch",
  attendancePunchSchema,
);

// Read-only here (card → employee lookup); schema kept identical anyway.
const hrUserSchema = new mongoose.Schema(
  {
    name: { type: String, required: true, trim: true },
    email: { type: String, required: true, trim: true, lowercase: true },
    employee_id: { type: String, required: true, trim: true, uppercase: true },
    rfid_card_id: { type: String, required: true, trim: true, uppercase: true },
    rfid_card_id_2: { type: String, trim: true, uppercase: true, default: "" },
    department: { type: String, required: true, trim: true },
    location: {
      type: String,
      required: true,
      enum: ["office", "factory", "wfh"],
    },
    phone: { type: String, trim: true, default: "" },
    designation: { type: String, trim: true, default: "" },
    is_active: { type: Boolean, default: true },
    is_delete: { type: Boolean, default: false },
    last_activity: { type: Array, default: [] },
  },
  { timestamps: true },
);

hrUserSchema.index({ rfid_card_id: 1 });
hrUserSchema.index({ rfid_card_id_2: 1 });

export const HRUser = mongoose.model("HRUser", hrUserSchema);

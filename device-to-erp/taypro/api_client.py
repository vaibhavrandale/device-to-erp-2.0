"""Console REST session. Sign-in stores the JWT; later calls send it as the authtoken cookie."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def token_from_body(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return ""
    return str(data.get("token") or "")


class ConsoleApi:
    def __init__(self, base: str, email: str, password: str, token_path: Path):
        self.base = str(base or "").rstrip("/")
        self.email = email
        self.password = password
        self.token_path = Path(token_path)
        self.token = ""
        self.logged_in = False
        if self.token_path.exists():
            self.token = self.token_path.read_text(encoding="utf-8").strip()

    def _store(self, token: str) -> None:
        self.token = token
        self.logged_in = True
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(token, encoding="utf-8")

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        auth: bool = True,
        timeout_s: float = 12,
    ) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        if auth and self.token:
            req.add_header("Cookie", f"authtoken={self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as res:
                raw = res.read().decode("utf-8", "replace")
                status = res.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            status = exc.code
        except urllib.error.URLError as exc:
            return 0, {"success": False, "message": str(exc.reason or exc)}
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"success": False, "message": raw[:200]}
        if not isinstance(payload, dict):
            payload = {"success": False, "message": "Bad response"}
        return status, payload

    def sign_in(self, timeout_s: float = 20) -> tuple[bool, str]:
        status, payload = self._request(
            "POST",
            "/auth/sign-in",
            {"email": self.email, "password": self.password},
            auth=False,
            timeout_s=timeout_s,
        )
        token = token_from_body(payload)
        if status != 200 or not token:
            self.logged_in = False
            return False, str(payload.get("message") or f"HTTP {status}")
        self._store(token)
        return True, str(payload.get("message") or "Signed in")

    def punch(self, fp_id: str, device_id: str = "", timeout_s: float = 12) -> dict[str, Any]:
        body: dict[str, Any] = {"finger_id": fp_id}
        if device_id:
            body["device_id"] = device_id

        def once() -> tuple[int, dict]:
            return self._request(
                "POST",
                "/hr/attendance/punch",
                body,
                timeout_s=timeout_s,
            )

        status, payload = once()
        if status == 401:
            ok, message = self.sign_in(timeout_s=timeout_s)
            if not ok:
                return {
                    "ok": False,
                    "employee": "",
                    "punch_type": "",
                    "message": message or "Login failed",
                    "http_status": 401,
                }
            status, payload = once()

        if status == 0:
            return {
                "ok": False,
                "employee": "",
                "punch_type": "",
                "message": str(payload.get("message") or "Network error"),
                "http_status": 0,
            }

        return {
            "ok": status == 200 and bool(payload.get("success")),
            "employee": str(payload.get("employee_name") or ""),
            "punch_type": str(payload.get("punch_type") or ""),
            "message": str(payload.get("message") or ""),
            "http_status": status,
        }

    def heartbeat(
        self,
        *,
        hardware_id: str,
        device_id: str = "",
        device_name: str = "",
        device_key: str = "",
        ip: str = "",
        status: str = "online",
        timeout_s: float = 12,
    ) -> tuple[bool, str]:
        """Mark this Pi online or offline. Same cookie JWT as punch."""
        body = {
            "hardware_id": hardware_id,
            "device_id": device_id,
            "device_name": device_name,
            "device_key": device_key,
            "wifi_ssid": "raspi",
            "ip": ip,
            "status": "offline" if status == "offline" else "online",
        }

        def once() -> tuple[int, dict]:
            return self._request(
                "POST",
                "/hr/attendance/heartbeat",
                body,
                timeout_s=timeout_s,
            )

        http_status, payload = once()
        if http_status == 401:
            ok, message = self.sign_in(timeout_s=timeout_s)
            if not ok:
                return False, message or "Login failed"
            http_status, payload = once()
        if http_status == 200 and payload.get("success"):
            return True, str(payload.get("message") or status)
        return False, str(payload.get("message") or f"HTTP {http_status}")


def _selfcheck() -> None:
    assert token_from_body({"success": True, "data": {"user": {}, "token": "jwt"}}) == "jwt"
    assert token_from_body({}) == ""
    assert token_from_body({"data": "nope"}) == ""
    print("api_client selfcheck ok")


if __name__ == "__main__":
    _selfcheck()

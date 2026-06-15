# -*- coding: utf-8 -*-
"""HTTP client used by the standalone UI to call Houdini via bridge."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Optional, Tuple

try:
    import requests
except Exception:  # pragma: no cover - handled at runtime in Houdini bundles
    requests = None  # type: ignore


class RemoteHoudiniMCPClient:
    """Compatibility wrapper for the existing HoudiniMCP API."""

    def __init__(self, bridge_url: str, default_timeout: float = 12.0):
        self.bridge_url = (bridge_url or "").rstrip("/")
        self.default_timeout = float(default_timeout or 12.0)
        self._stop_event = None

    def set_stop_event(self, event):
        self._stop_event = event

    @property
    def is_remote(self) -> bool:
        return True

    def health(self) -> Dict[str, Any]:
        if requests is None:
            return {"ok": False, "error": "requests is not available"}
        try:
            resp = requests.get(f"{self.bridge_url}/health", timeout=3.0)
            return resp.json()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if requests is None:
            return {"success": False, "error": "requests is not available in standalone UI"}
        if self._stop_event is not None:
            try:
                if self._stop_event.is_set():
                    return {"success": False, "error": "User requested stop"}
            except Exception:
                pass

        timeout = float((arguments or {}).get("_bridge_timeout", self.default_timeout))
        payload = {
            "tool_name": tool_name,
            "arguments": arguments or {},
            "request_id": uuid.uuid4().hex,
            "timeout": timeout,
        }
        try:
            resp = requests.post(
                f"{self.bridge_url}/tool",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=timeout + 2.0,
            )
            data = resp.json()
            if isinstance(data, dict):
                data.setdefault("success", False)
                data.setdefault("meta", {})
                data["meta"].setdefault("remote", True)
                data["meta"].setdefault("bridge_url", self.bridge_url)
                return data
            return {"success": False, "error": f"Invalid bridge response: {type(data).__name__}"}
        except Exception as e:
            return {
                "success": False,
                "error": f"Houdini bridge unavailable or busy: {e}",
                "meta": {"bridge_url": self.bridge_url, "remote": True},
            }

    def scene_context(self, timeout: float = 3.0) -> Dict[str, Any]:
        if requests is None:
            return {}
        try:
            resp = requests.post(
                f"{self.bridge_url}/context",
                data=json.dumps({"timeout": timeout}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                timeout=timeout + 1.0,
            )
            data = resp.json()
            if data.get("success") and isinstance(data.get("result"), dict):
                return data["result"]
            return {}
        except Exception:
            return {}

    def describe_selection(self, limit: int = 3, include_all_params: bool = False) -> Tuple[bool, str]:
        result = self.execute_tool("read_selection", {
            "limit": limit,
            "include_all_params": include_all_params,
        })
        if result.get("success"):
            return True, str(result.get("result", ""))
        return False, str(result.get("error", "Failed to read selection"))

    def get_network_structure_text(self, network_path: Optional[str] = None) -> Tuple[bool, str]:
        args: Dict[str, Any] = {}
        if network_path:
            args["network_path"] = network_path
        result = self.execute_tool("get_network_structure", args)
        if result.get("success"):
            return True, str(result.get("result", ""))
        return False, str(result.get("error", "Failed to read network"))

    def get_network_structure(self, network_path: Optional[str] = None):
        args: Dict[str, Any] = {}
        if network_path:
            args["network_path"] = network_path
        result = self.execute_tool("get_network_structure", args)
        if result.get("success"):
            return True, result.get("data") or {"text": result.get("result", "")}
        return False, {"error": result.get("error", "Failed to read network")}

    def _tool_list_skills(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self.execute_tool("list_skills", args or {})

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            health = self.health()
            if health.get("success") or health.get("ok"):
                return True
            time.sleep(0.2)
        return False

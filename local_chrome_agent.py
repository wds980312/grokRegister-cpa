#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本机 Chrome 拉起助手。

Docker 里的注册进程无法直接 open macOS 的 Chrome，因此在宿主机跑本助手：
控制台选择「本地 Chrome 无痕」并点开始时，容器会 POST /ensure，由助手执行
start-local-chrome.sh。

用法（本机终端，保持运行即可）:
  ./start-local-chrome-agent.sh
  # 或
  python3 local_chrome_agent.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
START_SCRIPT = ROOT / "start-local-chrome.sh"
DEFAULT_AGENT_HOST = "127.0.0.1"
DEFAULT_AGENT_PORT = 18083
DEFAULT_CHROME_PORT = 9222


def env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def chrome_port() -> int:
    raw = str(os.environ.get("LOCAL_CHROME_PORT", "") or "").strip()
    if raw.isdigit():
        return int(raw)
    # 兼容 config.json 里的 local_chrome_debug_address
    cfg = ROOT / "config.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            address = str(data.get("local_chrome_debug_address", "") or "").strip()
            if address:
                parsed = urlparse(address if "://" in address else f"http://{address}")
                if parsed.port:
                    return int(parsed.port)
        except Exception:
            pass
    return DEFAULT_CHROME_PORT


def minimize_chrome(port: int) -> bool:
    """尝试把调试 Chrome 最小化到程序坞，失败忽略。"""
    script = ROOT / "minimize_local_chrome.py"
    if not script.is_file():
        return False
    try:
        completed = subprocess.run(
            [sys.executable, str(script), str(port)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return completed.returncode == 0
    except Exception:
        return False


def probe_local(port: int, timeout: float = 1.5) -> dict | None:
    url = f"http://127.0.0.1:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def ensure_chrome(port: int, force_restart: bool = False) -> dict:
    existing = probe_local(port)
    if existing and not force_restart:
        minimized = minimize_chrome(port)
        return {
            "ok": True,
            "already_running": True,
            "port": port,
            "browser": existing.get("Browser", ""),
            "minimized": minimized,
        }

    if not START_SCRIPT.is_file():
        return {
            "ok": False,
            "error": f"找不到启动脚本: {START_SCRIPT}",
            "port": port,
        }

    try:
        command = ["/bin/sh", str(START_SCRIPT), str(port)]
        if force_restart:
            command.append("--reset")
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "启动 Chrome 超时", "port": port}
    except Exception as exc:
        return {"ok": False, "error": f"执行启动脚本失败: {exc}", "port": port}

    deadline = time.time() + 15
    while time.time() < deadline:
        existing = probe_local(port)
        if existing:
            minimized = minimize_chrome(port)
            return {
                "ok": True,
                "already_running": False,
                "force_restarted": force_restart,
                "port": port,
                "browser": existing.get("Browser", ""),
                "stdout": (completed.stdout or "").strip(),
                "minimized": minimized,
            }
        time.sleep(0.25)

    detail = (completed.stderr or completed.stdout or "").strip()
    return {
        "ok": False,
        "error": detail or f"Chrome 未在端口 {port} 就绪",
        "port": port,
        "returncode": completed.returncode,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stdout.write("[agent] " + (fmt % args) + "\n")
        sys.stdout.flush()

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in {"/", "/health"}:
            port = chrome_port()
            ready = probe_local(port) is not None
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "local-chrome-agent",
                    "chrome_port": port,
                    "chrome_ready": ready,
                },
            )
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path in {"/ensure", "/start"}:
            result = ensure_chrome(chrome_port())
            self._send_json(200 if result.get("ok") else 503, result)
            return
        if path in {"/reset", "/restart"}:
            result = ensure_chrome(chrome_port(), force_restart=True)
            self._send_json(200 if result.get("ok") else 503, result)
            return
        self._send_json(404, {"ok": False, "error": "not found"})


def main():
    host = str(os.environ.get("LOCAL_CHROME_AGENT_HOST", DEFAULT_AGENT_HOST) or DEFAULT_AGENT_HOST)
    port = env_int("LOCAL_CHROME_AGENT_PORT", DEFAULT_AGENT_PORT)
    chrome = chrome_port()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[*] Local Chrome 助手已启动: http://{host}:{port}", flush=True)
    print(f"[*] 目标 Chrome CDP 端口: {chrome}", flush=True)
    print("[*] 控制台选「本地 Chrome 无痕」后点开始，将自动尝试拉起 Chrome", flush=True)
    print("[*] 保持此终端运行；Ctrl+C 退出", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] 助手已退出", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

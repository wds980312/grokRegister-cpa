#!/usr/bin/env python3
"""Local web control panel for the Docker registration worker."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
CONFIG_FILE = APP_DIR / "config.json"
HOST = os.environ.get("WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("WEB_PORT", "18081"))
MAX_LOG_LINES = 500
JWT_PATTERN = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
STATS_PATTERN = re.compile(r"成功\s*(\d+)\s*\|\s*失败\s*(\d+)")
SUPPORTED_BROWSER_BACKENDS = {"bitbrowser", "local_chrome", "chromium"}


class TaskAlreadyRunning(RuntimeError):
    pass


def parse_count(value) -> int:
    if isinstance(value, bool):
        raise ValueError("注册数量必须是正整数")
    if isinstance(value, int):
        count = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if not raw.isdigit():
            raise ValueError("注册数量必须是正整数")
        count = int(raw)
    else:
        raise ValueError("注册数量必须是正整数")
    if count < 1:
        raise ValueError("注册数量必须大于 0")
    return count


def parse_browser_backend(value) -> str:
    backend = str(value or "").strip().lower()
    if backend not in SUPPORTED_BROWSER_BACKENDS:
        raise ValueError("浏览器后端必须是 bitbrowser、local_chrome 或 chromium")
    return backend


def redact_log_line(line: str) -> str:
    """Remove JWT-like values before a CLI line reaches the browser."""
    line = JWT_PATTERN.sub("[已隐藏]", str(line).rstrip("\r\n"))
    if "邮箱credential(jwt)" in line and ":" in line:
        prefix = line.split(":", 1)[0]
        return f"{prefix}: [已隐藏]"
    return line


def _public_config() -> tuple[str, int]:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    provider = str(data.get("email_provider") or "duckmail")
    try:
        default_count = parse_count(data.get("register_count", 1))
    except ValueError:
        default_count = 1
    return provider, default_count


def _configured_browser_backend() -> str:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return parse_browser_backend(data.get("browser_backend", "chromium"))
    except (OSError, ValueError):
        return "chromium"


def _save_browser_backend(backend: str) -> None:
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        raise ValueError("config.json 必须是 JSON 对象")
    data["browser_backend"] = backend
    CONFIG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RegistrationManager:
    def __init__(self, popen_factory=None, app_dir: Path | None = None):
        self._popen_factory = popen_factory or subprocess.Popen
        self._app_dir = Path(app_dir or APP_DIR)
        self._lock = threading.RLock()
        self._process = None
        self._reader_thread = None
        self._state = "idle"
        self._count = 0
        self._success = 0
        self._failed = 0
        self._logs = deque(maxlen=MAX_LOG_LINES)
        self._cli_exit_sent = False

    def _is_active_locked(self) -> bool:
        return self._state in ("starting", "running", "stopping")

    def _append_log_locked(self, line: str) -> None:
        line = redact_log_line(line)
        if not line:
            return
        self._logs.append(line)
        match = STATS_PATTERN.search(line)
        if match:
            self._success = int(match.group(1))
            self._failed = int(match.group(2))

    def _append_log(self, line: str) -> None:
        with self._lock:
            self._append_log_locked(line)

    def _subprocess_env(self):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["GROK_DOCKER"] = "1"
        return env

    def start(self, count, browser_backend=None):
        count = parse_count(count)
        backend = None if browser_backend in (None, "") else parse_browser_backend(browser_backend)
        with self._lock:
            if self._is_active_locked():
                raise TaskAlreadyRunning("已有注册任务正在运行")
            if backend is not None:
                _save_browser_backend(backend)
            self._state = "starting"
            self._count = count
            self._success = 0
            self._failed = 0
            self._cli_exit_sent = False
            self._logs.clear()
            self._append_log_locked(f"[Web] 准备启动注册任务，数量: {count}")
            command = [sys.executable, str(self._app_dir / "grok_register_ttk.py"), "cli"]
            try:
                process = self._popen_factory(
                    command,
                    cwd=str(self._app_dir),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=self._subprocess_env(),
                )
                process.stdin.write(f"start\n{count}\n")
                process.stdin.flush()
            except Exception:
                self._state = "error"
                self._process = None
                self._append_log_locked("[Web] 注册任务启动失败")
                raise

            self._process = process
            self._state = "running"
            self._reader_thread = threading.Thread(
                target=self._consume_output,
                args=(process,),
                name="registration-log-reader",
                daemon=True,
            )
            self._reader_thread.start()
            return self.snapshot()

    def _consume_output(self, process) -> None:
        try:
            for raw_line in iter(process.stdout.readline, ""):
                line = redact_log_line(raw_line)
                should_exit_cli = False
                with self._lock:
                    self._append_log_locked(line)
                    if (
                        self._process is process
                        and self._state == "running"
                        and not self._cli_exit_sent
                        and "任务结束。成功" in line
                    ):
                        self._cli_exit_sent = True
                        should_exit_cli = True
                if should_exit_cli:
                    try:
                        process.stdin.write("q\n")
                        process.stdin.flush()
                    except (AttributeError, OSError):
                        pass
            returncode = process.wait()
        except Exception as exc:
            self._append_log(f"[Web] 读取注册任务日志失败: {exc}")
            returncode = 1

        with self._lock:
            if self._process is not process:
                return
            was_stopping = self._state == "stopping"
            self._process = None
            self._reader_thread = None
            if was_stopping:
                self._state = "idle"
                self._append_log_locked("[Web] 注册任务已停止")
            elif returncode == 0:
                self._state = "completed"
                self._append_log_locked("[Web] 注册任务已完成")
            else:
                self._state = "error"
                self._append_log_locked(f"[Web] 注册任务异常结束，退出码: {returncode}")

    def _force_stop(self, process) -> None:
        time.sleep(15)
        with self._lock:
            if self._process is not process or self._state != "stopping":
                return
        try:
            if process.poll() is None:
                process.terminate()
        except Exception:
            pass

    def stop(self):
        with self._lock:
            process = self._process
            if process is None or not self._is_active_locked():
                return self.snapshot()
            self._state = "stopping"
            self._append_log_locked("[Web] 正在停止注册任务...")
            try:
                process.send_signal(signal.SIGINT)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.stdin.close()
            except (AttributeError, OSError):
                pass
            threading.Thread(
                target=self._force_stop,
                args=(process,),
                name="registration-stop-watchdog",
                daemon=True,
            ).start()
            return self.snapshot()

    def snapshot(self):
        provider, default_count = _public_config()
        with self._lock:
            return {
                "state": self._state,
                "count": self._count,
                "default_count": default_count,
                "success": self._success,
                "failed": self._failed,
                "provider": provider,
                "browser_backend": _configured_browser_backend(),
                "logs": list(self._logs),
            }


class ControlRequestHandler(BaseHTTPRequestHandler):
    server_version = "GrokRegisterWeb/1.0"

    @property
    def manager(self):
        return self.server.manager

    def log_message(self, format, *args):
        return

    def _send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("请求体无效")
        if length > 64 * 1024:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            raise ValueError("请求体必须是 JSON")
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            self._send_json(200, self.manager.snapshot())
            return
        files = {
            "/": (WEB_DIR / "index.html", "text/html; charset=utf-8"),
            "/static/app.js": (WEB_DIR / "app.js", "text/javascript; charset=utf-8"),
            "/static/style.css": (WEB_DIR / "style.css", "text/css; charset=utf-8"),
        }
        file_info = files.get(path)
        if file_info is None:
            self._send_json(404, {"error": "not found"})
            return
        file_path, content_type = file_info
        try:
            body = file_path.read_bytes()
        except OSError:
            self._send_json(500, {"error": "页面文件不可用"})
            return
        self._send_bytes(200, content_type, body)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/start":
            try:
                data = self._read_json()
                snapshot = self.manager.start(data.get("count"), data.get("browser_backend"))
            except TaskAlreadyRunning as exc:
                self._send_json(409, {"error": str(exc)})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except Exception:
                self._send_json(500, {"error": "注册任务启动失败"})
            else:
                self._send_json(202, snapshot)
            return
        if path == "/api/stop":
            self._send_json(200, self.manager.stop())
            return
        self._send_json(404, {"error": "not found"})


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, manager):
        self.manager = manager
        super().__init__(server_address, ControlRequestHandler)


def create_server(host: str = HOST, port: int = PORT, manager=None):
    return ControlServer((host, port), manager or RegistrationManager())


def main() -> None:
    server = create_server()
    print(f"[*] Web 控制台已启动: http://127.0.0.1:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.manager.stop()
        server.server_close()


if __name__ == "__main__":
    main()

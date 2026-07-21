import threading
import unittest
from pathlib import Path
import json
import urllib.request
from unittest.mock import patch

import web_server


class BlockingStdout:
    def __init__(self):
        self.release = threading.Event()

    def readline(self):
        self.release.wait(timeout=2)
        return ""


class SequenceStdout(BlockingStdout):
    def __init__(self, lines):
        super().__init__()
        self.lines = iter(lines)

    def readline(self):
        try:
            return next(self.lines)
        except StopIteration:
            return super().readline()


class FakeStdin:
    def __init__(self):
        self.written = ""
        self.closed = False

    def write(self, value):
        self.written += value

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.stdin = FakeStdin()
        self.stdout = BlockingStdout()
        self.returncode = None
        self.signals = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return self.returncode

    def send_signal(self, value):
        self.signals.append(value)


class WebServerTests(unittest.TestCase):
    def test_parse_browser_backend_accepts_supported_values(self):
        self.assertEqual(web_server.parse_browser_backend("bitbrowser"), "bitbrowser")
        self.assertEqual(web_server.parse_browser_backend(" local_chrome "), "local_chrome")
        self.assertEqual(web_server.parse_browser_backend("chromium"), "chromium")

    def test_parse_browser_backend_rejects_unknown_values(self):
        with self.assertRaises(ValueError):
            web_server.parse_browser_backend("unknown")

    def test_parse_count_accepts_positive_integer_values(self):
        self.assertEqual(web_server.parse_count(3), 3)
        self.assertEqual(web_server.parse_count(" 4 "), 4)

    def test_parse_count_rejects_invalid_values(self):
        for value in (True, False, 0, -1, "", "abc", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    web_server.parse_count(value)

    def test_redact_log_line_hides_jwt_debug_values(self):
        line = "[Debug] 邮箱credential(jwt): eyJheader.eyJpayload.signature"

        redacted = web_server.redact_log_line(line)

        self.assertNotIn("eyJheader", redacted)
        self.assertIn("[已隐藏]", redacted)

    def test_start_writes_cli_command_and_count(self):
        process = FakeProcess()
        with patch.object(web_server.subprocess, "Popen", return_value=process) as popen:
            manager = web_server.RegistrationManager()
            manager.start(3)

        self.assertEqual(process.stdin.written, "start\n3\n")
        popen.assert_called_once()
        self.assertEqual(manager.snapshot()["state"], "running")
        process.stdout.release.set()

    def test_running_task_rejects_second_start_and_stop_requests_signal(self):
        process = FakeProcess()
        with patch.object(web_server.subprocess, "Popen", return_value=process):
            manager = web_server.RegistrationManager()
            manager.start(2)

            with self.assertRaises(web_server.TaskAlreadyRunning):
                manager.start(1)

            snapshot = manager.stop()

        self.assertEqual(snapshot["state"], "stopping")
        self.assertTrue(process.signals)
        self.assertTrue(process.stdin.closed)
        process.stdout.release.set()

    def test_task_completion_tells_cli_subprocess_to_exit(self):
        process = FakeProcess()
        process.stdout = SequenceStdout(["[11:00:00] [*] 任务结束。成功 1 | 失败 0\n"])
        with patch.object(web_server.subprocess, "Popen", return_value=process):
            manager = web_server.RegistrationManager()
            manager.start(1)

            for _ in range(20):
                if "q\n" in process.stdin.written:
                    break
                threading.Event().wait(0.01)

        self.assertIn("q\n", process.stdin.written)
        process.stdout.release.set()

    def test_home_and_status_routes_are_available(self):
        manager = web_server.RegistrationManager()
        server = web_server.create_server("127.0.0.1", 0, manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base_url + "/", timeout=2) as response:
                html = response.read().decode("utf-8")
            with urllib.request.urlopen(base_url + "/api/status", timeout=2) as response:
                status = json.loads(response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()

        self.assertIn("GROK REGISTER", html)
        self.assertIn('id="browserBackend"', html)
        self.assertEqual(status["state"], "idle")


    def test_web_app_does_not_overwrite_form_on_every_status_poll(self):
        js = Path("web/app.js").read_text(encoding="utf-8")
        self.assertIn("controlsHydrated", js)
        self.assertIn("if (!controlsHydrated)", js)
        # 轮询不应每秒强制 browserBackend.value = status.browser_backend
        self.assertEqual(js.count("browserBackend.value = status.browser_backend"), 1)

    def test_web_log_panel_preserves_manual_scroll_and_supports_copy(self):
        js = Path("web/app.js").read_text(encoding="utf-8")
        html = Path("web/index.html").read_text(encoding="utf-8")
        css = Path("web/style.css").read_text(encoding="utf-8")

        self.assertIn("function renderLogs(logs)", js)
        self.assertIn("logOutput.append(document.createTextNode", js)
        self.assertIn("if (shouldStickToBottom)", js)
        self.assertNotIn('logOutput.textContent = status.logs.length ? status.logs.join("\\n")', js)
        self.assertIn("navigator.clipboard.writeText", js)
        self.assertIn('id="copyLogsButton"', html)
        self.assertIn("user-select: text", css)


if __name__ == "__main__":
    unittest.main()

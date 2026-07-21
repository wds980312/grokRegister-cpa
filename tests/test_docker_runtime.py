import os
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import grok_register_ttk as app
import local_chrome_agent as local_agent


class DockerRuntimeTests(unittest.TestCase):
    def setUp(self):
        app._force_browser_restart_next = False

    def test_docker_browser_options_include_root_safe_flags(self):
        with patch.dict(os.environ, {"GROK_DOCKER": "1"}):
            options = app.create_browser_options()

        self.assertIn("--no-sandbox", options.arguments)
        self.assertIn("--disable-dev-shm-usage", options.arguments)

    def test_docker_browser_options_enable_incognito_by_default(self):
        with patch.dict(os.environ, {"GROK_DOCKER": "1"}):
            with patch.object(app, "config", {**app.DEFAULT_CONFIG, "browser_incognito": None}):
                options = app.create_browser_options()

        self.assertIn("--incognito", options.arguments)

    def test_browser_options_allow_explicitly_disabling_incognito(self):
        with patch.dict(os.environ, {"GROK_DOCKER": "1"}):
            with patch.object(app, "config", {**app.DEFAULT_CONFIG, "browser_incognito": False}):
                options = app.create_browser_options()

        self.assertNotIn("--incognito", options.arguments)

    def test_bitbrowser_debug_address_uses_host_gateway_in_docker(self):
        with patch.dict(os.environ, {"GROK_DOCKER": "1"}):
            with patch("grok_register_ttk.socket.gethostbyname", return_value="192.0.2.10"):
                self.assertEqual(
                    app._normalize_bitbrowser_debug_address("127.0.0.1:61214"),
                    "192.0.2.10:61214",
                )

    def test_local_chrome_debug_address_uses_host_gateway_in_docker(self):
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "local_chrome_debug_address": "127.0.0.1:9222",
        }):
            with patch.dict(os.environ, {"GROK_DOCKER": "1"}):
                with patch("grok_register_ttk.socket.gethostbyname", return_value="192.0.2.10"):
                    self.assertEqual(
                        app._local_chrome_debug_address(),
                        "192.0.2.10:9222",
                    )

    def test_open_bitbrowser_profile_returns_http_debug_address(self):
        response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "success": True,
                "data": {"http": "127.0.0.1:61214"},
            },
        })()
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "bitbrowser_api_url": "http://127.0.0.1:54345",
            "bitbrowser_profile_id": "profile-1",
        }):
            with patch.object(app.requests, "post", return_value=response) as post:
                with patch("grok_register_ttk.socket.gethostbyname", return_value="192.0.2.10"):
                    address = app._open_bitbrowser_profile()

        self.assertEqual(address, "192.0.2.10:61214")
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["json"], {"id": "profile-1"})

    def test_create_cpa_device_authorization_returns_url_and_state(self):
        response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "status": "ok",
                "url": "https://accounts.x.ai/oauth2/device?user_code=TEST-CODE",
                "state": "state-1",
                "expires_in": 600,
            },
        })()
        with patch.object(app.requests, "get", return_value=response) as get:
            result = app._create_cpa_device_authorization("http://cpa:8317", "key")

        self.assertEqual(result["state"], "state-1")
        self.assertIn("user_code=TEST-CODE", result["url"])
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer key")

    def test_wait_cpa_device_authorization_accepts_authorized_status(self):
        responses = iter([
            {"status": "wait"},
            {"status": "authorized"},
        ])
        response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: next(responses),
        })()
        with patch.object(app.requests, "get", return_value=response):
            with patch.object(app.time, "sleep"):
                result = app._wait_cpa_device_authorization("http://cpa:8317", "key", "state-1")

        self.assertEqual(result["status"], "authorized")

    def test_prepare_grok_web_session_opens_grok_before_device_auth(self):
        current_page = type("Page", (), {"get": Mock()})()
        with patch.object(app, "page", current_page):
            with patch.object(app, "open_step_tab") as open_tab:
                app._prepare_grok_web_session(wait_seconds=0)

        open_tab.assert_called_once()
        self.assertEqual(open_tab.call_args.args[0], "https://grok.com/")

    def test_prepare_grok_web_session_opens_sign_in_when_email_password_present(self):
        current_page = type("Page", (), {"get": Mock()})()
        with patch.object(app, "page", current_page):
            with patch.object(app, "open_step_tab") as open_tab:
                app._prepare_grok_web_session(
                    wait_seconds=0,
                    email="a@b.com",
                    password="secret",
                )

        open_tab.assert_called_once()
        self.assertIn("accounts.x.ai/sign-in", open_tab.call_args.args[0])

    def test_clear_browser_session_data_clears_cookies_cache_and_xai_storage(self):
        browser = type("Browser", (), {"_run_cdp": Mock()})()
        current_page = type("Page", (), {
            "url": "https://accounts.x.ai/oauth2/device",
            "run_js": Mock(),
        })()
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", current_page):
                with patch.object(app, "config", {
                    **app.DEFAULT_CONFIG,
                    "browser_backend": "bitbrowser",
                    "browser_clear_data": True,
                }):
                    app.clear_browser_session_data()

        commands = [call.args[0] for call in browser._run_cdp.call_args_list]
        self.assertIn("Storage.clearCookies", commands)
        self.assertNotIn("Network.clearBrowserCookies", commands)
        self.assertNotIn("Network.clearBrowserCache", commands)
        current_page.run_js.assert_called_once()

    def test_clear_browser_session_data_skips_local_chrome_entirely(self):
        browser = type("Browser", (), {"_run_cdp": Mock()})()
        current_page = type("Page", (), {
            "url": "about:blank",
            "run_js": Mock(side_effect=RuntimeError("should not run")),
        })()
        logs = []
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", current_page):
                with patch.object(app, "config", {
                    **app.DEFAULT_CONFIG,
                    "browser_backend": "local_chrome",
                    "browser_clear_data": True,
                }):
                    app.clear_browser_session_data(log_callback=logs.append)

        current_page.run_js.assert_not_called()
        browser._run_cdp.assert_not_called()
        self.assertTrue(any("重新打开" in line or "无需清理" in line for line in logs))

    def test_docker_cpa_proxy_is_empty_when_no_proxy_is_configured(self):
        env = {
            "GROK_DOCKER": "1",
            "https_proxy": "",
            "HTTPS_PROXY": "",
            "http_proxy": "",
            "HTTP_PROXY": "",
        }
        with patch.object(app, "config", {**app.DEFAULT_CONFIG, "proxy": ""}):
            with patch.dict(os.environ, env):
                self.assertEqual(app._resolve_cpa_proxy(), "")

    def test_cli_count_prompt_accepts_custom_count(self):
        with patch("builtins.input", return_value="3"):
            self.assertEqual(app.read_cli_count(1), 3)

    def test_cli_count_prompt_uses_default_on_blank_input(self):
        with patch("builtins.input", return_value=""):
            self.assertEqual(app.read_cli_count(4), 4)

    def test_cli_count_prompt_returns_none_on_quit(self):
        with patch("builtins.input", return_value="q"):
            self.assertIsNone(app.read_cli_count(1))

    def test_cli_returns_to_prompt_after_registration_task(self):
        with patch("builtins.input", side_effect=["start", "2", "q"]):
            with patch.object(app, "run_registration_cli") as run_registration:
                app.main_cli()

        self.assertEqual(run_registration.call_args_list, [call(2)])

    def test_control_page_links_to_local_browser_view(self):
        html = Path("web/index.html").read_text(encoding="utf-8")

        self.assertIn("127.0.0.1:18082/vnc.html", html)

    def test_compose_maps_local_novnc_port(self):
        compose = Path("docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn('"127.0.0.1:18082:6080"', compose)


    def test_progress_device_authorization_page_clicks_authorize(self):
        current_page = type("Page", (), {
            "run_js": Mock(return_value={
                "action": "device-action-ready",
                "text": "Authorize",
                "score": 100,
                "rect": {"left": 1, "top": 2, "width": 3, "height": 4},
            }),
        })()
        with patch.object(app, "page", current_page):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "cpa_auto_click_device": True,
            }):
                with patch.object(app, "_click_device_action_native", return_value={
                    "clicked": True,
                    "method": "drissionpage-input",
                }) as native_click:
                    result = app._progress_device_authorization_page(email="a@b.com", password="x")

        self.assertEqual(result["action"], "device-continue")
        self.assertEqual(result["text"], "Authorize")
        native_click.assert_called_once()
        current_page.run_js.assert_called_once()

    def test_device_guard_never_uses_dom_click(self):
        source = Path("grok_register_ttk.py").read_text(encoding="utf-8")
        guarded = source.split("function guardedDeviceAction()", 1)[1].split("function findEmailInput()", 1)[0]
        self.assertNotIn("node.click()", guarded)
        self.assertIn("device-action-ready", guarded)

    def test_device_action_uses_native_element_click(self):
        element = Mock()
        current_page = type("Page", (), {
            "ele": Mock(return_value=element),
            "run_js": Mock(),
        })()
        descriptor = {
            "text": "Allow",
            "score": 100,
            "token": "device-action-1",
            "rect": {"left": 10, "top": 20, "width": 100, "height": 40},
        }
        with patch.object(app, "page", current_page):
            result = app._click_device_action_native(descriptor)

        self.assertTrue(result["clicked"])
        element.click.at.assert_called_once_with()

    def test_device_action_falls_back_to_cdp_mouse_events(self):
        current_page = type("Page", (), {
            "ele": Mock(return_value=None),
            "run_js": Mock(),
            "run_cdp": Mock(),
        })()
        descriptor = {
            "text": "Allow",
            "score": 100,
            "token": "device-action-2",
            "rect": {"left": 10, "top": 20, "width": 100, "height": 40},
        }
        with patch.object(app, "page", current_page):
            result = app._click_device_action_native(descriptor)

        self.assertTrue(result["clicked"])
        commands = [call.args[0] for call in current_page.run_cdp.call_args_list]
        self.assertEqual(commands, [
            "Input.dispatchMouseEvent",
            "Input.dispatchMouseEvent",
            "Input.dispatchMouseEvent",
        ])
        self.assertEqual(current_page.run_cdp.call_args_list[1].kwargs["type"], "mousePressed")

    def test_prepare_grok_web_session_auto_exits_when_ready(self):
        current_page = type("Page", (), {
            "get": Mock(),
            "run_js": Mock(return_value={"state": "ready", "url": "https://grok.com/"}),
            "url": "https://grok.com/",
        })()
        logs = []
        with patch.object(app, "page", current_page):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "cpa_auto_click_device": True,
                "cpa_grok_web_wait_seconds": 30,
            }):
                with patch.object(app, "open_step_tab"):
                    with patch.object(app, "_run_device_page_js", side_effect=[
                        {"state": "login", "url": "https://accounts.x.ai/sign-in"},
                        {"state": "ready", "url": "https://grok.com/"},
                        {"state": "ready", "url": "https://grok.com/"},
                    ]):
                        with patch.object(app, "_progress_device_authorization_page", return_value={
                            "action": "login-fill",
                            "hasPassword": True,
                            "emailValue": "a@b.com",
                        }):
                            with patch.object(app.time, "sleep"):
                                app._prepare_grok_web_session(
                                    wait_seconds=30,
                                    email="a@b.com",
                                    password="secret",
                                    log_callback=logs.append,
                                )

        self.assertTrue(any("登录成功" in item or "就绪" in item for item in logs))
        self.assertTrue(any("填写" in item for item in logs))

    def test_wait_cpa_device_authorization_auto_clicks_while_polling(self):
        responses = iter([
            {"status": "wait"},
            {"status": "authorized"},
        ])
        response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: next(responses),
        })()
        with patch.object(app.requests, "get", return_value=response):
            with patch.object(app, "_progress_device_authorization_page", return_value={"action": "click", "text": "Continue"}) as progress:
                with patch.object(app.time, "sleep"):
                    with patch.object(app, "config", {
                        **app.DEFAULT_CONFIG,
                        "cpa_auto_click_device": True,
                        "cpa_device_timeout": 30,
                    }):
                        result = app._wait_cpa_device_authorization(
                            "http://cpa:8317",
                            "key",
                            "state-1",
                            email="a@b.com",
                            password="secret",
                        )

        self.assertEqual(result["status"], "authorized")
        self.assertGreaterEqual(progress.call_count, 1)

    def test_device_done_page_waits_for_cpa_without_clicking_close(self):
        responses = iter([
            {"status": "wait"},
            {"status": "authorized"},
        ])
        response = type("Response", (), {
            "raise_for_status": lambda self: None,
            "json": lambda self: next(responses),
        })()
        current_page = type("Page", (), {
            "url": "https://accounts.x.ai/oauth2/device/done",
            "run_js": Mock(return_value="设备已授权"),
        })()
        with patch.object(app, "page", current_page):
            with patch.object(app.requests, "get", return_value=response):
                with patch.object(app, "_progress_device_authorization_page") as progress:
                    with patch.object(app.time, "sleep") as sleep:
                        with patch.object(app, "config", {
                            **app.DEFAULT_CONFIG,
                            "cpa_auto_click_device": True,
                            "cpa_device_timeout": 30,
                        }):
                            result = app._wait_cpa_device_authorization(
                                "http://cpa:8317",
                                "key",
                                "state-1",
                                email="a@b.com",
                                password="secret",
                            )

        self.assertEqual(result["status"], "authorized")
        progress.assert_not_called()
        sleep.assert_called_once_with(0.5)

    def test_device_action_page_changed_when_next_action_appears(self):
        current_page = type("Page", (), {
            "url": "https://accounts.x.ai/oauth2/device",
            "run_js": Mock(),
        })()
        with patch.object(app, "page", current_page):
            with patch.object(app, "_device_page_action_snapshot", return_value={
                "url": "https://accounts.x.ai/oauth2/device",
                "text": "Allow",
            }):
                self.assertTrue(
                    app._device_action_page_changed(
                        "https://accounts.x.ai/oauth2/device",
                        "Continue",
                    )
                )

    def test_device_action_settle_delay_uses_short_polling_interval(self):
        with patch.object(app, "_device_action_page_changed", return_value=False):
            with patch.object(app.time, "time", return_value=100.0):
                with patch.object(app, "config", {
                    **app.DEFAULT_CONFIG,
                    "cpa_device_action_settle_seconds": 10,
                }):
                    self.assertEqual(
                        app._device_action_settle_delay(
                            99.0,
                            "https://accounts.x.ai/oauth2/device",
                            "Continue",
                        ),
                        0.5,
                    )

    def test_email_signup_score_includes_japanese(self):
        source = Path("grok_register_ttk.py").read_text(encoding="utf-8")
        self.assertIn("メールで登録", source)
        self.assertIn("メールアドレスで登録", source)


    def test_progress_prefers_email_login_entry(self):
        current_page = type("Page", (), {
            "run_js": Mock(return_value={"action": "email-entry", "text": "使用邮箱登录"}),
        })()
        with patch.object(app, "page", current_page):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "cpa_auto_click_device": True,
            }):
                result = app._progress_device_authorization_page(email="a@b.com", password="x")
        self.assertEqual(result["action"], "email-entry")
        self.assertIn("邮箱登录", result["text"])

    def test_device_helper_scores_email_login_not_google(self):
        source = Path("grok_register_ttk.py").read_text(encoding="utf-8")
        self.assertIn("使用邮箱登录", source)
        self.assertIn("isEmailLoginText", source)
        self.assertIn("isSocialLoginText", source)
        self.assertIn("progressAuthPage", source)


    def test_device_flow_logs_login_before_authorize(self):
        logs = []
        with patch.object(app, "page", type("Page", (), {"get": Mock(), "url": "https://grok.com/"})()):
            with patch.object(app, "restart_browser"):
                with patch.object(app, "_prepare_grok_web_session") as prepare:
                    with patch.object(app, "_create_cpa_device_authorization", return_value={
                        "url": "https://accounts.x.ai/oauth2/device?user_code=AB",
                        "state": "s1",
                    }):
                        with patch.object(app, "open_step_tab"):
                            with patch.object(app, "_wait_cpa_device_authorization", return_value={"status": "ok"}):
                                with patch.object(app, "config", {
                                    **app.DEFAULT_CONFIG,
                                    "cpa_prepare_grok_web": False,
                                    "cpa_auto_click_device": True,
                                }):
                                    app._run_cpa_device_flow(
                                        "http://cpa:8317",
                                        "key",
                                        email="a@b.com",
                                        password="secret",
                                        log_callback=logs.append,
                                    )
        prepare.assert_called_once()
        self.assertTrue(any("先登录 Grok" in x for x in logs))
        self.assertTrue(any("Device 授权链接" in x for x in logs))


    def test_open_step_tab_creates_new_tab(self):
        created = []
        class FakeBrowser:
            def get_tabs(self):
                return created[:]
            def new_tab(self, url=None):
                tab = type("Tab", (), {
                    "url": url or "about:blank",
                    "get": Mock(),
                    "wait": type("W", (), {"doc_loaded": Mock()})(),
                    "run_js": Mock(return_value="[1-注册页] | x"),
                })()
                created.append(tab)
                return tab
        browser = FakeBrowser()
        logs = []
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", None):
                with patch.object(app, "config", {
                    **app.DEFAULT_CONFIG,
                    "browser_new_tab_per_step": True,
                }):
                    tab = app.open_step_tab("https://example.com", step_label="1-注册页", log_callback=logs.append)
        self.assertEqual(tab.url, "https://example.com")
        self.assertEqual(len(created), 1)
        self.assertTrue(any("新标签打开" in line and "1-注册页" in line for line in logs))

    def test_browser_ip_probe_reads_public_ip_from_current_page(self):
        current_page = type("Page", (), {
            "get": Mock(),
            "wait": type("W", (), {"doc_loaded": Mock()})(),
            "run_js": Mock(return_value='{"ip":"8.8.8.8"}'),
        })()
        with patch.object(app, "page", current_page):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "browser_ip_check_url": "https://api.ipify.org?format=json",
            }):
                ip = app._read_browser_public_ip()

        self.assertEqual(ip, "8.8.8.8")
        current_page.get.assert_called_once_with("https://api.ipify.org?format=json")

    def test_browser_ip_probe_rejects_proxy_error_page(self):
        current_page = type("Page", (), {
            "get": Mock(),
            "wait": type("W", (), {"doc_loaded": Mock()})(),
            "run_js": Mock(return_value="ERR_SOCKS_CONNECTION_FAILED"),
        })()
        with patch.object(app, "page", current_page):
            with self.assertRaises(RuntimeError) as ctx:
                app._read_browser_public_ip()

        self.assertIn("公网 IP", str(ctx.exception))

    def test_default_browser_flow_reuses_current_tab(self):
        self.assertFalse(app.DEFAULT_CONFIG["browser_new_tab_per_step"])

    def test_device_flow_can_skip_sso_when_credentials_and_cpa_are_ready(self):
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "cpa_auto_add": True,
            "cpa_auth_flow": "device",
            "cpa_remote_url": "http://cpa:8317",
            "cpa_management_key": "key",
        }):
            self.assertTrue(app._can_start_device_flow_without_sso("a@b.com", "secret"))

    def test_oauth_flow_still_requires_sso_cookie(self):
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "cpa_auto_add": True,
            "cpa_auth_flow": "oauth",
            "cpa_remote_url": "http://cpa:8317",
            "cpa_management_key": "key",
        }):
            self.assertFalse(app._can_start_device_flow_without_sso("a@b.com", "secret"))


    def test_detect_logged_out_grok_home_helpers(self):
        source = Path("grok_register_ttk.py").read_text(encoding="utf-8")
        self.assertIn("findHeaderLoginButton", source)
        self.assertIn("isLoggedOutGrokHome", source)
        self.assertIn("header-login", source)
        self.assertIn("2b-账户登录", source)

    def test_run_device_page_js_forwards_arguments_into_iife(self):
        source = Path("grok_register_ttk.py").read_text(encoding="utf-8")
        self.assertIn(").apply(null, arguments);", source)
        self.assertIn("findEmailInput", source)
        self.assertIn("cpa_restart_browser_before_login", source)
        self.assertIn("cpa_login_isolation", source)
        self.assertIn("_try_light_login_isolation", source)

    def test_device_flow_restarts_browser_before_login(self):
        """cpa_login_isolation=restart 时仍整浏览器重启。"""
        logs = []
        with patch.object(app, "page", type("Page", (), {"get": Mock(), "url": "https://grok.com/"})()):
            with patch.object(app, "restart_browser") as restart:
                with patch.object(app, "_prepare_grok_web_session") as prepare:
                    with patch.object(app, "_create_cpa_device_authorization", return_value={
                        "url": "https://accounts.x.ai/oauth2/device?user_code=AB",
                        "state": "s1",
                    }):
                        with patch.object(app, "open_step_tab"):
                            with patch.object(app, "_wait_cpa_device_authorization", return_value={"status": "ok"}):
                                with patch.object(app, "config", {
                                    **app.DEFAULT_CONFIG,
                                    "cpa_prepare_grok_web": True,
                                    "cpa_restart_browser_before_login": True,
                                    "cpa_login_isolation": "restart",
                                    "cpa_auto_click_device": True,
                                }):
                                    app._run_cpa_device_flow(
                                        "http://cpa:8317",
                                        "key",
                                        email="a@b.com",
                                        password="secret",
                                        log_callback=logs.append,
                                    )
        restart.assert_called_once()
        prepare.assert_called_once()
        self.assertTrue(any("重启浏览器" in x for x in logs))

    def test_light_isolation_already_clean_skips_all(self):
        """无 residual 时跳过退出页 / clearCookies / 站点存储。"""
        browser = type("Browser", (), {"_run_cdp": Mock(), "get_tabs": Mock(return_value=[])})()
        current_page = type("Page", (), {
            "get": Mock(),
            "run_js": Mock(),
            "url": "https://accounts.x.ai/",
            "cookies": Mock(return_value=[]),
        })()
        logs = []
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", current_page):
                with patch.object(app, "_has_xai_session_residue", return_value=False):
                    with patch.object(app.time, "sleep"):
                        ok = app._try_light_login_isolation(log_callback=logs.append)

        self.assertTrue(ok)
        current_page.get.assert_not_called()
        browser._run_cdp.assert_not_called()
        self.assertTrue(any("检测无 sso 残留" in item for item in logs))

    def test_light_isolation_signout_first_when_clean(self):
        """有 residual 时优先退出页，干净则跳过 clearCookies / 站点存储。"""
        browser = type("Browser", (), {"_run_cdp": Mock(), "get_tabs": Mock(return_value=[])})()
        current_page = type("Page", (), {
            "get": Mock(),
            "run_js": Mock(return_value=True),
            "wait": type("Wait", (), {"doc_loaded": Mock()})(),
            "url": "https://accounts.x.ai/",
            "cookies": Mock(return_value=[]),
        })()
        logs = []
        # 初始有残留；退出页后干净
        residue_states = iter([True, False])
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", current_page):
                with patch.object(
                    app,
                    "_has_xai_session_residue",
                    side_effect=lambda: next(residue_states, False),
                ):
                    with patch.object(app.time, "sleep"):
                        ok = app._try_light_login_isolation(log_callback=logs.append)

        self.assertTrue(ok)
        opened = [call.args[0] for call in current_page.get.call_args_list]
        self.assertEqual(len(opened), 1)
        self.assertIn("sign-out", opened[0])
        browser._run_cdp.assert_not_called()
        self.assertTrue(any("优先退出页" in item for item in logs))
        self.assertTrue(any("轻量隔离探针：退出页后 → 已干净" in item for item in logs))
        self.assertTrue(any("首次 residual 变干净出现在「退出页」" in item for item in logs))
        self.assertTrue(any("跳过 clearCookies / 站点存储" in item for item in logs))

    def test_light_isolation_clearcookies_fallback_after_signout(self):
        """退出页后仍有残留时，clearCookies 兜底成功则跳过站点存储。"""
        browser = type("Browser", (), {"_run_cdp": Mock(), "get_tabs": Mock(return_value=[])})()
        current_page = type("Page", (), {
            "get": Mock(),
            "run_js": Mock(return_value=True),
            "wait": type("Wait", (), {"doc_loaded": Mock()})(),
            "url": "https://accounts.x.ai/",
            "cookies": Mock(return_value=[]),
        })()
        logs = []
        # 初始有；退出页后仍有；clearCookies wait 干净
        residue_states = iter([True, True, False])
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", current_page):
                with patch.object(
                    app,
                    "_has_xai_session_residue",
                    side_effect=lambda: next(residue_states, False),
                ):
                    with patch.object(app.time, "sleep"):
                        ok = app._try_light_login_isolation(log_callback=logs.append)

        self.assertTrue(ok)
        opened = [call.args[0] for call in current_page.get.call_args_list]
        self.assertTrue(any("sign-out" in url for url in opened))
        self.assertFalse(any("grok.com" in url for url in opened))
        browser._run_cdp.assert_called()
        self.assertTrue(any("兜底 clearCookies" in item for item in logs))
        self.assertTrue(any("首次 residual 变干净出现在「clearCookies」" in item for item in logs))
        self.assertTrue(any("跳过站点存储清理" in item for item in logs))

    def test_light_isolation_site_storage_last_resort(self):
        """退出页 + clearCookies 仍有残留时才清站点存储。"""
        browser = type("Browser", (), {"_run_cdp": Mock(), "get_tabs": Mock(return_value=[])})()
        current_page = type("Page", (), {
            "get": Mock(),
            "run_js": Mock(return_value=True),
            "wait": type("Wait", (), {"doc_loaded": Mock()})(),
            "url": "https://accounts.x.ai/",
            "cookies": Mock(return_value=[]),
        })()
        logs = []
        # 初始/退出页/clear wait×2 仍有；站点存储探针后干净；终检干净
        residue_states = iter([True, True, True, True, False, False])
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", current_page):
                with patch.object(
                    app,
                    "_has_xai_session_residue",
                    side_effect=lambda: next(residue_states, False),
                ):
                    with patch.object(app.time, "sleep"):
                        ok = app._try_light_login_isolation(log_callback=logs.append)

        self.assertTrue(ok)
        opened = [call.args[0] for call in current_page.get.call_args_list]
        self.assertTrue(any("sign-out" in url for url in opened))
        self.assertTrue(any("grok.com" in url for url in opened))
        self.assertTrue(any("清理站点存储" in item for item in logs))

    def test_device_flow_auto_isolation_uses_light_clear_when_clean(self):
        logs = []
        with patch.object(app, "page", type("Page", (), {"get": Mock(), "url": "https://grok.com/"})()):
            with patch.object(app, "_try_light_login_isolation", return_value=True) as light:
                with patch.object(app, "_reset_browser_for_next_account") as reset:
                    with patch.object(app, "_prepare_grok_web_session") as prepare:
                        with patch.object(app, "_create_cpa_device_authorization", return_value={
                            "url": "https://accounts.x.ai/oauth2/device?user_code=AB",
                            "state": "s1",
                        }):
                            with patch.object(app, "open_step_tab"):
                                with patch.object(app, "_wait_cpa_device_authorization", return_value={"status": "ok"}):
                                    with patch.object(app, "config", {
                                        **app.DEFAULT_CONFIG,
                                        "cpa_prepare_grok_web": True,
                                        "cpa_restart_browser_before_login": True,
                                        "cpa_login_isolation": "auto",
                                        "cpa_auto_click_device": True,
                                    }):
                                        app._run_cpa_device_flow(
                                            "http://cpa:8317",
                                            "key",
                                            email="a@b.com",
                                            password="secret",
                                            log_callback=logs.append,
                                        )
        light.assert_called_once()
        reset.assert_not_called()
        prepare.assert_called_once()
        self.assertTrue(any("轻量隔离" in x for x in logs))

    def test_device_flow_auto_isolation_falls_back_to_restart(self):
        logs = []
        with patch.object(app, "page", type("Page", (), {"get": Mock(), "url": "https://grok.com/"})()):
            with patch.object(app, "_try_light_login_isolation", return_value=False):
                with patch.object(app, "_reset_browser_for_next_account", return_value="restarted") as reset:
                    with patch.object(app, "_prepare_grok_web_session") as prepare:
                        with patch.object(app, "_create_cpa_device_authorization", return_value={
                            "url": "https://accounts.x.ai/oauth2/device?user_code=AB",
                            "state": "s1",
                        }):
                            with patch.object(app, "open_step_tab"):
                                with patch.object(app, "_wait_cpa_device_authorization", return_value={"status": "ok"}):
                                    with patch.object(app, "config", {
                                        **app.DEFAULT_CONFIG,
                                        "cpa_prepare_grok_web": True,
                                        "cpa_restart_browser_before_login": True,
                                        "cpa_login_isolation": "auto",
                                        "cpa_auto_click_device": True,
                                    }):
                                        app._run_cpa_device_flow(
                                            "http://cpa:8317",
                                            "key",
                                            email="a@b.com",
                                            password="secret",
                                            log_callback=logs.append,
                                        )
        reset.assert_called_once()
        self.assertEqual(reset.call_args.kwargs.get("reason"), "before_login")
        prepare.assert_called_once()
        self.assertTrue(any("回退" in x for x in logs))

    def test_resolve_login_isolation_mode(self):
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "cpa_restart_browser_before_login": False,
        }):
            self.assertEqual(app._resolve_login_isolation_mode(), "off")
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "cpa_restart_browser_before_login": True,
            "cpa_login_isolation": "auto",
        }):
            self.assertEqual(app._resolve_login_isolation_mode(), "auto")
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "cpa_restart_browser_before_login": True,
            "cpa_login_isolation": "restart",
        }):
            self.assertEqual(app._resolve_login_isolation_mode(), "restart")



    def test_probe_cdp_endpoint_reports_connection_error(self):
        with patch.object(app.requests, "get", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError) as ctx:
                app._probe_cdp_endpoint("127.0.0.1:9")
        self.assertIn("无法连接本地 Chrome CDP", str(ctx.exception))


    def test_ensure_local_chrome_uses_agent_in_docker(self):
        logs = []
        with patch.dict(os.environ, {"GROK_DOCKER": "1"}):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "local_chrome_debug_address": "127.0.0.1:9222",
                "local_chrome_agent_url": "http://127.0.0.1:18083",
                "local_chrome_auto_launch": True,
            }):
                with patch.object(app, "_probe_cdp_endpoint", side_effect=[
                    RuntimeError("down"),
                    {"Browser": "Chrome/1"},
                ]):
                    with patch.object(app, "_request_local_chrome_agent_ensure", return_value={
                        "ok": True,
                        "already_running": False,
                    }) as ensure:
                        with patch.object(app.time, "sleep"):
                            addr = app.ensure_local_chrome(log_callback=logs.append)
        self.assertTrue(addr.endswith(":9222"))
        ensure.assert_called_once()
        self.assertTrue(any("自动拉起" in line for line in logs))

    def test_ensure_local_chrome_creates_page_target_when_cdp_has_none(self):
        logs = []
        targets = Mock()
        targets.json.return_value = []
        created = Mock()
        created.json.return_value = {"type": "page", "url": "about:blank"}
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "local_chrome_debug_address": "127.0.0.1:9222",
        }):
            with patch.object(app, "_probe_cdp_endpoint", return_value={"Browser": "Chrome/1"}):
                with patch.object(app.requests, "get", return_value=targets):
                    with patch.object(app.requests, "put", return_value=created) as create_page:
                        addr = app.ensure_local_chrome(log_callback=logs.append)

        self.assertTrue(addr.endswith(":9222"))
        create_page.assert_called_once_with(
            f"http://{addr}/json/new?about:blank",
            proxies={},
            timeout=2,
        )
        self.assertTrue(any("空白标签" in line for line in logs))

    def test_ensure_local_chrome_creates_page_target_without_log_callback(self):
        targets = Mock()
        targets.json.return_value = []
        created = Mock()
        created.json.return_value = {"type": "page", "url": "about:blank"}
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "local_chrome_debug_address": "127.0.0.1:9222",
        }):
            with patch.object(app, "_probe_cdp_endpoint", return_value={"Browser": "Chrome/1"}):
                with patch.object(app.requests, "get", return_value=targets):
                    with patch.object(app.requests, "put", return_value=created) as create_page:
                        app.ensure_local_chrome()

        create_page.assert_called_once()

    def test_local_chrome_agent_base_uses_host_gateway_in_docker(self):
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "local_chrome_agent_url": "http://127.0.0.1:18083",
        }):
            with patch.dict(os.environ, {"GROK_DOCKER": "1"}):
                with patch("grok_register_ttk.socket.gethostbyname", return_value="192.0.2.10"):
                    self.assertEqual(app._local_chrome_agent_base(), "http://192.0.2.10:18083")

    def test_sso_wait_diagnostic_includes_page_and_cookie_state(self):
        page = type("Page", (), {
            "url": "https://accounts.x.ai/sign-up?redirect=grok-com",
            "title": "Create your account",
        })()
        diagnostic = app._describe_sso_wait_state(
            page,
            {"cf_clearance", "session"},
            last_error="cookie read failed",
        )
        self.assertIn("accounts.x.ai/sign-up", diagnostic)
        self.assertIn("Create your account", diagnostic)
        self.assertIn("cf_clearance,session", diagnostic)
        self.assertIn("cookie read failed", diagnostic)

    def test_find_sso_cookie_scans_all_browser_tabs(self):
        active = type("Page", (), {
            "url": "https://grok.com/",
            "cookies": Mock(return_value=[{"name": "cf_clearance", "value": "x"}]),
        })()
        registration = type("Page", (), {
            "url": "https://accounts.x.ai/sign-up",
            "cookies": Mock(return_value=[{"name": "sso", "value": "token-from-registration"}]),
        })()
        browser = type("Browser", (), {"get_tabs": Mock(return_value=[active, registration])})()
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", active):
                result = app._find_sso_cookie_in_browser_tabs()
        self.assertEqual(result, "token-from-registration")

    def test_find_sso_cookie_falls_back_to_sso_rw(self):
        active = type("Page", (), {
            "url": "https://grok.com/",
            "cookies": Mock(return_value=[{"name": "sso-rw", "value": "rw-token"}]),
        })()
        browser = type("Browser", (), {"get_tabs": Mock(return_value=[active])})()
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", active):
                result = app._find_sso_cookie_in_browser_tabs()
        self.assertEqual(result, "rw-token")

    def test_sso_ready_requires_password_submit_this_run(self):
        with patch.object(app, "_find_sso_cookie_in_browser_tabs", return_value="tok"):
            self.assertFalse(app._sso_ready_after_password_submit(True, None))
            self.assertFalse(app._sso_ready_after_password_submit(False, time.time() - 5))
            self.assertFalse(app._sso_ready_after_password_submit(True, time.time() - 0.2, min_age=2.0))
            self.assertTrue(app._sso_ready_after_password_submit(True, time.time() - 3, min_age=2.0))

    def test_can_start_device_auth_from_sso_on_grok_loading(self):
        with patch.object(app, "_find_sso_cookie_in_browser_tabs", return_value="tok"):
            ok = app._can_start_device_auth_from_sso(
                login_fill_seen=True,
                password_submitted_at=time.time() - 4,
                current_state="loading",
                current_url="https://grok.com/",
                min_age=2.0,
            )
        self.assertTrue(ok)

    def test_can_start_device_auth_without_cookie_when_grok_loading(self):
        """local_chrome 读不到 cookie 时，grok.com loading 也允许提前授权。"""
        with patch.object(app, "_find_sso_cookie_in_browser_tabs", return_value=""):
            too_soon = app._can_start_device_auth_from_sso(
                login_fill_seen=True,
                password_submitted_at=time.time() - 3,
                current_state="loading",
                current_url="https://grok.com/",
                min_age=2.0,
            )
            ok = app._can_start_device_auth_from_sso(
                login_fill_seen=True,
                password_submitted_at=time.time() - 7,
                current_state="loading",
                current_url="https://grok.com/",
                min_age=2.0,
            )
            still_login = app._can_start_device_auth_from_sso(
                login_fill_seen=True,
                password_submitted_at=time.time() - 10,
                current_state="login",
                current_url="https://grok.com/",
                min_age=2.0,
            )
        self.assertFalse(too_soon)
        self.assertTrue(ok)
        self.assertFalse(still_login)

    def test_pick_sso_prefers_sso_over_rw(self):
        value = app._pick_sso_from_cookie_items([
            {"name": "sso-rw", "value": "rw"},
            {"name": "sso", "value": "main"},
        ])
        self.assertEqual(value, "main")

    def test_cdp_cookie_fallback_used_when_tab_cookies_empty(self):
        active = type("Page", (), {
            "url": "https://grok.com/",
            "cookies": Mock(return_value=[]),
            "run_cdp": Mock(return_value={
                "cookies": [{"name": "sso", "value": "from-cdp", "domain": ".x.ai"}],
            }),
        })()
        browser = type("Browser", (), {"get_tabs": Mock(return_value=[active])})()
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", active):
                result = app._find_sso_cookie_in_browser_tabs()
        self.assertEqual(result, "from-cdp")

    def test_can_start_device_auth_from_sso_waits_longer_on_login_page(self):
        with patch.object(app, "_find_sso_cookie_in_browser_tabs", return_value="tok"):
            too_soon = app._can_start_device_auth_from_sso(
                login_fill_seen=True,
                password_submitted_at=time.time() - 3,
                current_state="login",
                current_url="https://accounts.x.ai/sign-in?redirect=grok-com",
                min_age=2.0,
            )
            ready = app._can_start_device_auth_from_sso(
                login_fill_seen=True,
                password_submitted_at=time.time() - 6,
                current_state="login",
                current_url="https://accounts.x.ai/sign-in?redirect=grok-com",
                min_age=2.0,
            )
        self.assertFalse(too_soon)
        self.assertTrue(ready)

    def test_prepare_grok_web_session_exits_early_on_sso_while_loading(self):
        current_page = type("Page", (), {
            "get": Mock(),
            "run_js": Mock(return_value=True),
            "url": "https://grok.com/",
        })()
        logs = []
        with patch.object(app, "page", current_page):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "cpa_auto_click_device": True,
                "cpa_grok_web_wait_seconds": 30,
            }):
                with patch.object(app, "open_step_tab"):
                    with patch.object(app, "_run_device_page_js", side_effect=[
                        {"state": "login", "url": "https://accounts.x.ai/sign-in"},
                        {"state": "loading", "url": "https://grok.com/"},
                    ]):
                        with patch.object(app, "_progress_device_authorization_page", return_value={
                            "action": "login-fill",
                            "hasPassword": True,
                            "submitted": True,
                            "emailValue": "a@b.com",
                        }):
                            with patch.object(app, "_find_sso_cookie_in_browser_tabs", return_value="sso-token"):
                                with patch.object(app.time, "sleep"):
                                    with patch.object(app.time, "time", side_effect=[
                                        # deadline base
                                        1000.0,
                                        # loop 1 checks
                                        1000.1, 1000.1,
                                        # password_submitted_at assignment uses time()
                                        1000.2,
                                        # loop 2: deadline, sso age checks
                                        1003.0, 1003.0, 1003.0, 1003.0, 1003.0, 1003.0,
                                    ]):
                                        app._prepare_grok_web_session(
                                            wait_seconds=30,
                                            email="a@b.com",
                                            password="secret",
                                            log_callback=logs.append,
                                        )

        self.assertTrue(any("sso 会话" in item for item in logs))

    def test_ensure_turnstile_passed_calls_get_token_when_gate_not_ready(self):
        logs = []
        app._turnstile_stage_last_attempt.clear()
        fake_page = type("Page", (), {"run_js": Mock(return_value=120)})()
        with patch.object(app, "page", fake_page):
            with patch.object(
                app,
                "_probe_cloudflare_gate",
                return_value={"present": True, "ready": False, "tokenLen": 0, "verifyingText": True},
            ):
                with patch.object(app, "getTurnstileToken", return_value="t" * 100) as get_token:
                    with patch.object(app, "_backfill_turnstile_token", return_value=100) as backfill:
                        result = app._ensure_turnstile_passed(
                            log_callback=logs.append,
                            stage="Grok登录",
                            min_interval=0,
                            force=True,
                        )

        self.assertEqual(result, "passed")
        get_token.assert_called_once()
        backfill.assert_called_once()
        self.assertTrue(any("自动处理 Turnstile" in item for item in logs))
        self.assertTrue(any("通过并回填" in item for item in logs))

    def test_ensure_turnstile_passed_skips_when_rate_limited(self):
        app._turnstile_stage_last_attempt.clear()
        app._turnstile_stage_last_attempt["Grok登录"] = 10_000.0
        with patch.object(app, "page", object()):
            with patch.object(
                app,
                "_probe_cloudflare_gate",
                return_value={"present": True, "ready": False, "tokenLen": 0},
            ):
                with patch.object(app, "getTurnstileToken") as get_token:
                    with patch.object(app.time, "time", return_value=10_001.0):
                        result = app._ensure_turnstile_passed(
                            stage="Grok登录",
                            min_interval=6.0,
                            force=False,
                        )

        self.assertEqual(result, "skipped")
        get_token.assert_not_called()

    def test_prepare_grok_web_session_wait_cloudflare_triggers_turnstile(self):
        logs = []
        current_page = type("Page", (), {
            "get": Mock(),
            "run_js": Mock(return_value=True),
            "url": "https://accounts.x.ai/sign-in",
        })()
        progress_values = [
            {"action": "wait-cloudflare", "cfTokenLen": 0, "hasPassword": True},
            {"action": "login-fill", "hasPassword": True, "submitted": True, "emailValue": "a@b.com"},
        ]
        state_values = [
            {"state": "login", "url": "https://accounts.x.ai/sign-in"},
            {"state": "login", "url": "https://accounts.x.ai/sign-in"},
            {"state": "ready", "url": "https://grok.com/"},
            {"state": "ready", "url": "https://grok.com/"},
        ]
        with patch.object(app, "page", current_page):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "cpa_auto_click_device": True,
                "cpa_grok_web_wait_seconds": 30,
            }):
                with patch.object(app, "open_step_tab"):
                    with patch.object(app, "_run_device_page_js", side_effect=state_values):
                        with patch.object(
                            app,
                            "_progress_device_authorization_page",
                            side_effect=progress_values,
                        ):
                            with patch.object(
                                app,
                                "_ensure_turnstile_passed",
                                return_value="passed",
                            ) as ensure_cf:
                                with patch.object(app.time, "sleep"):
                                    app._prepare_grok_web_session(
                                        wait_seconds=30,
                                        email="a@b.com",
                                        password="secret",
                                        log_callback=logs.append,
                                    )

        ensure_cf.assert_called()
        called_stages = [kwargs.get("stage") for _, kwargs in ensure_cf.call_args_list if kwargs]
        self.assertIn("Grok登录", called_stages)
        self.assertTrue(any("Cloudflare" in item for item in logs))

    def test_device_page_invalid_code_is_detected(self):
        page = type("Page", (), {
            "url": "https://accounts.x.ai/oauth2/device?error=invalid_code",
            "run_js": Mock(return_value="代码无效或已过期"),
        })()
        self.assertTrue(app._is_invalid_device_code_page(page))

    def test_device_done_page_is_detected_before_action_click(self):
        page = type("Page", (), {
            "url": "https://accounts.x.ai/oauth2/device/done",
            "run_js": Mock(return_value="设备已授权"),
        })()
        self.assertTrue(app._is_device_authorization_done_page(page))

    def test_final_account_does_not_restart_browser(self):
        self.assertFalse(app._should_restart_after_account(1, 1))
        self.assertTrue(app._should_restart_after_account(1, 2))
        self.assertFalse(app._should_restart_after_account(1, 2, stopped=True))

    def test_signup_page_looks_wrong_detects_grok_and_cf(self):
        self.assertEqual(
            app._signup_page_looks_wrong("https://grok.com/", "<html></html>"),
            "grok-logged-in",
        )
        self.assertEqual(
            app._signup_page_looks_wrong(
                "https://accounts.x.ai/sign-up?redirect=grok-com",
                "<title>Just a moment...</title>",
            ),
            "cloudflare",
        )
        self.assertEqual(
            app._signup_page_looks_wrong(
                "https://accounts.x.ai/sign-up?redirect=grok-com",
                "<html>signup</html>",
            ),
            "",
        )

    def test_reset_next_account_uses_light_isolation_for_chromium(self):
        logs = []
        with patch.object(app, "browser", object()):
            with patch.object(app, "page", object()):
                with patch.object(app, "config", {
                    **app.DEFAULT_CONFIG,
                    "browser_backend": "chromium",
                    "browser_reset_strategy": "auto",
                    "browser_clear_data": True,
                }):
                    with patch.object(app, "_try_light_login_isolation", return_value=True) as light:
                        with patch.object(app, "clear_browser_session_data") as clear:
                            with patch.object(app, "_has_xai_session_residue", return_value=False):
                                with patch.object(app, "restart_browser") as restart:
                                    result = app._reset_browser_for_next_account(
                                        log_callback=logs.append,
                                        reason="next_account",
                                    )
        self.assertEqual(result, "cleared")
        light.assert_called_once()
        self.assertEqual(light.call_args.kwargs.get("purpose"), "next_account")
        clear.assert_called_once()
        restart.assert_not_called()
        self.assertTrue(any("退出页轻量隔离" in item for item in logs))

    def test_reset_next_account_restarts_when_residue_remains(self):
        with patch.object(app, "browser", object()):
            with patch.object(app, "page", object()):
                with patch.object(app, "config", {
                    **app.DEFAULT_CONFIG,
                    "browser_backend": "chromium",
                    "browser_reset_strategy": "auto",
                    "browser_clear_data": True,
                }):
                    with patch.object(app, "_try_light_login_isolation", return_value=False):
                        with patch.object(app, "clear_browser_session_data"):
                            with patch.object(app, "_has_xai_session_residue", return_value=True):
                                with patch.object(app, "restart_browser") as restart:
                                    result = app._reset_browser_for_next_account(
                                        reason="next_account",
                                    )
        self.assertEqual(result, "restarted")
        restart.assert_called_once()

    def test_auto_browser_reset_clears_session_without_restart(self):
        browser = type("Browser", (), {"_run_cdp": Mock()})()
        current_page = type("Page", (), {
            "url": "https://accounts.x.ai/oauth2/device",
            "run_js": Mock(),
        })()
        with patch.object(app, "browser", browser):
            with patch.object(app, "page", current_page):
                with patch.object(app, "config", {
                    **app.DEFAULT_CONFIG,
                    "browser_reset_strategy": "auto",
                    "browser_backend": "bitbrowser",
                    "browser_clear_data": True,
                }):
                    with patch.object(app, "_try_light_login_isolation", return_value=True) as light:
                        with patch.object(app, "_has_xai_session_residue", return_value=False):
                            with patch.object(app, "restart_browser") as restart:
                                result = app._reset_browser_for_next_account()

        self.assertEqual(result, "cleared")
        restart.assert_not_called()
        light.assert_called_once()
        self.assertIn("Storage.clearCookies", [call.args[0] for call in browser._run_cdp.call_args_list])

    def test_local_chrome_auto_reset_forces_a_fresh_browser_instance(self):
        logs = []
        current_browser = object()
        with patch.object(app, "browser", current_browser):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "browser_backend": "local_chrome",
                "browser_reset_strategy": "auto",
            }):
                with patch.object(app, "stop_browser") as stop:
                    with patch.object(
                        app,
                        "_request_local_chrome_agent_reset",
                        create=True,
                        return_value={"ok": True, "force_restarted": True},
                    ) as reset:
                        with patch.object(app, "start_browser", return_value=("browser", "page")) as start:
                            result = app._reset_browser_for_next_account(log_callback=logs.append)

        self.assertEqual(result, "restarted")
        stop.assert_called_once()
        reset.assert_called_once_with(log_callback=logs.append)
        start.assert_called_once_with(log_callback=logs.append)
        self.assertTrue(any("下一账号" in line and "强制重建" in line for line in logs))

    def test_local_chrome_reset_before_login_uses_login_message(self):
        logs = []
        with patch.object(app, "browser", object()):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "browser_backend": "local_chrome",
                "browser_reset_strategy": "auto",
            }):
                with patch.object(app, "stop_browser"):
                    with patch.object(
                        app,
                        "_request_local_chrome_agent_reset",
                        create=True,
                        return_value={"ok": True, "force_restarted": True},
                    ):
                        with patch.object(app, "start_browser", return_value=("browser", "page")):
                            result = app._reset_browser_for_next_account(
                                log_callback=logs.append,
                                reason="before_login",
                            )

        self.assertEqual(result, "restarted")
        self.assertTrue(any("登录前" in line for line in logs))
        self.assertTrue(any("开始邮箱登录" in line for line in logs))

    def test_local_chrome_agent_force_reset_passes_reset_flag_to_start_script(self):
        existing = {"Browser": "Chrome/138"}
        completed = type("Completed", (), {
            "stdout": "Chrome started",
            "stderr": "",
            "returncode": 0,
        })()
        with patch.object(local_agent, "probe_local", side_effect=[existing, existing]):
            with patch.object(local_agent.subprocess, "run", return_value=completed) as run:
                result = local_agent.ensure_chrome(9222, force_restart=True)

        self.assertTrue(result["ok"])
        self.assertTrue(result["force_restarted"])
        self.assertEqual(
            run.call_args.args[0],
            ["/bin/sh", str(local_agent.START_SCRIPT), "9222", "--reset"],
        )

    def test_local_chrome_start_script_supports_force_reset(self):
        script = Path("start-local-chrome.sh").read_text(encoding="utf-8")

        self.assertIn('FORCE_RESTART="${2:-}"', script)
        self.assertIn('[ "$FORCE_RESTART" = "--reset" ]', script)

    def test_local_chrome_start_script_creates_blank_page_for_cdp_clients(self):
        script = Path("start-local-chrome.sh").read_text(encoding="utf-8")

        self.assertIn("ensure_page_target()", script)
        self.assertIn('/json/new?about:blank', script)
        self.assertIn('method="PUT"', script)

    def test_restart_browser_reset_strategy_restarts_browser(self):
        with patch.object(app, "config", {
            **app.DEFAULT_CONFIG,
            "browser_reset_strategy": "restart",
        }):
            with patch.object(app, "restart_browser", return_value=("browser", "page")) as restart:
                result = app._reset_browser_for_next_account()

        self.assertEqual(result, "restarted")
        restart.assert_called_once()

    def test_turnstile_diagnostic_reports_unrendered_widget(self):
        page = type("Page", (), {
            "url": "https://accounts.x.ai/sign-up?redirect=grok-com",
            "run_js": Mock(return_value={
                "readyState": "complete",
                "hasTurnstileGlobal": False,
                "hasResponseInput": True,
                "responseLength": 0,
                "iframeCount": 0,
                "turnstileScriptCount": 0,
                "widgetCount": 1,
            }),
        })()
        result = app._get_turnstile_diagnostic(page)
        self.assertEqual(result["responseLength"], 0)
        self.assertFalse(result["hasTurnstileGlobal"])
        self.assertEqual(result["turnstileScriptCount"], 0)

    def test_device_authorization_does_not_treat_code_input_as_email(self):
        source = Path("grok_register_ttk.py").read_text(encoding="utf-8")
        self.assertIn("isDeviceAuthorizationPage", source)
        self.assertIn("isDeviceCodeInput", source)
        self.assertIn("设备代码", source)
        self.assertIn("oauth2/device", source)
        self.assertIn("wait-device-code", source)
        self.assertIn("device-continue", source)

    def test_device_authorization_waits_for_transition_and_recovers_invalid_action(self):
        source = Path("grok_register_ttk.py").read_text(encoding="utf-8")
        self.assertIn("__cpaLastAction", source)
        self.assertIn("document.readyState", source)
        self.assertIn("invalid action", source)
        self.assertIn("page.back()", source)
        self.assertIn("重新打开本次设备授权链接", source)

    def test_grok_login_wait_default_is_long_enough_for_slow_ip(self):
        self.assertEqual(app.DEFAULT_CONFIG["cpa_grok_web_wait_seconds"], 180)

    def test_device_flow_keeps_python_action_lock_across_page_navigation(self):
        source = Path("grok_register_ttk.py").read_text(encoding="utf-8")
        self.assertIn("last_device_action_at", source)
        self.assertIn("_device_action_settle_seconds", source)
        self.assertIn("device_action_settle_seconds", source)

    def test_device_allow_defaults_favor_rescue_then_regen(self):
        self.assertEqual(app.DEFAULT_CONFIG["cpa_device_allow_wait_seconds"], 25)
        self.assertEqual(app.DEFAULT_CONFIG["cpa_device_allow_rescue_seconds"], 10)

    def test_device_flow_passes_current_device_url_to_waiter(self):
        source = Path("grok_register_ttk.py").read_text(encoding="utf-8")
        self.assertIn("device_url=url", source)

    def test_device_action_text_is_allow(self):
        self.assertTrue(app._device_action_text_is_allow("Allow Allow"))
        self.assertTrue(app._device_action_text_is_allow("允许"))
        self.assertFalse(app._device_action_text_is_allow("Continue Continue"))
        self.assertFalse(app._device_action_text_is_allow("Accept All Cookies"))

    def test_turnstile_widget_absent_detects_script_only_state(self):
        diagnostic = {
            "hasTurnstileGlobal": True,
            "hasResponseInput": True,
            "responseLength": 0,
            "turnstileIframeCount": 0,
            "widgetCount": 0,
        }
        self.assertTrue(app._turnstile_widget_absent(diagnostic))
        diagnostic["widgetCount"] = 1
        self.assertFalse(app._turnstile_widget_absent(diagnostic))

    def test_note_login_turnstile_outcome_refreshes_then_aborts(self):
        logs = []
        state = {"fail_streak": 0, "refreshed": False}
        with patch.object(app, "config", {**app.DEFAULT_CONFIG, "cpa_login_cf_max_failures": 2}):
            with patch.object(app, "open_step_tab") as open_tab:
                with patch.object(app.time, "sleep"):
                    state = app._note_login_turnstile_outcome(
                        "failed",
                        state,
                        log_callback=logs.append,
                        stage="Grok登录",
                    )
                    self.assertEqual(state["fail_streak"], 1)
                    self.assertTrue(state["refreshed"])
                    open_tab.assert_called_once()
                    with self.assertRaises(RuntimeError) as ctx:
                        app._note_login_turnstile_outcome(
                            "failed",
                            state,
                            log_callback=logs.append,
                            stage="Grok登录",
                        )
        self.assertIn("连续失败", str(ctx.exception))
        self.assertTrue(app._force_browser_restart_next)
        self.assertTrue(any("刷新账户登录页" in item for item in logs))

    def test_prepare_grok_web_session_raises_when_login_unconfirmed(self):
        logs = []
        current_page = type("Page", (), {
            "get": Mock(),
            "run_js": Mock(return_value=True),
            "url": "https://accounts.x.ai/sign-in",
        })()
        clock = {"t": 1000.0}

        def fake_time():
            return clock["t"]

        def fake_sleep(seconds):
            clock["t"] += float(seconds or 0)

        with patch.object(app, "page", current_page):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "cpa_auto_click_device": True,
                "cpa_grok_web_wait_seconds": 1,
            }):
                with patch.object(app, "open_step_tab"):
                    with patch.object(
                        app,
                        "_run_device_page_js",
                        return_value={"state": "login", "url": "https://accounts.x.ai/sign-in"},
                    ):
                        with patch.object(
                            app,
                            "_progress_device_authorization_page",
                            return_value={"action": "none"},
                        ):
                            with patch.object(app.time, "time", side_effect=fake_time):
                                with patch.object(app.time, "sleep", side_effect=fake_sleep):
                                    with self.assertRaises(RuntimeError) as ctx:
                                        app._prepare_grok_web_session(
                                            wait_seconds=1,
                                            email="a@b.com",
                                            password="secret",
                                            log_callback=logs.append,
                                        )
        self.assertIn("登录仍未确认", str(ctx.exception))
        self.assertIn("中止 Device", str(ctx.exception))
        self.assertTrue(app._force_browser_restart_next)

    def test_wait_device_authorization_rescues_with_reload_before_regenerate(self):
        """Continue 后先刷新自救；若自救后出现 Allow 则完成授权。"""
        device_url = "https://accounts.x.ai/oauth2/device?user_code=ABC"
        current_page = type("Page", (), {
            "url": device_url,
            "get": Mock(),
            "wait": type("W", (), {"doc_loaded": Mock()})(),
            "run_js": Mock(return_value=""),
        })()
        now = {"t": 1000.0}
        phase = {"rescued": False, "allow_clicked": False}

        def fake_time():
            return now["t"]

        def fake_sleep(seconds):
            now["t"] += float(seconds or 0)

        def snap():
            if phase["rescued"]:
                return {
                    "readyState": "complete",
                    "url": device_url,
                    "text": "Allow",
                    "signature": f"{device_url}|Allow|9",
                    "score": 9,
                }
            return {
                "readyState": "complete",
                "url": device_url,
                "text": "Continue",
                "signature": f"{device_url}|Continue|1",
                "score": 1,
            }

        def progress(**kwargs):
            s = snap()
            if s["text"] == "Allow":
                phase["allow_clicked"] = True
            return {
                "action": "device-continue",
                "text": s["text"],
                "url": device_url,
            }

        class FakeResp:
            def raise_for_status(self):
                return None

            def json(self):
                if phase["allow_clicked"]:
                    return {"status": "ok"}
                return {"status": "pending"}

        def page_get(url):
            phase["rescued"] = True
            current_page.url = url

        current_page.get.side_effect = page_get
        logs = []
        with patch.object(app, "page", current_page):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "cpa_auto_click_device": True,
                "cpa_device_timeout": 600,
                "cpa_device_allow_wait_seconds": 25,
                "cpa_device_allow_rescue_seconds": 10,
                "cpa_device_action_settle_seconds": 5,
            }):
                with patch.object(app.time, "time", side_effect=fake_time):
                    with patch.object(app.time, "sleep", side_effect=fake_sleep):
                        with patch.object(app, "_is_invalid_device_action_page", return_value=False):
                            with patch.object(app, "_is_invalid_device_code_page", return_value=False):
                                with patch.object(app, "_is_device_authorization_done_page", return_value=False):
                                    with patch.object(app, "_device_page_action_snapshot", side_effect=snap):
                                        with patch.object(app, "_progress_device_authorization_page", side_effect=progress):
                                            with patch.object(app.requests, "get", return_value=FakeResp()):
                                                data = app._wait_cpa_device_authorization(
                                                    "http://127.0.0.1:8317",
                                                    "key",
                                                    "state-1",
                                                    email="a@b.com",
                                                    password="secret",
                                                    log_callback=logs.append,
                                                    device_url=device_url,
                                                )
        self.assertEqual(data.get("status"), "ok")
        current_page.get.assert_called()
        self.assertTrue(any("刷新授权页自救" in item for item in logs))
        self.assertFalse(any("重新生成授权链接" in item for item in logs))

    def test_wait_device_authorization_regenerates_when_allow_stuck(self):
        current_page = type("Page", (), {
            "url": "https://accounts.x.ai/oauth2/device?user_code=ABC",
            "run_js": Mock(return_value=""),
        })()
        now = {"t": 1000.0}

        def fake_time():
            return now["t"]

        class FakeResp:
            def raise_for_status(self):
                return None

            def json(self):
                return {"status": "pending"}

        with patch.object(app, "page", current_page):
            with patch.object(app, "config", {
                **app.DEFAULT_CONFIG,
                "cpa_auto_click_device": True,
                "cpa_device_timeout": 600,
                "cpa_device_allow_wait_seconds": 25,
                "cpa_device_allow_rescue_seconds": 100,
                "cpa_device_action_settle_seconds": 5,
            }):
                with patch.object(app.time, "time", side_effect=fake_time):
                    with patch.object(app.time, "sleep", side_effect=lambda s: now.__setitem__("t", now["t"] + float(s))):
                        with patch.object(app, "_is_invalid_device_action_page", return_value=False):
                            with patch.object(app, "_is_invalid_device_code_page", return_value=False):
                                with patch.object(app, "_is_device_authorization_done_page", return_value=False):
                                    with patch.object(
                                        app,
                                        "_device_page_action_snapshot",
                                        return_value={
                                            "readyState": "complete",
                                            "url": current_page.url,
                                            "text": "Continue",
                                            "signature": f"{current_page.url}|Continue|1",
                                            "score": 1,
                                        },
                                    ):
                                        with patch.object(
                                            app,
                                            "_progress_device_authorization_page",
                                            return_value={
                                                "action": "device-continue",
                                                "text": "Continue",
                                                "url": current_page.url,
                                            },
                                        ):
                                            with patch.object(app.requests, "get", return_value=FakeResp()):
                                                with self.assertRaises(RuntimeError) as ctx:
                                                    app._wait_cpa_device_authorization(
                                                        "http://127.0.0.1:8317",
                                                        "key",
                                                        "state-1",
                                                        email="a@b.com",
                                                        password="secret",
                                                    )
        self.assertIn("长时间未出现允许", str(ctx.exception))

    def test_reset_next_account_honors_force_restart_flag(self):
        logs = []
        app._force_browser_restart_next = True
        with patch.object(app, "browser", object()):
            with patch.object(app, "page", object()):
                with patch.object(app, "config", {
                    **app.DEFAULT_CONFIG,
                    "browser_backend": "chromium",
                    "browser_reset_strategy": "auto",
                }):
                    with patch.object(app, "restart_browser") as restart:
                        with patch.object(app, "_try_light_login_isolation") as light:
                            result = app._reset_browser_for_next_account(
                                log_callback=logs.append,
                                reason="next_account",
                            )
        self.assertEqual(result, "restarted")
        restart.assert_called_once()
        light.assert_not_called()
        self.assertFalse(app._force_browser_restart_next)
        self.assertTrue(any("强制重启" in item for item in logs))


if __name__ == "__main__":
    unittest.main()

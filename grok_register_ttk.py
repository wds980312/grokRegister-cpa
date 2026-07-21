#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grok 注册机 - TTK GUI 版本
整合 DrissionPage_example.py, openai_register.py, batch_open_nsfw.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import datetime
import time
import os
import sys
import signal
import gc
import queue
import secrets
import socket
import struct
import random
import re
import string
import json
import subprocess
import ipaddress
from urllib.parse import urlsplit, urlunsplit

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError
from curl_cffi import requests

# SSO → CLIProxyAPI(CPA) 扁平格式转换（复用 sso_to_auth_json 的授权码流程 + 写入器）
import sso_to_auth_json as _s2cpa


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
MEMORY_CLEANUP_INTERVAL = 5

UI_BG = "#242424"
UI_PANEL_BG = "#2b2b2b"
UI_FG = "#f2f2f2"
UI_MUTED_FG = "#b8b8b8"
UI_ENTRY_BG = "#333333"
UI_BUTTON_BG = "#3a3a3a"
UI_ACTIVE_BG = "#4a6078"

DEFAULT_CONFIG = {
    "duckmail_api_key": "",
    "cloudflare_api_base": "",
    "cloudflare_api_key": "",
    "cloudflare_auth_mode": "none",
    "cloudflare_custom_auth": "",
    "cloudflare_path_domains": "/api/domains",
    "cloudflare_path_accounts": "/api/new_address",
    "cloudflare_path_token": "/api/token",
    "cloudflare_path_messages": "/api/mails",
    "proxy": "http://127.0.0.1:7890",
    "browser_incognito": None,
    "browser_clear_data": None,
    "browser_reset_strategy": "auto",
    "browser_new_tab_per_step": False,
    "browser_ip_check_url": "https://api.ipify.org?format=json",
    "browser_ip_check_timeout": 45,
    "bitbrowser_check_public_ip": True,
    "browser_backend": "chromium",
    "local_chrome_debug_address": "127.0.0.1:9222",
    "local_chrome_agent_url": "http://127.0.0.1:18083",
    "local_chrome_auto_launch": True,
    "bitbrowser_api_url": "http://127.0.0.1:54345",
    "bitbrowser_profile_id": "",
    "enable_nsfw": True,
    "register_count": 1,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    # CLIProxyAPI(CPA) 直出：注册拿到 SSO 后自动走授权码流程换 token 并写成 CPA 扁平格式
    "cpa_auto_add": False,
    "cpa_auth_flow": "oauth",
    "cpa_device_timeout": 600,
    "cpa_device_refresh_retries": 2,
    "cpa_device_action_settle_seconds": 10,
    # 点击 Continue 后若长时间看不到 Allow，提前重生授权码（避免等失效）
    "cpa_device_allow_wait_seconds": 25,
    # Continue 后先轻量自救（刷新授权页）再进入重生
    "cpa_device_allow_rescue_seconds": 10,
    # 登录页 Turnstile 连续失败次数（含刷新后）达到阈值则中止本账号
    "cpa_login_cf_max_failures": 2,
    "cpa_prepare_grok_web": False,
    # 登录前隔离：auto=先轻量清会话，不干净再整浏览器重启；restart=总是重启；clear=只清会话；off=不隔离
    "cpa_login_isolation": "auto",
    "cpa_restart_browser_before_login": True,
    "cpa_grok_web_wait_seconds": 180,
    "sso_cookie_timeout": 300,
    "cpa_auto_click_device": True,
    "cpa_auth_dir": "",
    # 远程 CPA：通过 Management API POST /v0/management/auth-files 上传
    "cpa_remote_url": "",
    "cpa_management_key": "",
}

config = DEFAULT_CONFIG.copy()
_cf_domain_index = 0
# 上一账号登录/CF/Device 异常时，下一账号优先强制重启浏览器
_force_browser_restart_next = False


def _request_browser_restart_next(reason="", log_callback=None):
    """标记下一轮账号隔离时强制完整重启浏览器。"""
    global _force_browser_restart_next
    _force_browser_restart_next = True
    if log_callback and reason:
        log_callback(f"[Browser] 已标记下一账号强制重启浏览器（{reason}）")


def _consume_force_browser_restart_next():
    global _force_browser_restart_next
    flag = bool(_force_browser_restart_next)
    _force_browser_restart_next = False
    return flag


class RegistrationCancelled(Exception):
    pass


class AccountRetryNeeded(Exception):
    pass


def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            config = {**DEFAULT_CONFIG, **loaded}
        except Exception:
            config = DEFAULT_CONFIG.copy()
    return config


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"保存配置失败: {e}")


def ensure_stable_python_runtime():
    if sys.version_info < (3, 14) or os.environ.get("DPE_REEXEC_DONE") == "1":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return

        print(
            f"[*] 检测到 Python {sys.version.split()[0]}，自动切换到更稳定的解释器: {candidate}"
        )
        env = os.environ.copy()
        env["DPE_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]], env)


def warn_runtime_compatibility():
    if sys.version_info >= (3, 14):
        print(
            "[提示] 当前 Python 为 3.14+；若出现 Mail.tm TLS 异常，建议改用 Python 3.12 或 3.13。"
        )


ensure_stable_python_runtime()
warn_runtime_compatibility()

load_config()

EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)


DUCKMAIL_API_BASE = "https://api.duckmail.sbs"


def get_proxies():
    proxy = config.get("proxy", "")
    if proxy:
        return {"http": proxy, "https": proxy}
    return {}


def get_duckmail_api_key():
    return config.get("duckmail_api_key", "")


def get_cloudflare_api_base():
    return str(config.get("cloudflare_api_base", "") or "").rstrip("/")


def get_cloudflare_api_key():
    return config.get("cloudflare_api_key", "")


def get_cloudflare_auth_mode():
    return str(config.get("cloudflare_auth_mode", "none") or "none").lower()


def get_cloudflare_custom_auth():
    """全局访问密码（cloudflare_temp_email 的 PASSWORDS）。

    开启后 Worker 会对除 /open_api、/telegram 外的所有路径校验 x-custom-auth 头，
    与 cloudflare_auth_mode 正交叠加，需要在每个请求上单独注入。
    """
    return str(config.get("cloudflare_custom_auth", "") or "").strip()


def cloudflare_apply_custom_auth(headers):
    """给请求头注入全局访问密码，若未配置则原样返回。"""
    custom_auth = get_cloudflare_custom_auth()
    if custom_auth:
        headers["x-custom-auth"] = custom_auth
    return headers


def get_cloudflare_path(key, default_path):
    raw = str(config.get(key, default_path) or default_path).strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def cloudflare_build_headers(content_type=False):
    headers = {"Content-Type": "application/json"} if content_type else {}
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key:
        if mode == "x-api-key":
            headers["X-API-Key"] = key
        elif mode == "x-admin-auth":
            headers["x-admin-auth"] = key
        elif mode != "none":
            headers["Authorization"] = f"Bearer {key}"
    cloudflare_apply_custom_auth(headers)
    return headers


def cloudflare_apply_auth_params(params=None):
    merged = dict(params or {})
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key and mode == "query-key":
        merged["key"] = key
    return merged


def cloudflare_next_default_domain():
    """按配置轮换选择 Cloudflare 临时邮箱域名。"""
    global _cf_domain_index
    domains = [x.strip() for x in str(config.get("defaultDomains", "") or "").split(",") if x.strip()]
    if not domains:
        return ""
    domain = domains[_cf_domain_index % len(domains)]
    _cf_domain_index += 1
    return domain


def cloudflare_is_admin_create_path(path):
    """判断当前创建邮箱路径是否为 cloudflare_temp_email 管理员创建接口。"""
    return str(path or "").rstrip("/").lower() == "/admin/new_address"


def _pick_list_payload(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return data.get("results")
        if isinstance(data.get("hydra:member"), list):
            return data.get("hydra:member")
        if isinstance(data.get("data"), list):
            return data.get("data")
        if isinstance(data.get("messages"), list):
            return data.get("messages")
        if isinstance(data.get("data"), dict):
            nested = data.get("data")
            if isinstance(nested.get("messages"), list):
                return nested.get("messages")
    return []


def cloudflare_create_temp_address(api_base):
    """适配 cloudflare_temp_email 新建地址接口并兼容 admin 创建模式。"""
    path = get_cloudflare_path("cloudflare_path_accounts", "/api/new_address")
    url = f"{api_base}{path}"
    domain = cloudflare_next_default_domain()
    is_admin_create = cloudflare_is_admin_create_path(path)
    if is_admin_create:
        payload = {"name": generate_username(10), "enablePrefix": True}
        if domain:
            payload["domain"] = domain
        headers = cloudflare_build_headers(content_type=True)
    else:
        payload = {}
        if domain:
            payload["domain"] = domain
        headers = cloudflare_apply_custom_auth({"Content-Type": "application/json"})
    resp = http_post(url, json=payload, headers=headers)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare {path} 返回非JSON: {resp.text[:300]}")
    address = data.get("address")
    jwt = data.get("jwt")
    if not address or not jwt:
        raise Exception(f"Cloudflare {path} 缺少 address/jwt: {data}")
    return address, jwt


def get_user_agent():
    return config.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    )


def _normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def _resolve_cpa_proxy():
    """CPA 换 token 用的代理：优先配置和环境变量，Docker 无配置时直连。"""
    proxy = str(config.get("proxy", "") or "").strip()
    if proxy:
        return proxy
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        val = str(os.environ.get(key, "") or "").strip()
        if val:
            return val
    if os.environ.get("GROK_DOCKER") == "1":
        return ""
    return "http://127.0.0.1:7890"


def _cpa_management_headers(management_key):
    return {
        "Authorization": f"Bearer {management_key}",
        "Accept": "application/json",
    }


def _create_cpa_device_authorization(remote_url, management_key, log_callback=None):
    """向 CPA 申请 Device 授权链接；5xx/网络抖动时自动重试。"""
    last_error = None
    for attempt in range(1, 4):
        try:
            response = requests.get(
                f"{remote_url.rstrip('/')}/v0/management/xai-auth-url",
                params={"is_webui": "true"},
                headers=_cpa_management_headers(management_key),
                proxies={},
                timeout=20,
            )
            if response.status_code >= 500:
                last_error = RuntimeError(
                    f"HTTP Error {response.status_code}: {response.reason or 'Internal Server Error'}"
                )
                if log_callback:
                    log_callback(
                        f"创建 Device 授权链接失败（{attempt}/3）: {last_error}，稍后重试"
                    )
                time.sleep(1.2 * attempt)
                continue
            response.raise_for_status()
            data = response.json()
            if str(data.get("status", "")).lower() in {"error", "failed"}:
                raise RuntimeError(
                    data.get("message") or data.get("msg") or "CPA Device Flow 创建失败"
                )
            if not data.get("url") or not data.get("state"):
                raise RuntimeError(f"CPA Device Flow 响应缺少 url/state: {data}")
            return data
        except RuntimeError:
            raise
        except Exception as exc:
            last_error = exc
            if log_callback:
                log_callback(
                    f"创建 Device 授权链接异常（{attempt}/3）: {exc}，稍后重试"
                )
            time.sleep(1.2 * attempt)
    raise RuntimeError(f"CPA Device Flow 创建失败: {last_error}")


def _is_cpa_auto_click_enabled():
    configured = config.get("cpa_auto_click_device", True)
    if isinstance(configured, str):
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return bool(configured)


_DEVICE_PAGE_HELPER_JS = r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('value'),
        node.getAttribute('data-testid'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function setInputValue(input, value) {
    if (!input) return false;
    const target = String(value == null ? '' : value);
    try { input.removeAttribute('readonly'); } catch (e) {}
    try { input.readOnly = false; } catch (e) {}
    try { input.disabled = false; } catch (e) {}
    input.focus();
    try { input.click(); } catch (e) {}
    const proto = window.HTMLInputElement && window.HTMLInputElement.prototype;
    const descriptor = proto ? Object.getOwnPropertyDescriptor(proto, 'value') : null;
    // 触发 React 等受控组件的 value tracker
    try {
        const tracker = input._valueTracker;
        if (tracker && typeof tracker.setValue === 'function') {
            tracker.setValue('');
        }
    } catch (e) {}
    if (descriptor && descriptor.set) {
        descriptor.set.call(input, target);
    } else {
        input.value = target;
    }
    try {
        input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: target, inputType: 'insertText' }));
    } catch (e) {}
    try {
        input.dispatchEvent(new InputEvent('input', { bubbles: true, data: target, inputType: 'insertText' }));
    } catch (e) {
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    input.dispatchEvent(new Event('change', { bubbles: true }));
    try { input.blur(); } catch (e) {}
    return String(input.value || '') === target;
}
function dismissCookieConsentIfNeeded() {
    const nodes = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]'));
    const prefer = [];
    const secondary = [];
    for (const node of nodes) {
        if (!isVisible(node) || node.disabled || node.getAttribute('aria-disabled') === 'true') continue;
        const t = nodeText(node).replace(/\s+/g, '');
        const lower = t.toLowerCase();
        if (!t) continue;
        if (/^(全部接受|接受全部|同意全部|接受所有|全部同意|接受|同意|允许全部|allowall|acceptall|accept all|i agree|agree)$/i.test(t)
            || lower.includes('accept all') || lower.includes('allow all')
            || t.includes('全部接受') || t.includes('接受全部') || t.includes('同意全部')
            || t.includes('接受所有 Cookie') || t.includes('接受所有cookie')) {
            prefer.push(node);
            continue;
        }
        if ((t.includes('接受') || t.includes('同意') || lower.includes('accept') || lower.includes('agree'))
            && (t.includes('cookie') || lower.includes('cookie') || t.includes('Cookie') || t.includes('偏好') === false)
            && !/管理|设置|設定|偏好|拒绝|拒绝|decline|manage|settings|customize|自定义/.test(t + lower)) {
            secondary.push(node);
        }
    }
    const target = prefer[0] || secondary[0];
    if (!target) return { dismissed: false };
    try { target.click(); } catch (e) { return { dismissed: false, error: String(e) }; }
    return { dismissed: true, text: nodeText(target) };
}
function cloudflareGateStatus() {
    const bodyText = String((document.body && (document.body.innerText || document.body.textContent)) || '').slice(0, 8000);
    const titleText = String(document.title || '');
    const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
    // 注意：仅 script[src*=turnstile] 预加载不算门禁。注册首页常预挂脚本，无挑战 UI。
    const turnstileIframe = document.querySelector(
        'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], iframe[src*="cf-chl"]'
    );
    const turnstileBox = document.querySelector('div.cf-turnstile, #cf-turnstile, .cf-turnstile, [data-sitekey]');
    let widgetVisible = false;
    try {
        const nodes = [turnstileIframe, turnstileBox, cfInput].filter(Boolean);
        for (const node of nodes) {
            const style = window.getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            if (style && style.display !== 'none' && style.visibility !== 'hidden'
                && Number(style.opacity || '1') > 0 && rect.width > 0 && rect.height > 0) {
                widgetVisible = true;
                break;
            }
        }
    } catch (e) {}
    const token = String((cfInput && cfInput.value) || '').trim();
    const tokenOk = token.length >= 80;
    const verifyingText = /正在验证|验证您是真人|just a moment|checking your browser|verify you are human|attention required|请完成人机验证|请确认您是真人/i.test(bodyText + ' ' + titleText);
    // 门禁成立：中间页文案 / response 输入框 / 可见 widget 或 iframe
    // 隐藏 data-sitekey、仅 script 预加载 → 不算 present
    const present = !!(verifyingText || cfInput || widgetVisible || turnstileIframe);
    if (!present) return { present: false, ready: true, tokenLen: token.length, verifyingText: false, reason: 'none' };
    if (tokenOk) return { present: true, ready: true, tokenLen: token.length, verifyingText: false, reason: 'token-ok' };
    if (verifyingText) {
        return { present: true, ready: false, tokenLen: token.length, verifyingText: true, reason: 'interstitial' };
    }
    if (widgetVisible || turnstileIframe) {
        return { present: true, ready: false, tokenLen: token.length, verifyingText: false, reason: 'widget' };
    }
    // 仅有（可能隐藏的）cf-turnstile-response：表单页常见，提交前需等 token
    return { present: true, ready: false, tokenLen: token.length, verifyingText: false, reason: 'cf-input' };
}
function detectLoginError() {
    const bodyText = String((document.body && (document.body.innerText || document.body.textContent)) || '').slice(0, 8000);
    const patterns = [
        /错误的邮箱地址或密码/,
        /邮箱地址或密码不正确/,
        /邮箱或密码错误/,
        /incorrect (email|password)/i,
        /invalid (email|password|credentials)/i,
        /wrong (email|password)/i,
        /密码不正确/,
        /账号或密码错误/,
    ];
    for (const re of patterns) {
        if (re.test(bodyText)) {
            const m = bodyText.match(re);
            return { hasError: true, message: (m && m[0]) || 'login-error' };
        }
    }
    // 常见错误节点
    const alertNodes = Array.from(document.querySelectorAll('[role="alert"], .error, [data-testid*="error"], [class*="error"], [class*="Error"]'));
    for (const node of alertNodes) {
        if (!isVisible(node)) continue;
        const t = nodeText(node);
        if (/密码|password|邮箱|email|凭证|credential|错误|invalid|incorrect|wrong/i.test(t)) {
            return { hasError: true, message: t.slice(0, 120) };
        }
    }
    return { hasError: false, message: '' };
}
function pickInput(selectors) {
    for (const selector of selectors) {
        const nodes = Array.from(document.querySelectorAll(selector));
        for (const node of nodes) {
            if (isVisible(node) && !node.disabled) {
                return node;
            }
        }
    }
    return null;
}
function isDeviceAuthorizationPage() {
    const url = String(location.href || '');
    if (/oauth2\/device(?:\/|\?|$)/i.test(url)) return true;
    const text = String((document.body && (document.body.innerText || document.body.textContent)) || '');
    return /输入设备代码|设备代码|device code|enter code|invalid action/i.test(text)
        || /oauth2\/device\/approve/i.test(url);
}
function isDeviceAuthorizationDonePage() {
    const url = String(location.href || '');
    const text = String((document.body && (document.body.innerText || document.body.textContent)) || '');
    return /oauth2\/device\/done(?:[?#]|$)/i.test(url)
        || /设备已授权|device(?: has been)? authorized/i.test(text);
}
function isDeviceCodeInput(node) {
    if (!node) return false;
    const meta = [
        node.name, node.id, node.placeholder,
        node.getAttribute('aria-label'), node.getAttribute('data-testid'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').toLowerCase();
    return /device|code|设备|代码|验证码|user.?code/.test(meta);
}
function findDeviceCodeInput() {
    const explicit = pickInput([
        'input[name*="code"]',
        'input[id*="code"]',
        'input[placeholder*="设备代码"]',
        'input[placeholder*="device code" i]',
        'input[autocomplete="one-time-code"]',
        'input[data-testid*="code"]',
    ]);
    if (explicit) return explicit;
    return Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="password"])'))
        .find((node) => isVisible(node) && !node.disabled && isDeviceCodeInput(node)) || null;
}
function deviceActionKey() {
    const best = bestActionCandidate('authorize')[0];
    return best ? `${location.href}|${best.text}|${best.score}` : `${location.href}|none`;
}
function bestActionCandidate(mode) {
    return Array.from(document.querySelectorAll('button, a, [role="button"], input[type="submit"], input[type="button"]'))
        .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
        .map((node) => ({ node, text: nodeText(node), score: scoreActionButton(node, mode || 'authorize') }))
        .filter((item) => item.score > 0)
        .sort((a, b) => b.score - a.score);
}
function describeActionCandidate(candidate, token) {
    if (!candidate || !candidate.node) return null;
    const node = candidate.node;
    const rect = node.getBoundingClientRect();
    return {
        text: candidate.text || '',
        score: candidate.score || 0,
        tag: String(node.tagName || '').toLowerCase(),
        id: node.id || '',
        dataTestId: node.getAttribute('data-testid') || '',
        ariaLabel: node.getAttribute('aria-label') || '',
        name: node.getAttribute('name') || '',
        rect: {
            left: Number(rect.left || 0),
            top: Number(rect.top || 0),
            width: Number(rect.width || 0),
            height: Number(rect.height || 0),
        },
        token: token || '',
    };
}
function markBestDeviceAction(token) {
    const candidate = bestActionCandidate('authorize')[0];
    if (!candidate) return null;
    candidate.node.setAttribute('data-cpa-native-action', String(token || ''));
    return describeActionCandidate(candidate, token);
}
function guardedDeviceAction() {
    if (document.readyState !== 'complete') {
        return { action: 'wait-device-loading', url: location.href };
    }
    const key = deviceActionKey();
    const previous = window.__cpaLastAction || null;
    const now = Date.now();
    const settleMs = 10000;
    if (previous) {
        const changed = previous.key !== key || previous.url !== location.href;
        // 点击后即使按钮文字从“继续”变成“允许”，也要给后端和页面状态
        // 留出时间；否则慢 IP 下会在旧动作尚未完成时再次提交。
        if (now - Number(previous.at || 0) < settleMs) {
            return { action: 'wait-device-transition', url: location.href };
        }
        if (!changed) {
            return { action: 'wait-device-transition', url: location.href };
        }
        window.__cpaLastAction = null;
    }
    const candidate = bestActionCandidate('authorize')[0];
    if (!candidate) return { action: 'wait-device-authorize', url: location.href };
    // Device 授权必须由 Python/DevTools 发出真实鼠标事件，不能调用 DOM 点击 API。
    const descriptor = describeActionCandidate(candidate);
    return { action: 'device-action-ready', ...descriptor, url: location.href };
}
function findEmailInput() {
    const preferred = pickInput([
        'input[type="email"]',
        'input[name="email"]',
        'input[name="username"]',
        'input[autocomplete="username"]',
        'input[autocomplete="email"]',
        'input[data-testid*="email"]',
        'input[data-testid*="Email"]',
        'input[placeholder*="email"]',
        'input[placeholder*="Email"]',
        'input[placeholder*="邮箱"]',
        'input[placeholder*="メール"]',
        'input[id*="email"]',
        'input[id*="user"]',
    ]);
    if (preferred) return preferred;
    // Device 授权页只有一个设备代码框时，绝不能把它当成邮箱框。
    if (isDeviceAuthorizationPage()) return null;
    const inputs = Array.from(document.querySelectorAll(
        'input:not([type="hidden"]):not([type="password"]):not([type="submit"]):not([type="button"]):not([type="checkbox"]):not([type="radio"]):not([type="file"])'
    )).filter((node) => isVisible(node) && !node.disabled);
    for (const node of inputs) {
        const meta = [
            node.name, node.id, node.placeholder,
            node.getAttribute('aria-label'), node.getAttribute('autocomplete'),
            node.getAttribute('data-testid'),
        ].filter(Boolean).join(' ').toLowerCase();
        if (/email|user|mail|login|账号|帐户|账户|邮箱|メール/.test(meta)) {
            return node;
        }
    }
    if (inputs.length === 1) return inputs[0];
    return null;
}
function findPasswordInput() {
    return pickInput([
        'input[type="password"]',
        'input[name="password"]',
        'input[autocomplete="current-password"]',
        'input[data-testid*="password"]',
        'input[data-testid*="Password"]',
        'input[placeholder*="密码"]',
        'input[placeholder*="password"]',
    ]);
}
function isSocialLoginText(text) {
    const compact = String(text || '').replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    const social = ['google', 'apple', 'microsoft', 'github', 'facebook', 'twitter', 'discord', 'passkey', '通行密钥', '谷歌', '苹果'];
    if (social.some((word) => lower.includes(word) || compact.includes(word))) return true;
    if (compact.includes('使用X登录') || compact.includes('使用x登录') || lower.includes('使用x登录')) return true;
    if (/(使用|with).{0,8}(google|apple|x|twitter|github|microsoft)/i.test(String(text || ''))) return true;
    return false;
}
function isEmailLoginText(text) {
    const compact = String(text || '').replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱登录') || compact.includes('邮箱登录') || compact.includes('邮件登录')) return true;
    if (compact.includes('メールでログイン') || compact.includes('メールアドレスでログイン')) return true;
    if (lower.includes('continuewithemail') || lower.includes('signinwithemail') || lower.includes('loginwithemail')) return true;
    if (lower.includes('email') && (lower.includes('sign') || lower.includes('log') || lower.includes('continue') || lower.includes('use'))) return true;
    return false;
}
function isAuthorizeText(text) {
    const compact = String(text || '').replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (['authorize', 'allow', 'approve', 'grant', 'confirm'].some((word) => lower.includes(word))) return true;
    if (['許可', '承認', '同意', '授权', '允许', '批准', '確認'].some((word) => compact.includes(word))) return true;
    return false;
}
function isContinueText(text) {
    const compact = String(text || '').replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (['continue', 'next', 'proceed', 'submit'].some((word) => lower.includes(word))) return true;
    if (['続行', '次へ', '继续', '下一步', '确认', '確定'].some((word) => compact.includes(word))) return true;
    return false;
}
function scoreActionButton(node, mode) {
    const text = nodeText(node);
    const compact = text.replace(/\s+/g, '');
    const lower = text.toLowerCase().replace(/\s+/g, ' ').trim();
    const compactLower = compact.toLowerCase();
    if (!compact) return 0;
    const deny = [
        'cancel', 'deny', 'reject', 'back', 'decline', 'cookie', 'preference', 'settings', 'manage',
        '取消', '拒绝', '戻る', 'キャンセル', '拒否', '设置', '設定', '偏好', '管理', '对话框', '首选项', '注册', 'sign up', 'signup'
    ];
    if (deny.some((word) => compactLower.includes(word) || compact.includes(word))) return -100;
    if (isSocialLoginText(text)) return -100;

    if (mode === 'email-entry') {
        if (isEmailLoginText(text)) return 100;
        return 0;
    }
    if (mode === 'authorize') {
        if (isAuthorizeText(text)) return 100;
        if (isContinueText(text)) return 80;
        if (node.type === 'submit') return 40;
        return 0;
    }
    // login form submit / continue
    if (isAuthorizeText(text)) return 100;
    if (isContinueText(text)) return 90;
    if (['sign in', 'signin', 'log in', 'login'].includes(lower) || ['登录', '登入', 'サインイン', 'ログイン'].includes(compact)) return 70;
    if (node.type === 'submit') return 40;
    return 0;
}
function clickBestAction(mode) {
    const candidate = bestActionCandidate(mode || 'login')[0];
    if (!candidate) return { clicked: false };
    candidate.node.click();
    return { clicked: true, text: candidate.text, score: candidate.score };
}
function findEmailLoginEntry() {
    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'))
        .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
        .map((node) => ({ node, text: nodeText(node), score: scoreActionButton(node, 'email-entry') }))
        .filter((item) => item.score > 0)
        .sort((a, b) => b.score - a.score);
    return candidates[0] || null;
}
function findHeaderLoginButton() {
    // Grok 首页右上角「登录」按钮（未登录态）
    const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'))
        .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
    const exact = [];
    for (const node of nodes) {
        const text = nodeText(node);
        const compact = text.replace(/\s+/g, '');
        const lower = text.toLowerCase().replace(/\s+/g, ' ').trim();
        if (isSocialLoginText(text) || isEmailLoginText(text)) continue;
        if (compact.includes('注册') || lower.includes('sign up') || lower.includes('signup')) continue;
        // 精确匹配登录，避免误点「使用 Google 登录」等
        if (compact === '登录' || compact === '登入' || lower === 'log in' || lower === 'login' || lower === 'sign in') {
            exact.push({ node, text, score: 100 });
            continue;
        }
        // 次优：很短且就是登录
        if ((compact === 'Signin' || compact === 'Login') && compact.length <= 8) {
            exact.push({ node, text, score: 90 });
        }
    }
    exact.sort((a, b) => b.score - a.score);
    return exact[0] || null;
}
function isLoggedOutGrokHome() {
    const url = location.href || '';
    if (!/grok\.com/i.test(url) || /accounts\.x\.ai|auth\.grok\.com|auth\.x\.ai/i.test(url)) {
        return false;
    }
    // 右上角同时有「登录」按钮，说明未登录（即使中间有输入框）
    return !!findHeaderLoginButton();
}
function fillLoginIfNeeded(email, password) {
    const cookie = dismissCookieConsentIfNeeded();
    if (cookie && cookie.dismissed) {
        return {
            filled: false,
            action: 'cookie-dismiss',
            cookieText: cookie.text || '',
            hasEmail: !!findEmailInput(),
            hasPassword: !!findPasswordInput(),
        };
    }
    const loginError = detectLoginError();
    if (loginError.hasError) {
        return {
            filled: false,
            action: 'login-error',
            error: loginError.message || 'login-error',
            hasEmail: !!findEmailInput(),
            hasPassword: !!findPasswordInput(),
        };
    }
    const emailInput = findEmailInput();
    const passwordInput = findPasswordInput();
    let filled = false;
    let filledEmail = false;
    let filledPassword = false;
    if (emailInput && email && String(emailInput.value || '') !== String(email)) {
        filledEmail = setInputValue(emailInput, email);
        filled = filled || filledEmail;
    } else if (emailInput && email && String(emailInput.value || '') === String(email)) {
        filledEmail = true;
        filled = true;
    }
    if (passwordInput && password && String(passwordInput.value || '') !== String(password)) {
        filledPassword = setInputValue(passwordInput, password);
        filled = filled || filledPassword;
    } else if (passwordInput && password && String(passwordInput.value || '') === String(password)) {
        filledPassword = true;
        filled = true;
    }
    if (!emailInput && !passwordInput) {
        return { filled: false, hasEmail: false, hasPassword: false };
    }
    // 已填好也允许提交
    if (!filled && ((emailInput && emailInput.value) || (passwordInput && passwordInput.value))) {
        filled = true;
    }
    if (!filled) {
        return { filled: false, hasEmail: !!emailInput, hasPassword: !!passwordInput, filledEmail, filledPassword };
    }
    // 邮箱+密码都在时，先确认密码值真的写进了输入框
    if (passwordInput && password && String(passwordInput.value || '') !== String(password)) {
        return {
            filled: false,
            action: 'password-mismatch',
            hasEmail: !!emailInput,
            hasPassword: true,
            filledEmail: !!filledEmail,
            filledPassword: false,
            emailValue: emailInput ? String(emailInput.value || '') : '',
        };
    }
    // 有密码框时必须等人机验证完成再提交，否则站点常返回“邮箱或密码错误”
    if (passwordInput && password) {
        const cf = cloudflareGateStatus();
        if (cf.present && !cf.ready) {
            return {
                filled: true,
                action: 'wait-cloudflare',
                hasEmail: !!emailInput,
                hasPassword: true,
                filledEmail: !!filledEmail,
                filledPassword: !!filledPassword,
                emailValue: emailInput ? String(emailInput.value || '') : '',
                submitted: false,
                cfTokenLen: cf.tokenLen || 0,
                verifyingText: !!cf.verifyingText,
            };
        }
        // 已提交过密码则等待跳转，避免反复点登录导致页面卡死/无日志
        const lastSubmitAt = Number(window.__cpaLoginSubmittedAt || 0);
        if (lastSubmitAt && (Date.now() - lastSubmitAt) < 25000) {
            return {
                filled: true,
                action: 'wait-login-result',
                hasEmail: !!emailInput,
                hasPassword: true,
                filledEmail: !!filledEmail,
                filledPassword: !!filledPassword,
                emailValue: emailInput ? String(emailInput.value || '') : '',
                submitted: true,
                submitAgeMs: Date.now() - lastSubmitAt,
                url: location.href,
            };
        }
    }
    // 分步登录：只有邮箱框时点继续；有密码框时再提交登录
    const submit = clickBestAction('login');
    if (submit.clicked && passwordInput && password) {
        window.__cpaLoginSubmittedAt = Date.now();
    }
    return {
        filled: true,
        action: submit.clicked ? 'login-submit' : 'login-fill',
        hasEmail: !!emailInput,
        hasPassword: !!passwordInput,
        filledEmail: !!filledEmail,
        filledPassword: !!filledPassword,
        emailValue: emailInput ? String(emailInput.value || '') : '',
        submitted: !!submit.clicked,
        submitText: submit.text || '',
    };
}
function detectAuthPageState() {
    const url = location.href || '';
    const bodyText = (document.body && (document.body.innerText || document.body.textContent) || '').slice(0, 5000);
    const emailEntry = findEmailLoginEntry();
    const emailInput = findEmailInput();
    const passwordInput = findPasswordInput();
    const hasAuthorize = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="submit"]'))
        .some((node) => isVisible(node) && isAuthorizeText(nodeText(node)));
    if (isDeviceAuthorizationDonePage()) {
        return { state: 'done', url };
    }
    if (isDeviceAuthorizationPage()) {
        return {
            state: 'authorize',
            url,
            hasDeviceCode: !!findDeviceCodeInput(),
            deviceCodeFilled: !!(findDeviceCodeInput() && String(findDeviceCodeInput().value || '').trim()),
        };
    }
    // 表单已出现时优先填登录信息，避免反复点“使用邮箱登录”
    if (passwordInput || emailInput) {
        return {
            state: 'login-form',
            url,
            hasEmail: !!emailInput,
            hasPassword: !!passwordInput,
        };
    }
    if (emailEntry) {
        return { state: 'email-entry', url, entryText: emailEntry.text || '' };
    }
    if (hasAuthorize || /oauth2\/(consent|device|authorize)/i.test(url)) {
        return { state: 'authorize', url };
    }
    if (/sign-in|login|登录|登入/i.test(bodyText) || /sign-in|login|accounts\.x\.ai/i.test(url)) {
        return { state: 'login-unknown', url };
    }
    return { state: 'other', url };
}
function detectGrokWebState() {
    const url = location.href || '';
    const bodyText = (document.body && (document.body.innerText || document.body.textContent) || '').slice(0, 5000);
    const emailEntry = findEmailLoginEntry();
    const headerLogin = findHeaderLoginButton();
    const hasPassword = !!findPasswordInput();
    const hasEmail = !!findEmailInput();
    const onAuthHost = /accounts\.x\.ai|auth\.grok\.com|auth\.x\.ai/i.test(url);
    const onSignIn = /sign-in|sign_in|\/login|oauth2\/device|oauth2\/authorize/i.test(url);

    // 未登录 Grok 首页（有「登录」按钮）绝不能判 ready
    if (headerLogin || isLoggedOutGrokHome()) {
        return {
            state: 'login',
            url,
            hasEmail,
            hasPassword,
            hasEmailEntry: !!emailEntry,
            hasHeaderLogin: true,
            entryText: headerLogin ? (headerLogin.text || '登录') : (emailEntry ? (emailEntry.text || '') : '')
        };
    }
    if (emailEntry || hasEmail || hasPassword || onAuthHost || onSignIn || /登录您的账户|使用邮箱登录|sign[-\s]?in|log[-\s]?in/i.test(bodyText)) {
        return {
            state: 'login',
            url,
            hasEmail,
            hasPassword,
            hasEmailEntry: !!emailEntry,
            hasHeaderLogin: false,
            entryText: emailEntry ? (emailEntry.text || '') : ''
        };
    }
    // 已登录：不能再看到登录/注册入口，且有会话相关 UI
    const hasLogoutOrAccount = Array.from(document.querySelectorAll('button, a, [role="button"], img'))
        .some((node) => {
            if (!isVisible(node)) return false;
            const t = nodeText(node).replace(/\s+/g, '');
            return t.includes('退出') || t.includes('账户') || t.includes('Account') || t.includes('Settings') || t.includes('设置');
        });
    const hasComposer = !!document.querySelector('textarea, [contenteditable="true"], [data-testid*="chat"], [data-testid*="composer"]');
    const onGrokApp = /grok\.com(\/|$|\/chat|\/c\/)/i.test(url) && !onAuthHost;
    if (onGrokApp && hasComposer && !headerLogin) {
        // 再保险：页面文案若仍有明显未登录 CTA 则不算 ready
        if (/\b登录\b|\b注册\b|Sign in|Log in|Sign up/i.test(bodyText) && !hasLogoutOrAccount) {
            // 可能是页脚条款里的字，只有在 header 区域出现才算
            // header 已通过 headerLogin 处理；这里允许 ready
        }
        return { state: 'ready', url, hasComposer: true };
    }
    if (onGrokApp) {
        return { state: 'loading', url };
    }
    return { state: 'loading', url };
}
function progressAuthPage(email, password) {
    if (isDeviceAuthorizationDonePage()) {
        return { action: 'device-done', url: location.href };
    }
    const state = detectAuthPageState();
    // CPA 返回的 user_code 会自动填入设备代码框。只检查并点击继续，
    // 不把该输入框传给邮箱登录填充逻辑，也不改写它的值。
    if (state.state === 'authorize' && isDeviceAuthorizationPage()) {
        const deviceCode = findDeviceCodeInput();
        if (deviceCode && !String(deviceCode.value || '').trim()) {
            return { action: 'wait-device-code', url: location.href };
        }
        return guardedDeviceAction();
    }
    // Grok 首页：先点右上角「登录」
    const headerLogin = findHeaderLoginButton();
    if (headerLogin) {
        headerLogin.node.click();
        return { action: 'header-login', text: headerLogin.text || '登录', url: location.href };
    }
    if (state.state === 'email-entry') {
        const entry = findEmailLoginEntry();
        if (entry) {
            entry.node.click();
            return { action: 'email-entry', text: entry.text || '使用邮箱登录', url: location.href };
        }
    }
    if (state.state === 'login-form' || state.state === 'login-unknown') {
        const login = fillLoginIfNeeded(email, password);
        if (login.action === 'cookie-dismiss') {
            return { action: 'cookie-dismiss', text: login.cookieText || 'Cookie', url: location.href };
        }
        if (login.action === 'login-error') {
            return { action: 'login-error', text: login.error || 'login-error', url: location.href };
        }
        if (login.action === 'wait-cloudflare') {
            return {
                action: 'wait-cloudflare',
                hasEmail: !!login.hasEmail,
                hasPassword: !!login.hasPassword,
                cfTokenLen: login.cfTokenLen || 0,
                verifyingText: !!login.verifyingText,
                url: location.href
            };
        }
        if (login.action === 'wait-login-result') {
            return {
                action: 'wait-login-result',
                hasEmail: !!login.hasEmail,
                hasPassword: !!login.hasPassword,
                submitted: true,
                submitAgeMs: login.submitAgeMs || 0,
                emailValue: login.emailValue || '',
                url: location.href
            };
        }
        if (login.action === 'password-mismatch') {
            return { action: 'password-mismatch', url: location.href };
        }
        if (login.filled) {
            return {
                action: 'login-fill',
                filled: !!login.filled,
                hasEmail: !!login.hasEmail,
                hasPassword: !!login.hasPassword,
                submitted: !!login.submitted,
                submitText: login.submitText || '',
                emailValue: login.emailValue || '',
                url: location.href
            };
        }
        const entry = findEmailLoginEntry();
        if (entry) {
            entry.node.click();
            return { action: 'email-entry', text: entry.text || '使用邮箱登录', url: location.href };
        }
    }
    if (state.state === 'authorize') {
        return guardedDeviceAction();
    }
    const login = fillLoginIfNeeded(email, password);
    if (login.action === 'cookie-dismiss') {
        return { action: 'cookie-dismiss', text: login.cookieText || 'Cookie', url: location.href };
    }
    if (login.action === 'login-error') {
        return { action: 'login-error', text: login.error || 'login-error', url: location.href };
    }
    if (login.action === 'wait-cloudflare') {
        return {
            action: 'wait-cloudflare',
            hasEmail: !!login.hasEmail,
            hasPassword: !!login.hasPassword,
            cfTokenLen: login.cfTokenLen || 0,
            verifyingText: !!login.verifyingText,
            url: location.href
        };
    }
    if (login.action === 'wait-login-result') {
        return {
            action: 'wait-login-result',
            hasEmail: !!login.hasEmail,
            hasPassword: !!login.hasPassword,
            submitted: true,
            submitAgeMs: login.submitAgeMs || 0,
            emailValue: login.emailValue || '',
            url: location.href
        };
    }
    if (login.action === 'password-mismatch') {
        return { action: 'password-mismatch', url: location.href };
    }
    if (login.filled) {
        return {
            action: 'login-fill',
            filled: !!login.filled,
            hasEmail: !!login.hasEmail,
            hasPassword: !!login.hasPassword,
            submitted: !!login.submitted,
            submitText: login.submitText || '',
            emailValue: login.emailValue || '',
            url: location.href
        };
    }
    // 仍在 grok 未登录首页时继续尝试点登录
    const headerAgain = findHeaderLoginButton();
    if (headerAgain) {
        headerAgain.node.click();
        return { action: 'header-login', text: headerAgain.text || '登录', url: location.href };
    }
    return { action: 'wait', state: state.state || 'other', url: location.href };
}
"""

def _run_device_page_js(snippet, *args):
    if page is None:
        raise RuntimeError("浏览器页面尚未连接")
    # DrissionPage 会把脚本包成 function(){...}，参数通过 arguments 注入。
    # 这里必须 .apply(null, arguments)，否则内层 IIFE 拿不到 email/password。
    script = (
        _DEVICE_PAGE_HELPER_JS
        + "\n"
        + "const __deviceResult = (function(){\n"
        + snippet
        + "\n}).apply(null, arguments);\n"
        + "return (typeof __deviceResult === 'string') ? __deviceResult : JSON.stringify(__deviceResult);"
    )
    raw = page.run_js(script, *args)
    if raw is None or raw is False:
        return raw
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        text_value = raw.strip()
        if not text_value:
            return raw
        try:
            return json.loads(text_value)
        except Exception:
            return raw
    return raw


def _click_device_action_native(descriptor):
    """通过浏览器输入层点击 Device 授权动作，避免 DOM click 丢失授权请求。"""
    if page is None:
        return {"clicked": False, "reason": "浏览器页面尚未连接"}

    token = secrets.token_hex(12)
    marked = {}
    try:
        result = _run_device_page_js(
            "return markBestDeviceAction(arguments[0]);",
            token,
        )
        if isinstance(result, dict):
            marked = result
    except Exception:
        marked = {}

    rect = (marked or {}).get("rect") or (descriptor or {}).get("rect") or {}
    try:
        element = page.ele(f"@data-cpa-native-action={token}", timeout=3)
        clicker = getattr(element, "click", None) if element is not None else None
        native_at = getattr(clicker, "at", None) if clicker is not None else None
        if callable(native_at):
            native_at()
            return {
                "clicked": True,
                "method": "drissionpage-input",
                "text": (marked or {}).get("text") or (descriptor or {}).get("text") or "",
            }
    except Exception:
        pass
    finally:
        try:
            page.run_js(
                """
const node = document.querySelector('[data-cpa-native-action="' + arguments[0] + '"]');
if (node) node.removeAttribute('data-cpa-native-action');
return true;
""",
                token,
            )
        except Exception:
            pass

    try:
        left = float(rect.get("left", 0) or 0)
        top = float(rect.get("top", 0) or 0)
        width = float(rect.get("width", 0) or 0)
        height = float(rect.get("height", 0) or 0)
        if width <= 0 or height <= 0:
            return {"clicked": False, "reason": "授权按钮没有有效坐标"}
        x = left + width / 2
        y = top + height / 2
        page.run_cdp(
            "Input.dispatchMouseEvent",
            type="mouseMoved",
            x=x,
            y=y,
            button="none",
        )
        page.run_cdp(
            "Input.dispatchMouseEvent",
            type="mousePressed",
            x=x,
            y=y,
            button="left",
            clickCount=1,
        )
        page.run_cdp(
            "Input.dispatchMouseEvent",
            type="mouseReleased",
            x=x,
            y=y,
            button="left",
            clickCount=1,
        )
        return {
            "clicked": True,
            "method": "cdp-input",
            "text": (marked or {}).get("text") or (descriptor or {}).get("text") or "",
        }
    except Exception as exc:
        return {"clicked": False, "reason": str(exc)}


def _progress_device_authorization_page(email="", password="", log_callback=None):
    """Device 授权页自动流程：使用邮箱登录 → 填密码 → 再点授权。"""
    if page is None or not _is_cpa_auto_click_enabled():
        return None
    try:
        result = _run_device_page_js(
            """
const email = arguments[0] || '';
const password = arguments[1] || '';
return progressAuthPage(email, password);
""",
            email or "",
            password or "",
        )
    except Exception as exc:
        if log_callback:
            msg = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            if len(msg) > 160:
                msg = msg[:160] + "..."
            log_callback(f"[CPA] 自动处理授权页失败: {msg}")
        return None
    if isinstance(result, dict) and result.get("action") == "device-action-ready":
        clicked = _click_device_action_native(result)
        if clicked.get("clicked"):
            return {**result, **clicked, "action": "device-continue"}
        if log_callback:
            log_callback(f"[CPA] 授权按钮真实点击未完成: {clicked.get('reason') or '未知原因'}")
        return {**result, **clicked, "action": "wait-device-native-click"}
    return result


def _cookie_items_from_any(payload):
    """把 cookies API / CDP 返回值统一成 [{name,value,domain}, ...]。"""
    if payload is None:
        return []
    if isinstance(payload, dict):
        if isinstance(payload.get("cookies"), list):
            payload = payload.get("cookies")
        elif isinstance(payload.get("result"), dict) and isinstance(
            payload.get("result", {}).get("cookies"), list
        ):
            payload = payload["result"]["cookies"]
        else:
            return []
    if not isinstance(payload, (list, tuple)):
        return []
    items = []
    for item in payload:
        if isinstance(item, dict):
            name = str(item.get("name", "") or "").strip()
            value = str(item.get("value", "") or "").strip()
            domain = str(item.get("domain", "") or "").strip()
        else:
            name = str(getattr(item, "name", "") or "").strip()
            value = str(getattr(item, "value", "") or "").strip()
            domain = str(getattr(item, "domain", "") or "").strip()
        if name:
            items.append({"name": name, "value": value, "domain": domain})
    return items


def _cdp_all_cookies():
    """通过 CDP 读取全量 cookie（含 HttpOnly），local_chrome 上比 tab.cookies 更可靠。"""
    runners = []
    if page is not None:
        runners.append(page)
    if browser is not None:
        runners.append(browser)
    commands = ("Network.getAllCookies", "Storage.getCookies")
    for runner in runners:
        for cmd in commands:
            try:
                if hasattr(runner, "run_cdp"):
                    result = runner.run_cdp(cmd)
                elif hasattr(runner, "_run_cdp"):
                    result = runner._run_cdp(cmd)
                else:
                    continue
            except Exception:
                continue
            items = _cookie_items_from_any(result)
            if items:
                return items
    return []


def _is_sso_cookie_name(name):
    """仅识别真正的 xAI SSO cookie，避免误匹配含 sso 字样的其它 cookie。"""
    lname = str(name or "").strip().lower()
    if not lname:
        return False
    if lname in {"sso", "sso-rw", "__secure-sso", "__host-sso", "__secure-sso-rw", "__host-sso-rw"}:
        return True
    # 允许前缀变体，但拒绝宽泛 "sso" 子串匹配
    return lname.endswith(".sso") or lname.endswith(".sso-rw")


def _pick_sso_from_cookie_items(items):
    """从 cookie 列表挑选 sso / sso-rw 值。"""
    fallback_rw = ""
    for item in items or []:
        name = str((item or {}).get("name", "") or "").strip()
        value = str((item or {}).get("value", "") or "").strip()
        if not value or not _is_sso_cookie_name(name):
            continue
        lname = name.lower()
        if lname in {"sso", "__secure-sso", "__host-sso"} or lname.endswith(".sso"):
            return value
        if not fallback_rw:
            fallback_rw = value
    return fallback_rw


def _find_sso_cookie_in_browser_tabs():
    """在所有标签页 + CDP 查找可用的 SSO 会话 cookie。

    优先返回 sso；若仅有 sso-rw 也视为可用会话信号（Device 授权页认的是登录态）。
    本地无痕 Chrome 上 tab.cookies 常读不全 HttpOnly，必须走 CDP 兜底。
    """
    collected = []
    try:
        tabs = browser.get_tabs() if browser is not None else []
        tabs = list(tabs or [])
    except Exception:
        tabs = []
    candidates = list(tabs)
    if page is not None and page not in candidates:
        candidates.append(page)
    for tab in candidates:
        try:
            cookies = tab.cookies(all_domains=True, all_info=True) or []
        except Exception:
            try:
                cookies = tab.cookies() or []
            except Exception:
                cookies = []
        collected.extend(_cookie_items_from_any(cookies))

    picked = _pick_sso_from_cookie_items(collected)
    if picked:
        return picked

    cdp_items = _cdp_all_cookies()
    picked = _pick_sso_from_cookie_items(cdp_items)
    if picked:
        return picked
    return ""


def _sso_ready_after_password_submit(login_fill_seen, password_submitted_at, min_age=2.0):
    """本轮邮箱密码提交后，若已有 sso 会话则可跳过等 Grok UI 完全 ready。

    必须同时满足：
    - 本轮确实点过/填过登录（login_fill_seen）
    - 本轮已提交密码（password_submitted_at）
    - 提交后至少 min_age 秒（避免读到刚清会话前的瞬时脏数据）
    - 浏览器里能读到 sso / sso-rw
    """
    if not login_fill_seen or password_submitted_at is None:
        return False
    try:
        age = time.time() - float(password_submitted_at)
    except (TypeError, ValueError):
        return False
    if age < float(min_age or 0):
        return False
    return bool(_find_sso_cookie_in_browser_tabs())


def _grok_session_page_ready_for_device(
    login_fill_seen,
    password_submitted_at,
    current_state="",
    current_url="",
    min_age=6.0,
):
    """cookie 读不到时的页面启发式：密码提交后已到 grok.com 且非登录页。

    detectGrokWebState 在 loading 时已排除 header「登录」按钮；
    因此 loading@grok.com 在本轮提交后持续一段时间，可安全进 Device 授权。
    """
    if not login_fill_seen or password_submitted_at is None:
        return False
    try:
        age = time.time() - float(password_submitted_at)
    except (TypeError, ValueError):
        return False
    if age < float(min_age or 0):
        return False
    url = str(current_url or "")
    state = str(current_state or "")
    on_grok = bool(re.search(r"https?://(www\.)?grok\.com(/|$|\?)", url, re.I))
    if not on_grok:
        return False
    if state == "login":
        return False
    # ready / loading / other 都可：只要不在登录页
    return state in {"ready", "loading", "other", ""}


def _can_start_device_auth_from_sso(
    login_fill_seen,
    password_submitted_at,
    current_state="",
    current_url="",
    min_age=2.0,
):
    """是否可以提前进入 Device 授权（无需 Grok UI ready）。

    优先：本轮密码提交后读到 sso/sso-rw。
    兜底：本轮提交后已在 grok.com 且非登录 UI（cookie 暂时读不到时）。
    """
    if _sso_ready_after_password_submit(
        login_fill_seen, password_submitted_at, min_age=min_age
    ):
        url = str(current_url or "")
        state = str(current_state or "")
        on_auth_login = state == "login" and bool(
            re.search(r"accounts\.x\.ai|auth\.grok\.com|sign-in|login", url, re.I)
        )
        if on_auth_login:
            try:
                age = time.time() - float(password_submitted_at)
            except (TypeError, ValueError):
                return False
            return age >= max(float(min_age or 0), 5.0)
        return True

    return _grok_session_page_ready_for_device(
        login_fill_seen=login_fill_seen,
        password_submitted_at=password_submitted_at,
        current_state=current_state,
        current_url=current_url,
        min_age=max(float(min_age or 0), 6.0),
    )


def _device_auth_early_exit_reason(
    login_fill_seen,
    password_submitted_at,
    current_state="",
    current_url="",
):
    """返回提前进 Device 授权的原因文案；不可提前则返回空串。"""
    if not _can_start_device_auth_from_sso(
        login_fill_seen=login_fill_seen,
        password_submitted_at=password_submitted_at,
        current_state=current_state,
        current_url=current_url,
    ):
        return ""
    if _sso_ready_after_password_submit(
        login_fill_seen, password_submitted_at, min_age=2.0
    ):
        return "已检测到 sso 会话，跳过等待 Grok UI，开始 Device 授权"
    return (
        "Grok 已进入会话页（loading/非登录）且本轮密码已提交，"
        "跳过等待聊天 UI，开始 Device 授权"
    )


def _is_invalid_device_code_page(active_page):
    """识别 Device URL 已被消费/失效的页面。"""
    if active_page is None:
        return False
    try:
        url = str(getattr(active_page, "url", "") or "")
    except Exception:
        url = ""
    if re.search(r"oauth2/device[^#]*error=(?:invalid_code|expired|invalid)", url, re.I):
        return True
    try:
        body = active_page.run_js(
            "return ((document.body && (document.body.innerText || document.body.textContent)) || '').slice(0, 4000);"
        )
        text = str(body or "")
    except Exception:
        text = ""
    return bool(re.search(r"invalid[_ -]?code|code is invalid|代码无效|代码已过期|代码过期|无效或已过期", text, re.I))


def _is_invalid_device_action_page(active_page):
    """识别继续/允许后被 auth.x.ai 拒绝的临时动作页。"""
    if active_page is None:
        return False
    try:
        url = str(getattr(active_page, "url", "") or "")
    except Exception:
        url = ""
    if re.search(r"oauth2/device/approve", url, re.I):
        try:
            body = active_page.run_js(
                "return ((document.body && (document.body.innerText || document.body.textContent)) || '').slice(0, 1000);"
            )
            return bool(re.search(r"invalid action|无效操作", str(body or ""), re.I))
        except Exception:
            return True
    return False


def _is_device_authorization_done_page(active_page):
    """识别授权完成页，完成后不再尝试点击页面上的“关闭”等按钮。"""
    if active_page is None:
        return False
    try:
        url = str(getattr(active_page, "url", "") or "")
    except Exception:
        url = ""
    if re.search(r"oauth2/device/done(?:[?#]|$)", url, re.I):
        return True
    try:
        body = active_page.run_js(
            "return ((document.body && (document.body.innerText || document.body.textContent)) || '').slice(0, 1200);"
        )
    except Exception:
        return False
    return bool(re.search(r"设备已授权|device(?: has been)? authorized", str(body or ""), re.I))


def _is_retryable_device_status(status, data):
    message = " ".join(
        str(data.get(key, "") or "") for key in ("status", "message", "msg", "error")
    ).lower()
    return status in {"expired", "invalid_code"} or any(
        token in message for token in ("invalid_code", "invalid code", "expired", "过期", "无效")
    )


def _device_action_settle_seconds():
    try:
        return max(5.0, float(config.get("cpa_device_action_settle_seconds", 10) or 10))
    except (TypeError, ValueError):
        return 10.0


def _device_action_text_is_allow(text):
    """判断按钮文案是否为 Allow/允许（device-continue 会复用同一 action）。"""
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    compact = re.sub(r"\s+", "", raw).casefold()
    if not compact:
        return False
    # Cookie 同意按钮：accept all / allow all（避免误伤 Allow Allow）
    cookie_like = re.sub(r"[^a-z]", "", raw.casefold())
    if cookie_like in {"allowall", "acceptall", "allowallcookies", "acceptallcookies"}:
        return False
    if re.fullmatch(r"(allow|允许|許可|批准|授权)+", compact):
        return True
    tokens = [t for t in re.split(r"\s+", raw.casefold()) if t]
    if tokens and all(t in {"allow", "允许", "許可", "批准", "授权"} for t in tokens):
        return True
    return "允许" in raw or "許可" in raw


def _device_action_page_changed(previous_url, previous_text):
    """判断原生点击后是否已进入下一步，避免固定等待整段超时。"""
    if page is None:
        return False
    try:
        current_url = str(getattr(page, "url", "") or "")
    except Exception:
        current_url = ""
    if current_url and current_url != str(previous_url or ""):
        return True
    if (
        _is_device_authorization_done_page(page)
        or _is_invalid_device_action_page(page)
        or _is_invalid_device_code_page(page)
    ):
        return True
    snapshot = _device_page_action_snapshot()
    snapshot_url = str(snapshot.get("url") or "")
    snapshot_text = re.sub(r"\s+", " ", str(snapshot.get("text") or "")).strip().casefold()
    previous_text = re.sub(r"\s+", " ", str(previous_text or "")).strip().casefold()
    return bool(
        (snapshot_url and snapshot_url != str(previous_url or ""))
        or (snapshot_text and snapshot_text != previous_text)
    )


def _device_action_settle_delay(action_started_at, previous_url, previous_text):
    """返回下一次短轮询等待；页面已变化或达到保护上限时返回 None。"""
    if not action_started_at or _device_action_page_changed(previous_url, previous_text):
        return None
    remaining = _device_action_settle_seconds() - (time.time() - action_started_at)
    return min(0.5, remaining) if remaining > 0 else None


def _device_page_action_snapshot():
    """读取授权页当前动作，不执行点击；用于等待 React 页面完成 hydration。"""
    try:
        result = _run_device_page_js(
            """
const buttons = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="submit"], input[type="button"]'))
  .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
  .map((node) => ({ text: nodeText(node), score: scoreActionButton(node, 'authorize') }))
  .filter((item) => item.score > 0)
  .sort((a, b) => b.score - a.score);
const best = buttons[0] || null;
if (isDeviceAuthorizationDonePage()) {
  return {
    readyState: document.readyState,
    url: location.href,
    done: true,
    text: '',
    score: 0,
    signature: `${location.href}|done`,
  };
}
return {
  readyState: document.readyState,
  url: location.href,
  text: best ? best.text : '',
  score: best ? best.score : 0,
  signature: `${location.href}|${best ? best.text : ''}|${best ? best.score : 0}`,
};
"""
        ) or {}
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _prepare_grok_web_session(wait_seconds=None, email="", password="", log_callback=None):
    """注册完成后先登录 Grok Web，确认会话就绪再进入 Device 授权。"""
    if page is None:
        raise RuntimeError("浏览器页面尚未连接，无法初始化 Grok Web")
    if wait_seconds is None:
        wait_seconds = config.get("cpa_grok_web_wait_seconds", 180)
    try:
        wait_seconds = max(0, int(wait_seconds or 0))
    except (TypeError, ValueError):
        wait_seconds = 180

    # 有邮箱密码时直接进账户登录页，避免 grok.com 误判“残留会话”绕路。
    prefer_email_login = bool(email and password)
    if prefer_email_login:
        open_step_tab(
            "https://accounts.x.ai/sign-in?redirect=grok-com",
            step_label="2-Grok邮箱登录",
            log_callback=log_callback,
        )
    else:
        open_step_tab("https://grok.com/", step_label="2-Grok登录", log_callback=log_callback)
    if log_callback:
        if _is_cpa_auto_click_enabled() and email:
            log_callback(
                f"[CPA] 注册完成，开始登录 Grok（邮箱登录，最多 {wait_seconds} 秒）: {email}"
            )
        else:
            log_callback(f"[CPA] 已打开 Grok Web，请完成登录/初始化（最多 {wait_seconds} 秒）")
    if wait_seconds <= 0:
        return

    deadline = time.time() + wait_seconds
    last_action = ""
    saw_login_ui = prefer_email_login
    login_fill_seen = False
    ready_hits = 0
    password_submitted_at = None
    last_heartbeat_at = 0.0
    grok_probe_done = False
    forced_restart_for_residue = False
    # 登录页 CF 连续失败：失败→刷新一次→再失败则中止，避免空转 180s
    cf_state = {"fail_streak": 0, "refreshed": False}

    while time.time() < deadline:
        try:
            state = _run_device_page_js("return detectGrokWebState();") or {}
        except Exception:
            state = {}
        current = str((state or {}).get("state") or "")
        current_url = str((state or {}).get("url") or getattr(page, "url", "") or "")

        # 密码提交后人机/会话可能已就绪，但 grok.com 仍在 loading。
        # 读到 sso，或已稳定落在 grok.com 非登录页 → 直接 Device 授权。
        early_reason = _device_auth_early_exit_reason(
            login_fill_seen=login_fill_seen,
            password_submitted_at=password_submitted_at,
            current_state=current,
            current_url=current_url,
        )
        if early_reason:
            if log_callback:
                log_callback(f"[CPA] {early_reason}")
            return

        if current == "login":
            saw_login_ui = True
            ready_hits = 0
            if _is_cpa_auto_click_enabled() and (email or password):
                progress = _progress_device_authorization_page(
                    email=email,
                    password=password,
                    log_callback=log_callback,
                )
                action = str((progress or {}).get("action") or "")
                detail = str((progress or {}).get("text") or (progress or {}).get("submitText") or "")
                if action == "login-error":
                    msg = detail or "邮箱或密码错误"
                    if log_callback:
                        log_callback(
                            f"[CPA] Grok 登录失败：{msg}。"
                            "可能原因：1) 注册资料提交时人机验证未真正通过；"
                            "2) 登录页人机/Cookie 未完成就提交；"
                            "3) 密码未写入输入框。"
                        )
                    raise RuntimeError(f"Grok 邮箱登录失败: {msg}")
                if action == "wait-cloudflare":
                    marker = f"wait-cloudflare:{current_url}"
                    if marker != last_action and log_callback:
                        last_action = marker
                        token_len = (progress or {}).get("cfTokenLen") or 0
                        log_callback(
                            f"[CPA] Grok 登录：等待 Cloudflare 通过后再提交密码 "
                            f"(token长度={token_len})"
                        )
                    # 与注册路径一致：主动点 Turnstile，而不是干等 token
                    cf_result = _ensure_turnstile_passed(
                        log_callback=log_callback,
                        stage="Grok登录",
                        min_interval=6.0,
                    )
                    cf_state = _note_login_turnstile_outcome(
                        cf_result,
                        cf_state,
                        log_callback=log_callback,
                        stage="Grok登录",
                    )
                    time.sleep(1.0)
                    continue
                if action == "wait-login-result":
                    if password_submitted_at is None:
                        password_submitted_at = time.time()
                    age = time.time() - password_submitted_at
                    now = time.time()
                    if log_callback and (now - last_heartbeat_at >= 8.0):
                        last_heartbeat_at = now
                        log_callback(
                            f"[CPA] Grok 登录：密码已提交，等待跳转/会话生效 "
                            f"({int(age)}s) url={current_url[:120]}"
                        )
                    # 提交后几秒仍停在登录页，主动打开 grok.com 验证是否已登录
                    if age >= 5 and not grok_probe_done:
                        grok_probe_done = True
                        if log_callback:
                            log_callback("[CPA] Grok 登录：提交后仍在登录页，打开 grok.com 确认会话")
                        try:
                            open_step_tab(
                                "https://grok.com/",
                                step_label="2c-确认Grok会话",
                                log_callback=log_callback,
                            )
                        except Exception:
                            try:
                                page.get("https://grok.com/")
                            except Exception:
                                pass
                        time.sleep(2.0)
                        continue
                    time.sleep(1.2)
                    continue
                if action == "cookie-dismiss":
                    if log_callback and last_action != f"cookie:{detail}":
                        last_action = f"cookie:{detail}"
                        log_callback(f"[CPA] Grok 登录：已关闭 Cookie 提示 ({detail or 'Accept'})")
                    time.sleep(0.8)
                    continue
                if action == "password-mismatch":
                    if log_callback and last_action != "password-mismatch":
                        last_action = "password-mismatch"
                        log_callback("[CPA] Grok 登录：密码未成功写入输入框，重试填写...")
                    time.sleep(0.8)
                    continue
                if action == "login-fill":
                    login_fill_seen = True
                marker = f"{action}:{detail}:{current_url}"
                if marker != last_action and action in {"login-fill", "click", "email-entry", "header-login"} and log_callback:
                    last_action = marker
                    if action == "email-entry":
                        log_callback(f"[CPA] Grok 登录：已点击 {detail or '使用邮箱登录'}")
                    elif action == "login-fill":
                        has_pw = (progress or {}).get("hasPassword")
                        email_val = (progress or {}).get("emailValue") or ""
                        submitted = bool((progress or {}).get("submitted"))
                        if has_pw:
                            log_callback(
                                f"[CPA] Grok 登录：已填写邮箱/密码"
                                f"{'并提交' if submitted else '（等待提交）'} ({email_val or email})"
                            )
                        else:
                            log_callback(f"[CPA] Grok 登录：已填写邮箱并继续 ({email_val or email})")
                    else:
                        log_callback(f"[CPA] Grok 登录：已点击 {detail or '按钮'}")
                # 密码提交后给页面/人机验证留时间，避免立刻重复提交
                if action == "login-fill" and (progress or {}).get("hasPassword") and (progress or {}).get("submitted"):
                    password_submitted_at = time.time()
                    last_heartbeat_at = time.time()
                    if log_callback:
                        log_callback("[CPA] Grok 登录：密码已提交，等待页面跳转（最多约 25s 内不重复点击）")
                    time.sleep(3.5)
                    continue
                if action in {"login-fill", "click", "email-entry", "header-login"}:
                    time.sleep(2.0)
                    continue
            time.sleep(1)
            continue

        if current == "ready":
            # 有邮箱密码时：
            # - 从 grok 首页进入：必须先见过登录 UI / 完成填写，防假已登录
            # - 直接邮箱登录：必须至少完成过一次填写/提交，防空页面假 ready
            if email and password and not login_fill_seen and (not saw_login_ui or prefer_email_login):
                ready_hits = 0
                if prefer_email_login:
                    # 轻量隔离失败残留：未密码登录却已 ready → 整浏览器重启一次
                    if not forced_restart_for_residue:
                        forced_restart_for_residue = True
                        if log_callback:
                            log_callback(
                                "[CPA] 检测到未邮箱密码登录就已进入 Grok，"
                                "疑似注册残留会话，回退整浏览器重启"
                            )
                        try:
                            _reset_browser_for_next_account(
                                log_callback=log_callback,
                                reason="before_login",
                            )
                        except Exception as restart_exc:
                            if log_callback:
                                log_callback(f"[CPA] 残留会话回退重启失败: {restart_exc}")
                        try:
                            open_step_tab(
                                "https://accounts.x.ai/sign-in?redirect=grok-com",
                                step_label="2-Grok邮箱登录",
                                log_callback=log_callback,
                            )
                        except Exception:
                            try:
                                page.get("https://accounts.x.ai/sign-in?redirect=grok-com")
                            except Exception:
                                pass
                        time.sleep(1.5)
                        continue
                    time.sleep(0.8)
                    continue
                if last_action != "force-email-login":
                    last_action = "force-email-login"
                    if log_callback:
                        log_callback("[CPA] 检测到疑似残留会话，不信任当前登录态，强制邮箱登录")
                    try:
                        open_step_tab(
                            "https://accounts.x.ai/sign-in?redirect=grok-com",
                            step_label="2b-账户登录",
                            log_callback=log_callback,
                        )
                    except Exception:
                        try:
                            page.get("https://accounts.x.ai/sign-in?redirect=grok-com")
                        except Exception:
                            pass
                time.sleep(1.5)
                continue
            # 若刚经历过登录页，ready 需要连续确认，避免闪一下误判
            ready_hits += 1
            need_hits = 2 if (saw_login_ui or login_fill_seen) else 1
            if ready_hits >= need_hits:
                if log_callback:
                    log_callback("[CPA] Grok 已登录成功，开始 Device 授权")
                time.sleep(1)
                return
            time.sleep(1)
            continue

        # loading / other：若刚提交过密码，优先等待/探测，并输出心跳
        ready_hits = 0
        if password_submitted_at is not None:
            age = time.time() - password_submitted_at
            now = time.time()
            if log_callback and (now - last_heartbeat_at >= 8.0):
                last_heartbeat_at = now
                has_sso = bool(_find_sso_cookie_in_browser_tabs())
                log_callback(
                    f"[CPA] Grok 登录：提交后页面状态={current or 'loading'} "
                    f"({int(age)}s) sso={'yes' if has_sso else 'no'} url={current_url[:100]}"
                )
            if age >= 5 and not grok_probe_done and "accounts.x.ai" in current_url:
                grok_probe_done = True
                if log_callback:
                    log_callback("[CPA] Grok 登录：打开 grok.com 确认是否已登录")
                try:
                    open_step_tab(
                        "https://grok.com/",
                        step_label="2c-确认Grok会话",
                        log_callback=log_callback,
                    )
                except Exception:
                    try:
                        page.get("https://grok.com/")
                    except Exception:
                        pass
                time.sleep(2.0)
                continue
        if password_submitted_at is not None and (time.time() - password_submitted_at) >= 25:
            # 25s 后 UI 仍 loading：有会话信号则直接授权，不要重填密码
            late_reason = _device_auth_early_exit_reason(
                login_fill_seen=login_fill_seen,
                password_submitted_at=password_submitted_at,
                current_state=current,
                current_url=current_url,
            )
            if late_reason:
                if log_callback:
                    log_callback(f"[CPA] {late_reason}")
                return
            if log_callback and last_action != "login-retry":
                last_action = "login-retry"
                log_callback("[CPA] Grok 登录：提交后仍未就绪，准备重试邮箱登录")
            password_submitted_at = None
            grok_probe_done = False
            try:
                page.run_js("window.__cpaLoginSubmittedAt = 0; return true;")
            except Exception:
                pass
        # Grok 入口可能单独卡在 Cloudflare 中间页（state=loading）
        if "grok.com" in (current_url or "").lower():
            cf_status = _probe_cloudflare_gate()
            if cf_status.get("present") and not cf_status.get("ready"):
                if last_action != "cf-grok-entry" and log_callback:
                    last_action = "cf-grok-entry"
                    token_len = (cf_status or {}).get("tokenLen") or 0
                    log_callback(
                        f"[CF] Grok 入口：检测到人机验证，自动处理 (token长度={token_len})"
                    )
                cf_result = _ensure_turnstile_passed(
                    log_callback=log_callback,
                    stage="Grok入口",
                    min_interval=6.0,
                )
                cf_state = _note_login_turnstile_outcome(
                    cf_result,
                    cf_state,
                    log_callback=log_callback,
                    stage="Grok入口",
                )
                time.sleep(1.2)
                continue

        if _is_cpa_auto_click_enabled() and email and password_submitted_at is None:
            progress = _progress_device_authorization_page(
                email=email,
                password=password,
                log_callback=log_callback,
            )
            action = str((progress or {}).get("action") or "")
            if action == "login-fill":
                login_fill_seen = True
                saw_login_ui = True
            if action == "wait-cloudflare":
                if last_action != f"wait-cloudflare-loading:{current_url}" and log_callback:
                    last_action = f"wait-cloudflare-loading:{current_url}"
                    token_len = (progress or {}).get("cfTokenLen") or 0
                    log_callback(
                        f"[CPA] Grok 登录：等待 Cloudflare 通过后再提交密码 "
                        f"(token长度={token_len})"
                    )
                cf_result = _ensure_turnstile_passed(
                    log_callback=log_callback,
                    stage="Grok登录",
                    min_interval=6.0,
                )
                cf_state = _note_login_turnstile_outcome(
                    cf_result,
                    cf_state,
                    log_callback=log_callback,
                    stage="Grok登录",
                )
                time.sleep(1.0)
                continue
            if action in {"login-fill", "click", "email-entry", "header-login"}:
                detail = str((progress or {}).get("text") or (progress or {}).get("submitText") or "")
                marker = f"{action}:{detail}"
                if marker != last_action and log_callback:
                    last_action = marker
                    if action == "header-login":
                        log_callback(f"[CPA] Grok 登录：已点击首页 {detail or '登录'}")
                    elif action == "email-entry":
                        log_callback(f"[CPA] Grok 登录：已点击 {detail or '使用邮箱登录'}")
                    elif action == "login-fill":
                        log_callback("[CPA] Grok 登录：已填写登录信息")
                    else:
                        log_callback(f"[CPA] Grok 登录：已点击 {detail or '按钮'}")
                time.sleep(2.0)
                continue
        time.sleep(1)

    # 超时前再强制走一遍 accounts 登录页，尽量避免“未登录就授权”
    if _is_cpa_auto_click_enabled() and email and password:
        if log_callback:
            log_callback("[CPA] Grok 首页登录超时，改为打开账户登录页继续邮箱登录")
        try:
            open_step_tab(
                "https://accounts.x.ai/sign-in?redirect=grok-com",
                step_label="2b-账户登录",
                log_callback=log_callback,
            )
        except Exception:
            try:
                page.get("https://accounts.x.ai/sign-in?redirect=grok-com")
            except Exception:
                pass
        extra_deadline = time.time() + min(45, max(15, wait_seconds // 2 or 15))
        while time.time() < extra_deadline:
            progress = _progress_device_authorization_page(
                email=email,
                password=password,
                log_callback=log_callback,
            )
            action = str((progress or {}).get("action") or "")
            if action == "login-error":
                detail = str((progress or {}).get("text") or "邮箱或密码错误")
                if log_callback:
                    log_callback(f"[CPA] 账户登录页失败: {detail}")
                raise RuntimeError(f"Grok 邮箱登录失败: {detail}")
            if action == "wait-cloudflare":
                if log_callback:
                    log_callback("[CPA] 账户登录页：等待 Cloudflare 通过后再提交")
                cf_result = _ensure_turnstile_passed(
                    log_callback=log_callback,
                    stage="账户登录",
                    min_interval=6.0,
                )
                cf_state = _note_login_turnstile_outcome(
                    cf_result,
                    cf_state,
                    log_callback=log_callback,
                    stage="账户登录",
                )
                time.sleep(1.0)
                continue
            if action == "cookie-dismiss":
                if log_callback:
                    log_callback("[CPA] 账户登录页：已关闭 Cookie 提示")
                time.sleep(0.8)
                continue
            if action in {"login-fill", "click", "email-entry", "header-login"}:
                detail = str((progress or {}).get("text") or (progress or {}).get("submitText") or "")
                if log_callback:
                    log_callback(f"[CPA] 账户登录页处理: {action} {detail}".strip())
                time.sleep(3.0 if action == "login-fill" and (progress or {}).get("hasPassword") else 2.0)
            try:
                state = _run_device_page_js("return detectGrokWebState();") or {}
            except Exception:
                state = {}
            extra_state = str((state or {}).get("state") or "")
            extra_url = str((state or {}).get("url") or getattr(page, "url", "") or "")
            if extra_state == "ready":
                if log_callback:
                    log_callback("[CPA] Grok 已登录成功，开始 Device 授权")
                return
            # 兜底轮询同样认 sso，不必死等 ready
            if action == "login-fill":
                login_fill_seen = True
                if (progress or {}).get("hasPassword") and (progress or {}).get("submitted"):
                    password_submitted_at = time.time()
            extra_reason = _device_auth_early_exit_reason(
                login_fill_seen=login_fill_seen,
                password_submitted_at=password_submitted_at,
                current_state=extra_state,
                current_url=extra_url,
            )
            if extra_reason:
                if log_callback:
                    log_callback(f"[CPA] {extra_reason}")
                return
            # 登录后跳回 grok.com 再确认
            try:
                if "accounts.x.ai" not in str(getattr(page, "url", "") or ""):
                    if extra_state != "login":
                        page.get("https://grok.com/")
                        time.sleep(2)
            except Exception:
                pass
            time.sleep(1)

    _request_browser_restart_next("login-unconfirmed", log_callback)
    raise RuntimeError(
        f"Grok 登录仍未确认（已等待 {wait_seconds}s+），已中止 Device 授权"
        "（避免未登录硬进授权页反复卡死）"
    )


def _wait_cpa_device_authorization(
    remote_url,
    management_key,
    state,
    email="",
    password="",
    log_callback=None,
    device_url="",
):
    try:
        timeout_seconds = max(1, int(config.get("cpa_device_timeout", 600) or 600))
    except (TypeError, ValueError):
        timeout_seconds = 600
    deadline = time.time() + timeout_seconds
    success_statuses = {"ok", "success", "authorized", "complete", "completed"}
    failure_statuses = {"error", "failed", "expired", "cancelled", "canceled"}
    last_action = ""
    # 这个锁在 Python 页面导航之后仍然存在，不能只放在页面 window 里。
    last_device_action_at = 0.0
    last_device_action_url = ""
    last_device_action_text = ""
    stable_signature = ""
    stable_hits = 0
    recovery_allow_only = False
    done_page_logged = False
    # 点 Continue 后开始计时；先自救刷新，再重生授权码
    post_continue_at = 0.0
    post_continue_rescued = False

    while True:
        # 页面导航会销毁 window.__cpaLastAction；Python 锁必须先于页面检查。
        # 锁只在页面未变化时以短间隔轮询，变化后立刻继续下一步。
        if last_device_action_at:
            settle_delay = _device_action_settle_delay(
                last_device_action_at,
                last_device_action_url,
                last_device_action_text,
            )
            if settle_delay is not None:
                time.sleep(settle_delay)
                continue
            last_device_action_at = 0.0
            stable_signature = ""
            stable_hits = 0
        if _is_invalid_device_action_page(page):
            if log_callback:
                log_callback("[CPA] 授权页出现 Invalid action，重新打开本次设备授权链接后返回允许页重试，不重新点击继续")
            try:
                page.back()
            except Exception:
                pass
            recovery_allow_only = True
            stable_signature = ""
            stable_hits = 0
            time.sleep(5)
            continue
        if _is_invalid_device_code_page(page):
            raise RuntimeError("CPA Device 授权码已失效或过期，需要重新生成授权链接")
        device_done = _is_device_authorization_done_page(page)
        if device_done and not done_page_logged:
            if log_callback:
                log_callback("[CPA] 检测到设备已授权完成页，停止页面点击，等待 CPA 状态确认")
            done_page_logged = True
            post_continue_at = 0.0
        # Continue 后：先轻量自救（刷新同授权链接），再提前重生
        if post_continue_at and not device_done:
            try:
                allow_wait = max(
                    12.0,
                    float(config.get("cpa_device_allow_wait_seconds", 25) or 25),
                )
            except (TypeError, ValueError):
                allow_wait = 25.0
            try:
                rescue_after = max(
                    6.0,
                    float(config.get("cpa_device_allow_rescue_seconds", 10) or 10),
                )
            except (TypeError, ValueError):
                rescue_after = 10.0
            if rescue_after >= allow_wait:
                rescue_after = max(6.0, allow_wait * 0.4)
            elapsed = time.time() - post_continue_at
            try:
                snap_now = _device_page_action_snapshot()
            except Exception:
                snap_now = {}
            snap_text = str((snap_now or {}).get("text") or "")
            allow_visible = _device_action_text_is_allow(snap_text)
            if allow_visible:
                pass
            elif (not post_continue_rescued) and elapsed >= rescue_after:
                post_continue_rescued = True
                rescue_url = str(device_url or getattr(page, "url", "") or "").strip()
                if log_callback:
                    log_callback(
                        f"[CPA] 点击继续后 {int(elapsed)}s 仍未出现允许，"
                        "刷新授权页自救（1 次）"
                    )
                if rescue_url and page is not None:
                    try:
                        page.get(rescue_url)
                        try:
                            page.wait.doc_loaded()
                        except Exception:
                            pass
                    except Exception as reload_exc:
                        if log_callback:
                            log_callback(f"[CPA] 授权页自救刷新失败（继续等待）: {reload_exc}")
                # 允许重新点 Continue / 识别 Allow
                last_device_action_at = 0.0
                last_device_action_url = ""
                last_device_action_text = ""
                stable_signature = ""
                stable_hits = 0
                recovery_allow_only = False
                time.sleep(1.0)
                continue
            elif elapsed >= allow_wait:
                if log_callback:
                    log_callback(
                        f"[CPA] 点击继续后 {int(allow_wait)}s 仍未出现允许，"
                        "重新生成授权链接"
                    )
                raise RuntimeError(
                    "CPA Device 点击继续后长时间未出现允许，需要重新生成授权链接"
                )
        snapshot = {}
        if not device_done and _is_cpa_auto_click_enabled() and page is not None:
            snapshot = _device_page_action_snapshot()
            if snapshot.get("readyState") != "complete" or not snapshot.get("text"):
                stable_signature = ""
                stable_hits = 0
                time.sleep(0.5)
                continue
            signature = str(snapshot.get("signature") or "")
            if signature == stable_signature:
                stable_hits += 1
            else:
                stable_signature = signature
                stable_hits = 1
            # 至少连续看到同一个可点击动作三次，避免页面刚导航完成但事件处理器尚未挂载。
            if stable_hits < 3:
                time.sleep(0.5)
                continue
            if last_device_action_url and not recovery_allow_only:
                if snapshot.get("url") == last_device_action_url and snapshot.get("text") == last_device_action_text:
                    # 点过 Allow 后页面可能暂不变，仍要去轮询 CPA status
                    if _device_action_text_is_allow(last_device_action_text) or _device_action_text_is_allow(
                        snapshot.get("text")
                    ):
                        pass
                    else:
                        time.sleep(1)
                        continue
            if recovery_allow_only and "allow" not in str(snapshot.get("text", "")).lower() and "允许" not in str(snapshot.get("text", "")):
                time.sleep(1)
                continue
        if not device_done and _is_cpa_auto_click_enabled():
            progress = _progress_device_authorization_page(
                email=email,
                password=password,
                log_callback=log_callback,
            )
            action = str((progress or {}).get("action") or "")
            detail = str((progress or {}).get("text") or (progress or {}).get("submitText") or "")
            marker = f"{action}:{detail}"
            if marker != last_action and action in {"login-fill", "click", "email-entry", "header-login", "device-continue"} and log_callback:
                last_action = marker
                if action == "header-login":
                    log_callback(f"[CPA] 已点击首页登录: {detail or '登录'}")
                elif action == "email-entry":
                    log_callback(f"[CPA] 已点击邮箱登录入口: {detail or '使用邮箱登录'}")
                elif action == "login-fill":
                    has_pw = (progress or {}).get("hasPassword")
                    log_callback("[CPA] 已自动填写邮箱/密码并提交" if has_pw else "[CPA] 已自动填写邮箱并继续")
                elif action == "device-continue":
                    log_callback(f"[CPA] 设备代码已由授权页自动带入，仅点击继续: {detail or '继续'}")
                else:
                    log_callback(f"[CPA] 已自动点击授权按钮: {detail or '按钮'}")
            if action in {"login-fill", "click", "email-entry", "header-login", "device-continue", "wait-cloudflare"}:
                if action in {"login-fill", "email-entry", "header-login", "wait-cloudflare"}:
                    # 授权页再次要求登录时暂停 Continue→Allow 超时
                    post_continue_at = 0.0
                if action == "device-continue":
                    last_device_action_url = str(progress.get("url") or snapshot.get("url") or "")
                    last_device_action_text = detail
                    recovery_allow_only = False
                    if _device_action_text_is_allow(detail):
                        # 已点 Allow：立刻进入 CPA 状态确认，不锁 settle 整轮跳过
                        post_continue_at = 0.0
                        last_device_action_at = 0.0
                    else:
                        last_device_action_at = time.time()
                        if not post_continue_at:
                            post_continue_at = time.time()
                        continue
                if action == "wait-cloudflare":
                    _ensure_turnstile_passed(
                        log_callback=log_callback,
                        stage="Device授权登录",
                        min_interval=6.0,
                    )
                    time.sleep(1.0)
                    continue
                time.sleep(2.0)

        response = requests.get(
            f"{remote_url.rstrip('/')}/v0/management/get-auth-status",
            params={"state": state},
            headers=_cpa_management_headers(management_key),
            proxies={},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        status = str(data.get("status", "")).strip().lower()
        if status in success_statuses:
            return data
        if status in failure_statuses:
            if _is_retryable_device_status(status, data):
                raise RuntimeError("CPA Device 授权码已失效或过期，需要重新生成授权链接")
            raise RuntimeError(data.get("message") or data.get("msg") or f"CPA Device Flow 状态: {status}")
        if time.time() >= deadline:
            raise TimeoutError(f"CPA Device Flow 等待超过 {timeout_seconds} 秒")
        time.sleep(0.5 if device_done else 2)



def _resolve_login_isolation_mode():
    """返回登录前隔离策略：auto / restart / clear / off。"""
    restart_flag = config.get("cpa_restart_browser_before_login", True)
    if isinstance(restart_flag, str):
        restart_flag = restart_flag.strip().lower() in {"1", "true", "yes", "on"}
    if not restart_flag:
        return "off"
    mode = str(config.get("cpa_login_isolation", "auto") or "auto").strip().lower()
    if mode in {"auto", "restart", "clear", "off"}:
        return mode
    # 兼容旧布尔语义
    if mode in {"1", "true", "yes", "on"}:
        return "restart"
    if mode in {"0", "false", "no"}:
        return "off"
    return "auto"


def _has_xai_session_residue():
    """检测是否仍有可用于假登录的 xAI SSO 残留。

    只认 sso / sso-rw（及安全前缀变体），不再把泛化 session cookie 当残留，
    避免 clearCookies 后误判导致总是走慢路径。
    """
    try:
        return bool(_find_sso_cookie_in_browser_tabs())
    except Exception:
        return False


def _probe_xai_session_residue(step_label, log_callback=None):
    """慢路径分步 residual 探针：只打日志，返回是否仍有 SSO 残留。"""
    try:
        has_residue = bool(_has_xai_session_residue())
    except Exception:
        has_residue = False
    if log_callback:
        status = "仍有残留" if has_residue else "已干净"
        log_callback(f"[CPA] 轻量隔离探针：{step_label} → {status}")
    return has_residue


def _wait_xai_session_residue_cleared(
    retries=2,
    interval=0.2,
    log_callback=None,
    reason="清 Cookie 后",
):
    """clearCookies 后 residual 偶发短暂仍可见，短轮询再决定是否升级慢路径。

    立即检测 1 次，若仍有残留则再重试 retries 次（默认 2 次，间隔 ~200ms）。
    返回 True 表示已干净；False 表示仍有残留。
    """
    try:
        retries = max(0, int(retries))
    except Exception:
        retries = 2
    try:
        interval = max(0.05, float(interval))
    except Exception:
        interval = 0.2

    total = retries + 1
    for attempt in range(total):
        if not _has_xai_session_residue():
            if attempt > 0 and log_callback:
                log_callback(
                    f"[CPA] 轻量隔离：{reason}残留检测重试后已干净"
                    f"（第 {attempt + 1}/{total} 次）"
                )
            return True
        if attempt + 1 < total:
            if log_callback and attempt == 0:
                log_callback(
                    f"[CPA] 轻量隔离：{reason}短暂检测到残留，"
                    f"{interval:.1f}s 后重试（最多 {retries} 次）"
                )
            time.sleep(interval)
    return False


def _clear_browser_cookies_cdp(log_callback=None):
    """通过 CDP 清空浏览器 Cookie，成功返回 True。"""
    if browser is None:
        return False
    for cdp_cmd in ("Storage.clearCookies", "Network.clearBrowserCookies"):
        try:
            browser._run_cdp(cdp_cmd)
            if log_callback:
                log_callback(f"[CPA] 轻量隔离：已执行 {cdp_cmd}")
            return True
        except Exception:
            continue
    if log_callback:
        log_callback("[CPA] 轻量隔离：Cookie 清理命令失败")
    return False


def _clear_xai_site_storage(log_callback=None):
    """导航到 accounts/grok 并清理 localStorage/sessionStorage。"""
    global page
    if page is None:
        return
    for site, label in (
        ("https://accounts.x.ai/sign-in", "accounts.x.ai"),
        ("https://grok.com/", "grok.com"),
    ):
        try:
            if log_callback:
                log_callback(f"[CPA] 轻量隔离：清理 {label} 站点存储")
            page.get(site)
            time.sleep(0.2)
            page.run_js(
                "try{localStorage.clear();sessionStorage.clear();}catch(e){} return true;"
            )
        except Exception as exc:
            if log_callback:
                log_callback(f"[CPA] 轻量隔离：清理 {label} 失败（忽略）: {exc}")


def _open_accounts_sign_out(log_callback=None):
    """打开 accounts 退出页（慢路径才使用）。"""
    global page
    if page is None:
        return
    try:
        if log_callback:
            log_callback("[CPA] 轻量隔离：打开 accounts 退出页")
        page.get("https://accounts.x.ai/sign-out")
        try:
            page.wait.doc_loaded()
        except Exception:
            pass
        time.sleep(0.4)
    except Exception as exc:
        if log_callback:
            log_callback(f"[CPA] 打开退出页失败（继续清理 Cookie）: {exc}")


def _try_light_login_isolation(log_callback=None, purpose="login"):
    """轻量隔离：优先退出页，失败再 clearCookies / 站点存储。

    purpose:
      - login: 注册完成后、二次邮箱登录前
      - next_account: 连续注册账号切换

    实测（探针归因）：
    - 单独 Storage.clearCookies 注册后几乎总清不掉 sso
    - 打开 accounts 退出页后 residual 通常立刻干净
    因此主路径改为 sign-out；clearCookies / 站点存储仅作兜底。

    成功（足够干净）返回 True；失败或不干净返回 False。
    不访问 auth.x.ai 根路径（该地址会 404，无清理价值）。
    """
    global page
    if browser is None or page is None:
        return False
    purpose = str(purpose or "login").strip().lower()
    stage = "登录前" if purpose == "login" else "下一账号"
    try:
        if log_callback:
            log_callback(
                f"[CPA] {stage}轻量隔离：优先退出页"
                "（单独 clearCookies 注册后常清不干净）"
            )

        # 0) 已无残留则跳过一切
        if not _has_xai_session_residue():
            if log_callback:
                log_callback(
                    "[CPA] 轻量隔离完成：检测无 sso 残留"
                    "（跳过退出页 / clearCookies / 站点存储）"
                )
            return True

        # 1) 主路径：退出页（探针已证明这是清 SSO 的关键步）
        _open_accounts_sign_out(log_callback=log_callback)
        if not _probe_xai_session_residue("退出页后", log_callback=log_callback):
            if log_callback:
                log_callback(
                    "[CPA] 轻量隔离归因：首次 residual 变干净出现在「退出页」"
                    "（跳过 clearCookies / 站点存储）"
                )
                log_callback(
                    "[CPA] 轻量隔离完成：未检测到 sso/登录会话，可在当前浏览器邮箱登录"
                )
            return True

        # 2) 兜底：clearCookies（退出页异常/未生效时）
        if log_callback:
            log_callback("[CPA] 退出页后仍有残留，兜底 clearCookies")
        if not _clear_browser_cookies_cdp(log_callback=log_callback):
            if log_callback:
                log_callback("[CPA] 轻量隔离：clearCookies 失败，继续尝试站点存储清理")
        elif _wait_xai_session_residue_cleared(
            retries=1,
            interval=0.2,
            log_callback=log_callback,
            reason="clearCookies 后",
        ):
            if log_callback:
                log_callback(
                    "[CPA] 轻量隔离归因：首次 residual 变干净出现在「clearCookies」"
                    "（跳过站点存储清理）"
                )
                log_callback(
                    "[CPA] 轻量隔离完成：未检测到 sso/登录会话，可在当前浏览器邮箱登录"
                )
            return True

        # 3) 兜底：站点存储（对 cookie residual 通常无直接作用，保留最后防线）
        if log_callback:
            log_callback("[CPA] clearCookies 后仍有残留，清理站点存储")
        _clear_xai_site_storage(log_callback=log_callback)
        first_clean_step = None
        if not _probe_xai_session_residue("站点存储清理后", log_callback=log_callback):
            first_clean_step = "站点存储清理"

        if not _wait_xai_session_residue_cleared(
            retries=1,
            interval=0.2,
            log_callback=log_callback,
            reason="慢路径清理后",
        ):
            if log_callback:
                log_callback("[CPA] 轻量隔离后仍检测到会话残留，判定不干净")
            return False
        if log_callback:
            if first_clean_step:
                log_callback(
                    f"[CPA] 轻量隔离归因：首次 residual 变干净出现在「{first_clean_step}」"
                )
            else:
                log_callback(
                    "[CPA] 轻量隔离归因：分步探针仍见残留，终检重试后才干净"
                )
            log_callback(
                "[CPA] 轻量隔离完成：未检测到 sso/登录会话，可在当前浏览器邮箱登录"
            )
        return True
    except Exception as exc:
        if log_callback:
            log_callback(f"[CPA] 轻量隔离异常: {exc}")
        return False


def _ensure_clean_browser_before_login(log_callback=None):
    """方案 D：默认轻量隔离，不干净再整浏览器重启。"""
    mode = _resolve_login_isolation_mode()
    if mode == "off":
        if log_callback:
            log_callback("[CPA] 步骤 0/2：已关闭登录前隔离，直接邮箱登录")
        return "off"

    if mode == "restart":
        _reset_browser_for_next_account(log_callback=log_callback, reason="before_login")
        if log_callback:
            log_callback("[CPA] 步骤 0/2：注册完成，重启浏览器后再登录")
        return "restarted"

    light_ok = _try_light_login_isolation(log_callback=log_callback)
    if light_ok:
        if log_callback:
            log_callback("[CPA] 步骤 0/2：注册完成，轻量隔离后邮箱登录（未重启浏览器）")
        return "cleared"

    if mode == "clear":
        if log_callback:
            log_callback("[CPA] 步骤 0/2：轻量隔离不干净，仍继续当前浏览器（cpa_login_isolation=clear）")
        return "cleared-dirty"

    # auto → 回退整浏览器重启
    if log_callback:
        log_callback("[CPA] 轻量隔离不干净，回退为整浏览器重启后再邮箱登录")
    _reset_browser_for_next_account(log_callback=log_callback, reason="before_login")
    if log_callback:
        log_callback("[CPA] 步骤 0/2：注册完成，已回退重启浏览器后再登录")
    return "restarted-fallback"


def _run_cpa_device_flow(remote_url, management_key, email="", password="", log_callback=None):
    def _cpa_log(message):
        if log_callback:
            log_callback(f"[CPA] {str(message).strip()}")

    if page is None:
        raise RuntimeError("浏览器页面尚未连接，无法打开 CPA 授权链接")

    # Device 入库前必须先登录 Grok：
    # - 配置开启 cpa_prepare_grok_web，或
    # - 已有注册邮箱/密码（自动邮箱登录）
    should_login_grok = bool(config.get("cpa_prepare_grok_web", False)) or bool(email and password)
    if should_login_grok:
        # 注册刚结束时浏览器里可能残留 SSO/半登录态，直接授权会失败。
        # 方案 D：默认先轻量隔离（退出+清会话），不干净再整浏览器重启，然后邮箱登录。
        _ensure_clean_browser_before_login(log_callback=log_callback)
        _cpa_log("步骤 1/2：先登录 Grok Web（使用邮箱登录）")
        _prepare_grok_web_session(email=email, password=password, log_callback=log_callback)
    else:
        _cpa_log("未配置 Grok 预登录，将直接进入 Device 授权")

    _cpa_log("步骤 2/2：生成并打开 CPA Device 授权链接")
    try:
        refresh_retries = max(0, int(config.get("cpa_device_refresh_retries", 2) or 0))
    except (TypeError, ValueError):
        refresh_retries = 2
    result = None
    for auth_attempt in range(refresh_retries + 1):
        auth = _create_cpa_device_authorization(
            remote_url, management_key, log_callback=_cpa_log
        )
        url = str(auth["url"]).strip()
        _cpa_log(f"Device 授权链接（第 {auth_attempt + 1} 次）: {url}")
        open_step_tab(url, step_label="3-Device授权", log_callback=log_callback)
        if _is_cpa_auto_click_enabled():
            _cpa_log("授权页自动处理：若再次要求登录则邮箱登录，然后点“允许”")
        else:
            _cpa_log("请在浏览器中完成授权，CPA 正在等待结果...")
        try:
            result = _wait_cpa_device_authorization(
                remote_url,
                management_key,
                auth["state"],
                email=email,
                password=password,
                log_callback=log_callback,
                device_url=url,
            )
            break
        except RuntimeError as exc:
            message = str(exc)
            retryable = (
                "授权码已失效" in message
                or "invalid_code" in message
                or "重新生成授权链接" in message
                or "长时间未出现允许" in message
            )
            if not retryable or auth_attempt >= refresh_retries:
                raise
            _cpa_log(f"检测到授权码失效，重新生成授权链接（{auth_attempt + 1}/{refresh_retries}）")
            time.sleep(1)
    if result is None:
        raise RuntimeError("CPA Device 授权未完成")
    _cpa_log(f"Device 授权完成，CPA 已自动入库 (status={result.get('status')})")


def add_sso_to_cpa(raw_token, email="", password="", log_callback=None):
    """按配置通过 Device Flow 或旧 OAuth 流程完成 CPA 入库。

    返回 True 表示 CPA 侧无需处理或已成功；False 表示入库失败。
    Device Flow 由 CPA 自己保存授权结果；旧 OAuth 流程才会把 SSO 换成
    access/refresh token 并写成 CPA 的 xai-<email>.json。

    - 本地：写入 cpa_auth_dir，CPA 监听热加载
    - 远程：POST Management API /v0/management/auth-files（cpa_remote_url + cpa_management_key）
    """
    if not config.get("cpa_auto_add", False):
        return True
    auth_dir = str(config.get("cpa_auth_dir", "") or "").strip()
    remote_url = str(config.get("cpa_remote_url", "") or "").strip()
    management_key = str(config.get("cpa_management_key", "") or "").strip()
    if not auth_dir and not remote_url:
        if log_callback:
            log_callback("[Debug] 已开启 CPA 直出但未配置 cpa_auth_dir 或 cpa_remote_url，跳过")
        return False
    if remote_url and not management_key:
        if log_callback:
            log_callback("[Debug] 已配置 cpa_remote_url 但未配置 cpa_management_key，跳过远程上传")
        remote_url = ""
    if not auth_dir and not remote_url:
        return False
    auth_flow = str(config.get("cpa_auth_flow", "oauth") or "oauth").strip().lower()
    if auth_flow == "device":
        if not remote_url or not management_key:
            if log_callback:
                log_callback("[CPA] Device Flow 需要 cpa_remote_url 和 cpa_management_key，跳过")
            return False
        try:
            _run_cpa_device_flow(
                remote_url,
                management_key,
                email=email,
                password=password,
                log_callback=log_callback,
            )
            return True
        except Exception as exc:
            if log_callback:
                log_callback(f"[CPA] Device Flow 失败: {exc}")
            msg = str(exc)
            if any(
                key in msg
                for key in (
                    "Turnstile",
                    "Cloudflare",
                    "登录仍未确认",
                    "中止 Device",
                    "邮箱登录失败",
                )
            ):
                _request_browser_restart_next("device-flow-login-cf", log_callback)
            return False
    sso = _normalize_sso_token(raw_token)
    if not sso:
        if log_callback:
            log_callback("[CPA] 缺少 SSO，无法走 OAuth 入库")
        return False
    proxy = _resolve_cpa_proxy()

    def _cpa_log(message):
        if log_callback:
            log_callback(f"[CPA] {str(message).strip()}")

    try:
        _cpa_log(f"SSO → 授权码流程换 token (proxy={proxy}) ...")
        token = _s2cpa.sso_to_token(sso, proxy=proxy, log=_cpa_log)
        if not token:
            _cpa_log("授权码流程换 token 失败，跳过")
            return False
        record = _s2cpa.token_to_cpa_record(token, email=email, sso=sso)
        ap = _s2cpa.decode_jwt_payload(record.get("access_token", ""))
        ref = ap.get("referrer")
        if ref != "grok-build":
            _cpa_log(f"警告: access_token referrer={ref!r}，预期 grok-build")
        else:
            _cpa_log("access_token referrer=grok-build OK")
        wrote_any = False
        if auth_dir:
            try:
                path = _s2cpa.write_cpa_auth(_s2cpa.Path(auth_dir), record)
                _cpa_log(f"已写入本地 {path}")
                wrote_any = True
            except Exception as local_exc:
                _cpa_log(f"本地写入失败: {local_exc}")
        if remote_url:
            try:
                name = _s2cpa.upload_cpa_auth_remote(remote_url, management_key, record)
                _cpa_log(f"已上传远程 {remote_url.rstrip('/')}/.../{name}")
                wrote_any = True
            except Exception as remote_exc:
                _cpa_log(f"远程上传失败: {remote_exc}")
        return wrote_any
    except Exception as exc:
        _cpa_log(f"直出失败: {exc}")
        return False


def _can_start_device_flow_without_sso(email, password):
    """Device Flow 使用邮箱密码登录时，不要求先拿到注册页 SSO Cookie。"""
    auth_flow = str(config.get("cpa_auth_flow", "oauth") or "oauth").strip().lower()
    return bool(
        config.get("cpa_auto_add", False)
        and auth_flow == "device"
        and str(config.get("cpa_remote_url", "") or "").strip()
        and str(config.get("cpa_management_key", "") or "").strip()
        and str(email or "").strip()
        and str(password or "").strip()
    )


def is_browser_incognito_enabled():
    configured = config.get("browser_incognito")
    if configured is None:
        return os.environ.get("GROK_DOCKER") == "1"
    if isinstance(configured, str):
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return bool(configured)


def is_browser_clear_data_enabled():
    configured = config.get("browser_clear_data")
    if configured is None:
        return str(config.get("browser_backend", "chromium") or "chromium").strip().lower() in {
            "bitbrowser",
            "local_chrome",
        }
    if isinstance(configured, str):
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return bool(configured)


def clear_browser_session_data(log_callback=None):
    if browser is None or not is_browser_clear_data_enabled():
        return
    backend = str(config.get("browser_backend", "chromium") or "chromium").strip().lower()
    # 本机无痕模式依赖“强制重建 Chrome 进程”隔离账号，不在 about:blank 上清 storage。
    if backend == "local_chrome":
        if log_callback:
            log_callback("[LocalChrome] 使用独立调试 Chrome；账号切换会重新打开，无需清理当前页存储")
        return
    label = "BitBrowser" if backend == "bitbrowser" else "Browser"

    try:
        browser._run_cdp("Storage.clearCookies")
    except Exception as exc:
        if log_callback:
            log_callback(f"[{label}] 清理 Cookie 失败: {exc}")

    try:
        if page is not None and hasattr(page, "run_js"):
            page.run_js("localStorage.clear(); sessionStorage.clear();")
    except Exception as exc:
        if log_callback:
            log_callback(f"[{label}] 清理当前页面站点存储失败: {exc}")
    if log_callback:
        if backend == "bitbrowser":
            log_callback("[BitBrowser] 已清理 Cookie 和当前页面站点存储；缓存由 BitBrowser 启动前清理配置负责")
        elif backend in {"chromium", "chrome", "docker"}:
            log_callback("[DockerChromium] 已清理 Cookie 和当前页面站点存储")
        else:
            log_callback(f"[{label}] 已清理 Cookie 和当前页面站点存储")


def create_browser_options():
    options = ChromiumOptions()
    options.auto_port()
    options.set_timeouts(base=1)
    if os.environ.get("GROK_DOCKER") == "1":
        options.set_argument("--no-sandbox")
        options.set_argument("--disable-dev-shm-usage")
    if is_browser_incognito_enabled():
        options.set_argument("--incognito")
    if os.path.exists(EXTENSION_PATH):
        options.add_extension(EXTENSION_PATH)
    return options


def _build_request_kwargs(**kwargs):
    request_kwargs = dict(kwargs)
    proxies = request_kwargs.pop("proxies", None)
    if proxies is None:
        proxies = get_proxies()
    if proxies:
        request_kwargs["proxies"] = proxies
    request_kwargs.setdefault("timeout", 15)
    return request_kwargs


def http_get(url, **kwargs):
    try:
        return requests.get(url, **_build_request_kwargs(**kwargs))
    except Exception as exc:
        err = str(exc)
        # 代理不可用时自动回退为直连，避免整个流程直接失败
        if "127.0.0.1 port 7890" in err or "Could not connect to server" in err:
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.get(url, **_build_request_kwargs(**retry_kwargs))
        raise


def http_post(url, **kwargs):
    try:
        return requests.post(url, **_build_request_kwargs(**kwargs))
    except Exception as exc:
        err = str(exc)
        if "127.0.0.1 port 7890" in err or "Could not connect to server" in err:
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.post(url, **_build_request_kwargs(**retry_kwargs))
        raise


def raise_if_cancelled(cancel_callback=None):
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled("用户停止注册")


def sleep_with_cancel(seconds, cancel_callback=None):
    deadline = time.time() + max(seconds, 0)
    while True:
        raise_if_cancelled(cancel_callback)
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def get_domains(api_key=None):
    headers = {}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = http_get(f"{DUCKMAIL_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def create_account(address, password, api_key=None, expires_in=0):
    headers = {"Content-Type": "application/json"}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = {"address": address, "password": password, "expiresIn": expires_in}
    resp = http_post(f"{DUCKMAIL_API_BASE}/accounts", json=data, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_token(address, password):
    data = {"address": address, "password": password}
    resp = http_post(f"{DUCKMAIL_API_BASE}/token", json=data)
    resp.raise_for_status()
    return resp.json().get("token")


def get_messages(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def get_message_detail(token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_domains(api_base, api_key=None):
    headers = cloudflare_build_headers(content_type=False)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_domains", "/domains")
    params = cloudflare_apply_auth_params()
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    return _pick_list_payload(resp.json())


def cloudflare_create_account(api_base, address, password, api_key=None, expires_in=0):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    payload = {"address": address, "password": password, "expiresIn": expires_in}
    path = get_cloudflare_path("cloudflare_path_accounts", "/accounts")
    params = cloudflare_apply_auth_params()
    resp = http_post(f"{api_base}{path}", json=payload, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_token(api_base, address, password, api_key=None):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_token", "/token")
    resp = http_post(
        f"{api_base}{path}",
        json={"address": address, "password": password},
        headers=headers,
        params=cloudflare_apply_auth_params(),
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        if data.get("token"):
            return data.get("token")
        if isinstance(data.get("data"), dict) and data["data"].get("token"):
            return data["data"].get("token")
    return None


def cloudflare_get_messages(api_base, token):
    headers = cloudflare_apply_custom_auth({"Authorization": f"Bearer {token}"})
    path = get_cloudflare_path("cloudflare_path_messages", "/messages")
    params = {"limit": 20, "offset": 0}
    params = cloudflare_apply_auth_params(params)
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare messages 返回非JSON: {resp.text[:300]}")
    return _pick_list_payload(data)


def cloudflare_get_message_detail(api_base, token, message_id):
    headers = cloudflare_apply_custom_auth({"Authorization": f"Bearer {token}"})
    candidates = [
        f"{api_base}/api/mail/{message_id}",
        f"{api_base}{get_cloudflare_path('cloudflare_path_messages', '/messages')}/{message_id}",
    ]
    last_err = None
    for url in candidates:
        try:
            resp = http_get(
                url,
                headers=headers,
                params=cloudflare_apply_auth_params(),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                return data["data"]
            return data
        except Exception as exc:
            last_err = exc
            continue
    raise Exception(f"Cloudflare 获取邮件详情失败: {last_err}")


YYDS_API_BASE = "https://maliapi.215.im/v1"


def get_yyds_api_key():
    return config.get("yyds_api_key", "")


def get_yyds_jwt():
    return config.get("yyds_jwt", "")


def yyds_get_domains(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", []) if data.get("success") else []


def yyds_create_account(address=None, domain=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    payload = {}
    if address:
        payload["address"] = address
    if domain:
        payload["domain"] = domain
    elif key or token:
        payload["autoDomainStrategy"] = "prefer_owned"
    resp = http_post(f"{YYDS_API_BASE}/accounts", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 创建邮箱失败: {data}")


def yyds_get_token(address, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_post(
        f"{YYDS_API_BASE}/token", json={"address": address}, headers=headers
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("token")
    raise Exception(f"YYDS 获取token失败: {data}")


def yyds_get_messages(address, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(
        f"{YYDS_API_BASE}/messages",
        params={"address": address},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("messages", [])
    return []


def yyds_get_message_detail(message_id, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 获取邮件详情失败: {data}")


def yyds_generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def yyds_pick_domain(api_key=None, jwt=None):
    domains = yyds_get_domains(api_key=api_key, jwt=jwt)
    if not domains:
        raise Exception("YYDS 没有返回任何可用域名")
    private = [d for d in domains if d.get("isVerified") and not d.get("isPublic")]
    if private:
        return private[0]["domain"]
    public = [d for d in domains if d.get("isVerified") and d.get("isPublic")]
    if public:
        return public[0]["domain"]
    verified = [d for d in domains if d.get("isVerified")]
    if verified:
        return verified[0]["domain"]
    raise Exception("YYDS 无已验证域名可用")


def yyds_get_email_and_token(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    if not token and not key:
        raise Exception("YYDS API Key 或 JWT 未配置")
    domain = yyds_pick_domain(api_key=key, jwt=token)
    username = yyds_generate_username(10)
    result = yyds_create_account(
        address=username, domain=domain, api_key=key, jwt=token
    )
    address = result.get("address") or f"{username}@{domain}"
    temp_token = result.get("token")
    if not temp_token:
        temp_token = yyds_get_token(address, api_key=key, jwt=token)
    if not temp_token:
        raise Exception("获取 YYDS token 失败")
    print(f"[*] 已创建 YYDS 邮箱: {address}")
    return address, temp_token


def yyds_get_oai_code(
    token,
    address,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    jwt=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = yyds_get_messages(address, token=token, jwt=jwt)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] YYDS 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            to_addrs = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if address.lower() not in to_addrs:
                continue
            try:
                detail = yyds_get_message_detail(msg_id, token=token, jwt=jwt)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] YYDS 获取邮件详情失败: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] YYDS 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] YYDS 从邮件中提取到验证码: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"YYDS 在 {timeout}s 内未收到验证码邮件")


def generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def pick_domain(api_key=None):
    domains = get_domains(api_key=api_key)
    if not domains:
        raise Exception("DuckMail 没有返回任何可用域名")
    private = [d for d in domains if d.get("ownerId")]
    verified_private = [d for d in private if d.get("isVerified")]
    if verified_private:
        return verified_private[0]["domain"]
    public = [d for d in domains if d.get("isVerified")]
    if public:
        return public[0]["domain"]
    raise Exception("DuckMail 无已验证域名可用")


def get_email_provider():
    return config.get("email_provider", "duckmail")


def get_email_and_token(api_key=None):
    provider = get_email_provider()
    if provider == "yyds":
        return yyds_get_email_and_token(api_key=api_key, jwt=get_yyds_jwt())
    if provider == "cloudflare":
        api_base = get_cloudflare_api_base()
        if not api_base:
            raise Exception("Cloudflare API Base 未配置")
        try:
            # cloudflare_temp_email 专用模式
            return cloudflare_create_temp_address(api_base)
        except Exception as primary_exc:
            # 兜底回退到 Mail.tm 风格
            key = api_key or get_cloudflare_api_key()
            domains = cloudflare_get_domains(api_base, api_key=key)
            if not domains:
                raise Exception(f"Cloudflare 创建邮箱失败: {primary_exc}")
            verified = [d for d in domains if d.get("isVerified")]
            target = verified[0] if verified else domains[0]
            domain = target.get("domain")
            if not domain:
                raise Exception("Cloudflare 域名数据格式错误，缺少 domain 字段")
            username = generate_username(10)
            address = f"{username}@{domain}"
            password = secrets.token_urlsafe(12)
            cloudflare_create_account(
                api_base, address, password, api_key=key, expires_in=0
            )
            token = cloudflare_get_token(api_base, address, password, api_key=key)
            if not token:
                raise Exception("获取 Cloudflare 邮箱 token 失败")
            return address, token
    key = api_key or get_duckmail_api_key()
    domain = pick_domain(api_key=key)
    username = generate_username(10)
    address = f"{username}@{domain}"
    password = secrets.token_urlsafe(12)
    create_account(address, password, api_key=key, expires_in=0)
    token = get_token(address, password)
    if not token:
        raise Exception("获取 DuckMail token 失败")
    return address, token


def get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    provider = get_email_provider()
    if provider == "yyds":
        return yyds_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            jwt=get_yyds_jwt(),
            cancel_callback=cancel_callback,
        )
    if provider == "cloudflare":
        return cloudflare_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            resend_callback=resend_callback,
        )
    return duckmail_get_oai_code(
        dev_token,
        email,
        timeout=timeout,
        poll_interval=poll_interval,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
    )


def extract_verification_code(text, subject=""):
    if subject:
        match = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.IGNORECASE)
        if match:
            return match.group(1)
    match = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    patterns = [
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def duckmail_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = get_messages(dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if email.lower() not in recipients:
                continue
            try:
                detail = get_message_detail(dev_token, msg_id)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 获取邮件详情失败: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] 从邮件中提取到验证码: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"在 {timeout}s 内未收到验证码邮件")


def cloudflare_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    api_base = get_cloudflare_api_base()
    if not api_base:
        raise Exception("Cloudflare API Base 未配置")
    deadline = time.time() + timeout
    # 同一封邮件正文可能延迟可读，允许多次重试解析，避免偶发漏码
    seen_attempts = {}
    next_resend_at = time.time() + 35
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if resend_callback and time.time() >= next_resend_at:
            try:
                resend_callback()
                if log_callback:
                    log_callback("[*] 已触发重新发送验证码")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.time() + 35
        try:
            messages = cloudflare_get_messages(api_base, dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Cloudflare 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        if log_callback:
            log_callback(f"[Debug] Cloudflare 本轮邮件数量: {len(messages)}")

        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id:
                continue
            attempt = int(seen_attempts.get(msg_id, 0))
            if attempt >= 5:
                continue
            seen_attempts[msg_id] = attempt + 1
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            msg_addr = str(msg.get("address", "")).lower()
            # 优先匹配目标邮箱；若结构不一致也允许继续解析，避免接口字段漂移导致漏码
            address_matched = True
            if recipients:
                address_matched = email.lower() in recipients
            elif msg_addr:
                address_matched = msg_addr == email.lower()
            if not address_matched and log_callback:
                log_callback(f"[Debug] 跳过疑似非目标邮件 id={msg_id} address={msg_addr} to={recipients}")
                continue
            parts = []
            # 先直接从列表项取内容，避免 detail 接口差异导致漏码
            for field in ("text", "raw", "content", "intro", "body", "snippet"):
                value = msg.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
            html_list = msg.get("html") or []
            if isinstance(html_list, str):
                html_list = [html_list]
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            subject = str(msg.get("subject", "") or "")
            combined = "\n".join(parts)
            # 再尝试 detail 接口补全内容
            try:
                detail = cloudflare_get_message_detail(api_base, dev_token, msg_id)
                for field in ("text", "raw", "content", "intro", "body", "snippet"):
                    value = detail.get(field)
                    if isinstance(value, str) and value.strip():
                        combined += "\n" + value
                html_list2 = detail.get("html") or []
                if isinstance(html_list2, str):
                    html_list2 = [html_list2]
                for h in html_list2:
                    combined += "\n" + re.sub(r"<[^>]+>", " ", h)
                if not subject:
                    subject = str(detail.get("subject", "") or "")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] Cloudflare detail接口失败，改用列表内容解析: {exc}")
            if log_callback:
                log_callback(f"[Debug] Cloudflare 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] Cloudflare 从邮件中提取到验证码: {code}")
                return code
            elif log_callback:
                log_callback(f"[Debug] 邮件已解析但未提取到验证码 id={msg_id} attempt={seen_attempts[msg_id]}")
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"Cloudflare 在 {timeout}s 内未收到验证码邮件")


def generate_random_birthdate():
    import datetime as dt

    today = dt.date.today()
    age = random.randint(20, 40)
    birth_year = today.year - age
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return f"{birth_year}-{birth_month:02d}-{birth_day:02d}T16:00:00.000Z"


def response_preview(res, limit=200):
    """安全预览 HTTP 响应体；gRPC/二进制内容不直接当文本打印。"""
    try:
        headers = {str(k).lower(): str(v).lower() for k, v in dict(getattr(res, "headers", {}) or {}).items()}
        content_type = headers.get("content-type", "")
        raw = getattr(res, "content", None)
        if raw is None:
            try:
                raw = (res.text or "").encode("utf-8", errors="replace")
            except Exception:
                raw = b""
        if not isinstance(raw, (bytes, bytearray)):
            raw = str(raw).encode("utf-8", errors="replace")
        raw = bytes(raw)

        # gRPC / protobuf 常见 content-type 或正文以不可打印字节为主
        is_binaryish = (
            "grpc" in content_type
            or "protobuf" in content_type
            or "octet-stream" in content_type
            or (raw[:1] in (b"\x00", b"\x01") and b"grpc-status" in raw)
        )
        if is_binaryish or (raw and sum(1 for b in raw[:64] if b < 9 or (13 < b < 32)) > 8):
            # 尽量抽出可读的 trailer 片段（如 grpc-status:0）
            readable = re.findall(rb"[ -~]{3,}", raw)
            text = " ".join(part.decode("ascii", errors="ignore") for part in readable)
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                text = f"<binary {len(raw)} bytes>"
            return text[:limit]

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]
    except Exception:
        return ""


def is_cloudflare_block_response(res):
    try:
        headers = {str(k).lower(): str(v).lower() for k, v in dict(res.headers).items()}
        text = str(res.text or "").lower()
        server = headers.get("server", "")
        content_type = headers.get("content-type", "")
        return (
            res.status_code in (403, 429, 503)
            and (
                "cloudflare" in server
                or "cloudflare" in text
                or "cf-error" in text
                or "__cf_chl" in text
                or "text/html" in content_type
            )
        )
    except Exception:
        return False


def set_birth_date(session, log_callback=None):
    url = "https://grok.com/rest/auth/set-birth-date"
    new_headers = {
        "content-type": "application/json",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    payload = {"birthDate": generate_random_birthdate()}
    try:
        res = session.post(url, json=payload, headers=new_headers, timeout=15)
        body_preview = response_preview(res)
        if log_callback:
            log_callback(
                f"[Debug] set_birth_date status: {res.status_code}, body: {body_preview}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        # 生日一旦写过就不能改；算已完成，不能当失败中断后续 NSFW
        text = str(res.text or "")
        if res.status_code in (400, 409, 429) and (
            "birth-date-change-limit-reached" in text
            or "Birth date is locked" in text
            or "already set" in text.lower()
        ):
            return True, "already_set"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_birth_date 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_birth_date HTTP {res.status_code}: {body_preview}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_birth_date] 异常: {e}")
        return False, f"set_birth_date 异常: {e}"


def set_tos_accepted(session, log_callback=None):
    url = "https://accounts.x.ai/auth_mgmt.AuthManagement/SetTosAcceptedVersion"
    payload = struct.pack("B", (2 << 3) | 0) + struct.pack("B", 1)
    data = b"\x00" + struct.pack(">I", len(payload)) + payload
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "x-user-agent": "connect-es/2.1.1",
        "origin": "https://accounts.x.ai",
        "referer": "https://accounts.x.ai/accept-tos",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(f"[Debug] set_tos_accepted status: {res.status_code}")
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_tos_accepted 被 accounts.x.ai 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_tos_accepted HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_tos_accepted] 异常: {e}")
        return False, f"set_tos_accepted 异常: {e}"


def encode_grpc_nsfw_settings():
    field1_content = bytes([0x10, 0x01])
    field1 = bytes([0x0A, len(field1_content)]) + field1_content
    nsfw_string = b"always_show_nsfw_content"
    field2_inner = bytes([0x0A, len(nsfw_string)]) + nsfw_string
    field2 = bytes([0x12, len(field2_inner)]) + field2_inner
    payload = field1 + field2
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def update_nsfw_settings(session, log_callback=None):
    url = "https://grok.com/auth_mgmt.AuthManagement/UpdateUserFeatureControls"
    data = encode_grpc_nsfw_settings()
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] update_nsfw status: {res.status_code}, body: {response_preview(res)}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "update_nsfw_settings 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"update_nsfw_settings HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[update_nsfw] 异常: {e}")
        return False, f"update_nsfw_settings 异常: {e}"


def enable_nsfw_for_token(token, cf_clearance="", user_agent="", log_callback=None):
    proxies = get_proxies()
    # cf_clearance 与签发它的浏览器 UA 严格绑定，优先用注册浏览器的真实 UA
    ua = user_agent or get_user_agent()
    try:
        with requests.Session(impersonate="chrome120", proxies=proxies) as session:
            cookie_parts = [f"sso={token}", f"sso-rw={token}"]
            if cf_clearance:
                cookie_parts.append(f"cf_clearance={cf_clearance}")
            session.headers.update(
                {
                    "user-agent": ua,
                    "cookie": "; ".join(cookie_parts),
                }
            )
            ok, message = set_tos_accepted(session, log_callback)
            if not ok:
                return False, message
            ok, message = set_birth_date(session, log_callback)
            if not ok:
                return False, message
            ok, message = update_nsfw_settings(session, log_callback)
            if not ok:
                return False, message
            return True, "成功开启 NSFW"
    except Exception as e:
        return False, f"异常: {str(e)}"


SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

browser = None
page = None
bitbrowser_profile_opened = False


def setup_light_theme(root):
    try:
        root.option_add("*Background", UI_BG)
        root.option_add("*Foreground", UI_FG)
        root.option_add("*selectBackground", UI_ACTIVE_BG)
        root.option_add("*selectForeground", UI_FG)
        root.option_add("*insertBackground", UI_FG)
        root.option_add("*Entry.Background", UI_ENTRY_BG)
        root.option_add("*Text.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Foreground", UI_FG)
        style = ttk.Style(root)
        available = set(style.theme_names())
        if "clam" in available:
            style.theme_use("clam")
        elif "default" in available:
            style.theme_use("default")
        root.configure(bg=UI_BG)
        style.configure(".", background=UI_BG, foreground=UI_FG, fieldbackground=UI_ENTRY_BG)
        style.configure("TFrame", background=UI_BG)
        style.configure("TLabelframe", background=UI_BG, foreground=UI_FG)
        style.configure("TLabelframe.Label", background=UI_BG, foreground=UI_FG)
        style.configure("TLabel", background=UI_BG, foreground=UI_FG)
        style.configure("TCheckbutton", background=UI_BG, foreground=UI_FG)
        style.configure("TButton", background=UI_BUTTON_BG, foreground=UI_FG)
        style.configure("TEntry", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TCombobox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TSpinbox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
    except Exception:
        pass


def tk_label(parent, text="", **kwargs):
    return tk.Label(parent, text=text, bg=kwargs.pop("bg", UI_BG), fg=kwargs.pop("fg", UI_FG), **kwargs)


def tk_entry(parent, textvariable=None, width=30, **kwargs):
    return tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        disabledbackground="#2f2f2f",
        disabledforeground=UI_MUTED_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
        **kwargs,
    )


def tk_button(parent, text="", command=None, state=tk.NORMAL, **kwargs):
    return tk.Button(
        parent,
        text=text,
        command=command,
        state=state,
        bg=UI_BUTTON_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        disabledforeground="#777777",
        relief=tk.RAISED,
        padx=10,
        pady=3,
        **kwargs,
    )


def tk_checkbutton(parent, text="", variable=None, **kwargs):
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=UI_BG,
        fg=UI_FG,
        activebackground=UI_BG,
        activeforeground=UI_FG,
        selectcolor="#3d7be0",
        **kwargs,
    )


def tk_option_menu(parent, variable, values, width=12):
    menu = tk.OptionMenu(parent, variable, *values)
    menu.configure(
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
    )
    menu["menu"].configure(bg=UI_ENTRY_BG, fg=UI_FG, activebackground=UI_ACTIVE_BG, activeforeground=UI_FG)
    return menu


def _bitbrowser_api_base():
    raw = str(config.get("bitbrowser_api_url", "") or "").strip().rstrip("/")
    if not raw:
        raise ValueError("未配置 bitbrowser_api_url")
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlsplit(raw)
    if os.environ.get("GROK_DOCKER") == "1" and parsed.hostname in {"127.0.0.1", "localhost"}:
        host = "host.docker.internal"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        raw = urlunsplit((parsed.scheme or "http", host, parsed.path, parsed.query, parsed.fragment))
    return raw.rstrip("/")


def _normalize_bitbrowser_debug_address(raw_address):
    value = str(raw_address or "").strip()
    if not value:
        raise ValueError("BitBrowser 未返回 CDP 调试地址")
    parsed = urlsplit(value if "://" in value else f"http://{value}")
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        raise ValueError(f"BitBrowser CDP 地址格式无效: {value}")
    if os.environ.get("GROK_DOCKER") == "1" and host in {"127.0.0.1", "localhost"}:
        try:
            host = socket.gethostbyname("host.docker.internal")
        except OSError:
            host = "host.docker.internal"
    return f"{host}:{port}"


def _local_chrome_debug_address():
    raw = str(config.get("local_chrome_debug_address", "") or "").strip()
    if not raw:
        raise ValueError("未配置 local_chrome_debug_address")
    return _normalize_bitbrowser_debug_address(raw)


def _local_chrome_port():
    raw = str(config.get("local_chrome_debug_address", "") or "").strip() or "127.0.0.1:9222"
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    try:
        return int(parsed.port or 9222)
    except (TypeError, ValueError):
        return 9222


def _local_chrome_agent_base():
    raw = str(config.get("local_chrome_agent_url", "") or "http://127.0.0.1:18083").strip()
    if not raw:
        raw = "http://127.0.0.1:18083"
    parsed = urlsplit(raw if "://" in raw else f"http://{raw}")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 18083
    scheme = parsed.scheme or "http"
    if os.environ.get("GROK_DOCKER") == "1" and host in {"127.0.0.1", "localhost"}:
        try:
            host = socket.gethostbyname("host.docker.internal")
        except OSError:
            host = "host.docker.internal"
    return f"{scheme}://{host}:{port}".rstrip("/")


def _is_local_chrome_auto_launch_enabled():
    configured = config.get("local_chrome_auto_launch", True)
    if isinstance(configured, str):
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return bool(configured)


def _launch_local_chrome_on_host(log_callback=None, force_restart=False):
    """非 Docker：直接执行 start-local-chrome.sh。"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start-local-chrome.sh")
    port = _local_chrome_port()
    if not os.path.isfile(script):
        raise RuntimeError(f"找不到启动脚本: {script}")
    if log_callback:
        action = "强制重建本机无痕 Chrome" if force_restart else "正在本机执行 start-local-chrome.sh"
        log_callback(f"[LocalChrome] {action} (port={port})")
    command = ["/bin/sh", script, str(port)]
    if force_restart:
        command.append("--reset")
    completed = subprocess.run(
        command,
        cwd=os.path.dirname(script),
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or f"start-local-chrome.sh 退出码 {completed.returncode}")
    return completed


def _request_local_chrome_agent_ensure(log_callback=None):
    """Docker：请求宿主机助手拉起 Chrome。"""
    base = _local_chrome_agent_base()
    url = f"{base}/ensure"
    if log_callback:
        log_callback(f"[LocalChrome] 请求宿主机助手自动拉起 Chrome: {url}")
    try:
        response = requests.post(url, json={}, proxies={}, timeout=45)
    except Exception as exc:
        raise RuntimeError(
            f"连不上本机 Chrome 助手 ({base})。"
            "请在 Mac 本机先运行一次并保持运行: ./start-local-chrome-agent.sh"
            f" 详情: {exc}"
        ) from exc
    try:
        data = response.json()
    except Exception:
        data = {}
    if response.status_code >= 400 or not data.get("ok"):
        raise RuntimeError(
            data.get("error")
            or f"本机助手拉起 Chrome 失败 (HTTP {response.status_code})"
        )
    return data


def _request_local_chrome_agent_reset(log_callback=None):
    """Docker：请求宿主机助手关闭旧 Chrome 后新建无痕调试实例。"""
    base = _local_chrome_agent_base()
    url = f"{base}/reset"
    if log_callback:
        log_callback(f"[LocalChrome] 请求宿主机强制重建无痕 Chrome: {url}")
    try:
        response = requests.post(url, json={}, proxies={}, timeout=60)
    except Exception as exc:
        raise RuntimeError(
            f"连不上本机 Chrome 助手 ({base})，无法强制重建无痕 Chrome。"
            "请在 Mac 本机先运行并保持: ./start-local-chrome-agent.sh"
            f" 详情: {exc}"
        ) from exc
    try:
        data = response.json()
    except Exception:
        data = {}
    if response.status_code >= 400 or not data.get("ok"):
        raise RuntimeError(
            data.get("error")
            or f"本机助手强制重建 Chrome 失败 (HTTP {response.status_code})"
        )
    return data


def ensure_local_chrome(log_callback=None):
    """确保本地调试 Chrome 可用；必要时自动拉起。"""
    debug_address = _local_chrome_debug_address()
    try:
        _probe_cdp_endpoint(debug_address, timeout=2)
        created_page = _ensure_cdp_page_target(debug_address, timeout=2)
        if log_callback:
            if created_page:
                log_callback("[LocalChrome] CDP 未发现页面标签，已创建空白标签后再连接")
            log_callback(f"[LocalChrome] 本机 Chrome 已就绪: {debug_address}")
        return debug_address
    except Exception:
        pass

    if not _is_local_chrome_auto_launch_enabled():
        raise RuntimeError(
            f"未检测到本机 Chrome CDP ({debug_address})，且 local_chrome_auto_launch=false。"
            "请手动执行 ./start-local-chrome.sh"
        )

    if log_callback:
        log_callback("[LocalChrome] 未检测到调试 Chrome，尝试自动拉起...")

    if os.environ.get("GROK_DOCKER") == "1":
        result = _request_local_chrome_agent_ensure(log_callback=log_callback)
        if log_callback:
            if result.get("already_running"):
                log_callback("[LocalChrome] 助手报告：Chrome 已在运行")
            else:
                log_callback("[LocalChrome] 助手已拉起本机 Chrome")
    else:
        _launch_local_chrome_on_host(log_callback=log_callback)

    # 给端口一点时间
    deadline = time.time() + 12
    last_error = None
    while time.time() < deadline:
        try:
            _probe_cdp_endpoint(debug_address, timeout=2)
            created_page = _ensure_cdp_page_target(debug_address, timeout=2)
            if log_callback:
                if created_page:
                    log_callback("[LocalChrome] CDP 未发现页面标签，已创建空白标签后再连接")
                log_callback(f"[LocalChrome] 自动拉起成功，已连接: {debug_address}")
            return debug_address
        except Exception as exc:
            last_error = exc
            time.sleep(0.4)
    raise RuntimeError(
        f"自动拉起后仍无法连接本机 Chrome CDP ({debug_address}): {last_error}"
    )


def _probe_cdp_endpoint(debug_address, timeout=3):
    """快速探测 CDP 是否可达，避免 DrissionPage 长时间卡死。"""
    address = str(debug_address or "").strip()
    if not address:
        raise ValueError("CDP 地址为空")
    url = f"http://{address}/json/version"
    try:
        response = requests.get(url, proxies={}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        hint = ""
        if os.environ.get("GROK_DOCKER") == "1":
            hint = (
                " Docker 容器需访问宿主机 Chrome。"
                " 请在 Mac 本机先运行: ./start-local-chrome.sh"
                "（或确保 Chrome 以 --remote-debugging-port=9222 启动）。"
            )
        else:
            hint = " 请先启动本机调试 Chrome: ./start-local-chrome.sh"
        raise RuntimeError(
            f"无法连接本地 Chrome CDP ({address}): {exc}.{hint}"
        ) from exc
    if not data.get("webSocketDebuggerUrl") and not data.get("Browser"):
        raise RuntimeError(f"CDP 响应异常，不是有效的 Chrome 调试端口: {address}")
    return data


def _ensure_cdp_page_target(debug_address, timeout=3):
    """确保 CDP 至少有一个可供 DrissionPage 连接的页面标签。"""
    address = str(debug_address or "").strip()
    if not address:
        raise ValueError("CDP 地址为空")
    base = f"http://{address}"
    try:
        response = requests.get(f"{base}/json", proxies={}, timeout=timeout)
        response.raise_for_status()
        targets = response.json()
    except Exception as exc:
        raise RuntimeError(f"读取 Chrome 页面标签失败 ({address}): {exc}") from exc

    if isinstance(targets, list) and any(
        item.get("type") in {"page", "webview"}
        for item in targets
        if isinstance(item, dict)
    ):
        return False

    try:
        response = requests.put(
            f"{base}/json/new?about:blank",
            proxies={},
            timeout=timeout,
        )
        response.raise_for_status()
        created = response.json()
    except Exception as exc:
        raise RuntimeError(f"创建 Chrome 空白标签失败 ({address}): {exc}") from exc
    if not isinstance(created, dict) or created.get("type") not in {"page", "webview"}:
        raise RuntimeError(f"Chrome 未返回有效页面标签 ({address})")
    return True


def _bitbrowser_request(path, payload):
    response = requests.post(
        f"{_bitbrowser_api_base()}{path}",
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("success"):
        raise RuntimeError(data.get("msg") or f"BitBrowser {path} 请求失败")
    return data.get("data")


def _open_bitbrowser_profile():
    profile_id = str(config.get("bitbrowser_profile_id", "") or "").strip()
    if not profile_id:
        raise ValueError("未配置 bitbrowser_profile_id")
    data = _bitbrowser_request("/browser/open", {"id": profile_id}) or {}
    return _normalize_bitbrowser_debug_address(data.get("http") or data.get("ws"))


def _close_bitbrowser_profile(log_callback=None):
    profile_id = str(config.get("bitbrowser_profile_id", "") or "").strip()
    if not profile_id:
        return
    try:
        _bitbrowser_request("/browser/close", {"id": profile_id})
    except Exception as exc:
        if log_callback:
            log_callback(f"[BitBrowser] 关闭测试环境失败: {exc}")


def _read_browser_public_ip():
    """通过当前浏览器代理读取出口 IP，不使用容器自身网络。"""
    if page is None:
        raise RuntimeError("浏览器页面尚未连接，无法检测公网 IP")
    probe_url = str(
        config.get("browser_ip_check_url", "https://api.ipify.org?format=json")
        or "https://api.ipify.org?format=json"
    ).strip()
    if not probe_url:
        raise RuntimeError("未配置公网 IP 检测地址")
    try:
        page.get(probe_url)
        try:
            page.wait.doc_loaded()
        except Exception:
            pass
        body = page.run_js(
            "return ((document.body && (document.body.innerText || document.body.textContent)) || '').trim();"
        )
    except Exception as exc:
        raise RuntimeError(f"浏览器公网 IP 检测请求失败: {exc}") from exc

    text = str(body or "").strip()
    if not text or "ERR_SOCKS_CONNECTION_FAILED" in text or "This site can’t be reached" in text:
        raise RuntimeError(f"未获取到有效公网 IP，页面内容: {text[:160]}")
    candidate = ""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            candidate = str(data.get("ip") or data.get("query") or "").strip()
        elif isinstance(data, str):
            candidate = data.strip()
    except Exception:
        candidate = text.splitlines()[0].strip()
    if not candidate:
        ipv6_match = re.search(
            r"(?<![0-9a-f:])(?:[0-9a-f]{0,4}:){2,}[0-9a-f:]{0,4}(?![0-9a-f:])",
            text,
            re.I,
        )
        candidate = ipv6_match.group(0) if ipv6_match else ""
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError as exc:
        raise RuntimeError(f"公网 IP 格式无效: {candidate or text[:120]}") from exc
    if address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
        raise RuntimeError(f"检测到的不是公网 IP: {address}")
    return str(address)


def _wait_for_browser_public_ip(log_callback=None):
    try:
        timeout = max(5, int(config.get("browser_ip_check_timeout", 45) or 45))
    except (TypeError, ValueError):
        timeout = 45
    deadline = time.time() + timeout
    last_error = None
    logged_wait = False
    while time.time() < deadline:
        try:
            address = _read_browser_public_ip()
            if log_callback:
                log_callback(f"[BitBrowser] 出口 IP 已就绪: {address}")
            return address
        except Exception as exc:
            last_error = exc
            if log_callback and not logged_wait:
                log_callback(f"[BitBrowser] 等待动态公网 IP: {exc}")
                logged_wait = True
            time.sleep(2)
    raise RuntimeError(f"BitBrowser 在 {timeout} 秒内未获取到有效公网 IP: {last_error}")


def start_browser(log_callback=None):
    global browser, page, bitbrowser_profile_opened

    backend = str(config.get("browser_backend", "chromium") or "chromium").strip().lower()
    last_exc = None
    for attempt in range(1, 5):
        try:
            if backend == "bitbrowser":
                debug_address = _open_bitbrowser_profile()
                bitbrowser_profile_opened = True
                browser = Chromium(debug_address)
                if log_callback:
                    log_callback(f"[BitBrowser] 已连接测试环境，CDP: {debug_address}")
            elif backend == "local_chrome":
                debug_address = ensure_local_chrome(log_callback=log_callback)
                if log_callback:
                    log_callback(f"[LocalChrome] 正在连接本机 Chrome CDP: {debug_address}")
                browser = Chromium(debug_address)
                if log_callback:
                    log_callback(f"[LocalChrome] 已连接本地无痕 Chrome，CDP: {debug_address}")
            else:
                browser = Chromium(create_browser_options())
            tabs = browser.get_tabs()
            page = tabs[-1] if tabs else browser.new_tab()
            clear_browser_session_data(log_callback=log_callback)
            if backend == "bitbrowser" and config.get("bitbrowser_check_public_ip", True):
                _wait_for_browser_public_ip(log_callback=log_callback)
            if log_callback and getattr(browser, "user_data_path", None):
                log_callback(f"[Debug] 当前浏览器资料目录: {browser.user_data_path}")
            if log_callback and attempt > 1:
                log_callback(f"[*] 浏览器第 {attempt} 次启动成功")
            return browser, page
        except Exception as exc:
            last_exc = exc
            if log_callback:
                log_callback(f"[Debug] 浏览器启动失败(第{attempt}/4次): {exc}")
            try:
                if browser is not None:
                    browser.quit(del_data=True)
            except Exception:
                pass
            browser = None
            page = None
            if bitbrowser_profile_opened:
                _close_bitbrowser_profile(log_callback=log_callback)
                bitbrowser_profile_opened = False
            # 本机 Chrome 未启动时快速失败，避免“卡住”数分钟
            if backend == "local_chrome" and "无法连接本地 Chrome CDP" in str(exc):
                break
            time.sleep(min(1.5 * attempt, 4))
    if backend == "local_chrome":
        raise Exception(
            f"浏览器启动失败: {last_exc}。"
            "若在 Docker 中使用本地无痕：请在 Mac 本机保持运行 ./start-local-chrome-agent.sh，"
            "然后重新点开始（会自动拉起 Chrome）。"
        )
    raise Exception(f"浏览器启动失败，已重试4次: {last_exc}")


def stop_browser():
    global browser, page, bitbrowser_profile_opened
    current = browser
    was_bitbrowser = bitbrowser_profile_opened
    try:
        clear_browser_session_data()
        if current is not None:
            current.quit(del_data=True)
    except BaseException:
        # KeyboardInterrupt 继承 BaseException，清理阶段必须吞掉，避免 Ctrl+C 刷 traceback
        pass
    finally:
        browser = None
        page = None
        if was_bitbrowser:
            _close_bitbrowser_profile()
            bitbrowser_profile_opened = False


def restart_browser(log_callback=None):
    stop_browser()
    return start_browser(log_callback=log_callback)


def _is_local_chrome_backend():
    return str(config.get("browser_backend", "chromium") or "chromium").strip().lower() == "local_chrome"


def _force_restart_local_chrome(log_callback=None, reason="next_account"):
    """关闭旧的宿主机调试 Chrome，再连接新建的无痕实例。"""
    reason = str(reason or "next_account").strip().lower()
    if reason == "before_login":
        start_msg = "[LocalChrome] 登录前：强制重建无痕 Chrome，隔离注册残留会话"
        ready_msg = "[LocalChrome] 新无痕 Chrome 已就绪，开始邮箱登录"
    else:
        start_msg = "[LocalChrome] 下一账号：强制重建无痕 Chrome，关闭旧实例后再创建"
        ready_msg = "[LocalChrome] 新无痕 Chrome 已就绪，开始下一账号"
    if log_callback:
        log_callback(start_msg)
    stop_browser()
    if os.environ.get("GROK_DOCKER") == "1":
        _request_local_chrome_agent_reset(log_callback=log_callback)
    else:
        _launch_local_chrome_on_host(log_callback=log_callback, force_restart=True)
    start_browser(log_callback=log_callback)
    if log_callback:
        log_callback(ready_msg)
    return "restarted"


def _browser_reset_strategy():
    configured = str(config.get("browser_reset_strategy", "auto") or "auto").strip().lower()
    return configured if configured in {"auto", "clear", "restart"} else "auto"


def _reset_browser_for_next_account(log_callback=None, reason="next_account"):
    """按策略隔离下一轮账号；auto 优先清理，清理异常时才完整重启。"""
    strategy = _browser_reset_strategy()
    reason = str(reason or "next_account").strip().lower()
    if _consume_force_browser_restart_next():
        if log_callback:
            log_callback("[Browser] 按上一账号异常标记，强制重启浏览器")
        restart_browser(log_callback=log_callback)
        return "restarted"
    # 本机 Chrome 的无痕窗口会在同一进程内保留会话状态；默认 auto 必须新建实例。
    # 显式设为 clear 时才允许沿用进程，仅清理 Cookie/当前站点存储。
    if _is_local_chrome_backend() and strategy != "clear":
        return _force_restart_local_chrome(log_callback=log_callback, reason=reason)
    if strategy == "restart":
        restart_browser(log_callback=log_callback)
        return "restarted"
    if browser is None:
        restart_browser(log_callback=log_callback)
        return "restarted"
    try:
        # Docker Chromium / BitBrowser：仅 clearCookies 清不掉 sso，
        # 连续注册会带着上一账号登录态跳到 grok.com，找不到注册按钮。
        purpose = "login" if reason == "before_login" else "next_account"
        if log_callback and purpose == "next_account":
            log_callback("[Browser] 下一账号：先走退出页轻量隔离（clearCookies 不够）")
        light_ok = False
        try:
            light_ok = bool(_try_light_login_isolation(log_callback=log_callback, purpose=purpose))
        except Exception as iso_exc:
            if log_callback:
                log_callback(f"[Browser] 轻量隔离异常（继续 Cookie 清理）: {iso_exc}")
        clear_browser_session_data(log_callback=log_callback)
        still_dirty = False
        try:
            still_dirty = bool(_has_xai_session_residue())
        except Exception:
            still_dirty = not light_ok
        if still_dirty and strategy == "auto":
            if log_callback:
                log_callback("[Browser] 隔离后仍有会话残留，自动重启浏览器")
            restart_browser(log_callback=log_callback)
            return "restarted"
        if still_dirty and log_callback:
            log_callback("[Browser] 隔离后仍有残留（strategy=clear，不重启）")
        return "cleared" if not still_dirty or strategy == "clear" else "restarted"
    except Exception as exc:
        if strategy != "auto":
            raise
        if log_callback:
            log_callback(f"[Browser] 会话清理失败，自动重启浏览器: {exc}")
        restart_browser(log_callback=log_callback)
        return "restarted"


def _should_restart_after_account(completed_count, target_count, stopped=False):
    """只有还有待处理账号且任务未停止时，才为下一轮重启浏览器。"""
    if stopped:
        return False
    try:
        return int(completed_count) < int(target_count)
    except (TypeError, ValueError):
        return False


def cleanup_runtime_memory(log_callback=None, reason="定期清理"):
    try:
        if log_callback:
            log_callback(f"[*] {reason}: 关闭浏览器并清理内存")
        stop_browser()
        collected = gc.collect()
        if log_callback:
            log_callback(f"[*] Python GC 已回收对象数: {collected}")
    except BaseException:
        # 退出清理中再收到 Ctrl+C 时静默结束，不向外抛
        try:
            stop_browser()
        except BaseException:
            pass


def refresh_active_page():
    global browser, page
    if browser is None:
        restart_browser()
    try:
        tabs = browser.get_tabs()
        if tabs:
            page = tabs[-1]
        else:
            page = browser.new_tab()
    except Exception:
        restart_browser()
    return page




def is_browser_new_tab_per_step_enabled():
    configured = config.get("browser_new_tab_per_step", True)
    if isinstance(configured, str):
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return bool(configured)


def open_step_tab(url, step_label="", log_callback=None):
    """关键步骤在新标签打开，便于从标签数量/标题观察进度。"""
    global browser, page
    if browser is None:
        start_browser(log_callback=log_callback)

    label = str(step_label or "").strip() or "步骤"
    use_new_tab = is_browser_new_tab_per_step_enabled()
    tab_count_before = 0
    try:
        tab_count_before = len(browser.get_tabs() or [])
    except Exception:
        tab_count_before = 0

    opened_new = False
    if use_new_tab:
        try:
            page = browser.new_tab(url)
            opened_new = True
        except Exception:
            try:
                page = browser.new_tab()
                page.get(url)
                opened_new = True
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 新标签打开失败，回退当前标签: {exc}")
                refresh_active_page()
                page.get(url)
    else:
        refresh_active_page()
        page.get(url)

    try:
        page.wait.doc_loaded()
    except Exception:
        pass

    # 改标题，标签栏更容易识别
    try:
        page.run_js(
            """
const prefix = String(arguments[0] || '').trim();
const raw = String(document.title || '').replace(/^\\[[^\\]]+\\]\\s*\\|\\s*/, '');
document.title = prefix ? (`[${prefix}] | ${raw || location.hostname}`) : raw;
return document.title;
            """,
            label,
        )
    except Exception:
        pass

    tab_count_after = tab_count_before
    try:
        tab_count_after = len(browser.get_tabs() or [])
    except Exception:
        pass

    if log_callback:
        mode = "新标签" if opened_new else "当前标签"
        log_callback(
            f"[*] {mode}打开: {label} | 标签数 {tab_count_before}->{tab_count_after} | {url}"
        )
    return page


def extract_cf_clearance_and_ua(log_callback=None):
    """从注册浏览器提取 grok.com 的 cf_clearance 及其绑定的真实 UA。

    注册流程能拿到 sso 说明浏览器已通过 grok.com 的 Cloudflare 盾，
    此刻 cf_clearance 就在浏览器 cookie 里，配合真实 UA 可用于后续 NSFW 请求。

    返回:
      - (cf_clearance str, user_agent str)：任一取不到则为空字符串
    """
    cf_clearance = ""
    user_agent = ""
    try:
        active = refresh_active_page()
        if active is None:
            return "", ""
        cookies = active.cookies(all_domains=True, all_info=True) or []
        for item in cookies:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                value = str(item.get("value", "")).strip()
            else:
                name = str(getattr(item, "name", "")).strip()
                value = str(getattr(item, "value", "")).strip()
            if name == "cf_clearance" and value:
                cf_clearance = value
                break
        try:
            ua = active.run_js("return navigator.userAgent;")
            if ua:
                user_agent = str(ua).strip()
        except Exception:
            pass
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 提取 cf_clearance 失败: {exc}")
    return cf_clearance, user_agent


def click_email_signup_button(timeout=10, log_callback=None, cancel_callback=None):
    global page
    deadline = time.time() + timeout
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if log_callback:
            log_callback("[Debug] 尝试查找“使用邮箱注册”按钮...")

        clicked = page.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('href'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function scoreEntry(node) {
    const compact = nodeText(node).replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱注册')) return 100;
    if (compact.includes('メールで登録') || compact.includes('メールアドレスで登録') || compact.includes('メールでサインアップ')) return 100;
    if (compact.includes('メール') && (compact.includes('登録') || compact.includes('サインアップ'))) return 95;
    if (lower.includes('signupwithemail')) return 95;
    if (lower.includes('continuewithemail')) return 90;
    if (lower.includes('email') && (lower.includes('sign') || lower.includes('continue') || lower.includes('use') || lower.includes('with'))) return 80;
    if (lower === 'email' || lower.includes('邮箱') || compact.includes('メール')) return 70;
    return 0;
}
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map((node) => ({ node, score: scoreEntry(node), text: nodeText(node) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
const target = candidates[0]?.node || null;
if (!target) {
    return false;
}
target.click();
return candidates[0].text || true;
        """)

        if clicked:
            if log_callback:
                detail = f": {clicked}" if isinstance(clicked, str) else ""
                log_callback(f"[*] 已点击「使用邮箱注册」按钮{detail}")
            sleep_with_cancel(2, cancel_callback)
            return True

        if log_callback:
            current_url = page.url if page else "none"
            log_callback(f"[Debug] 当前URL: {current_url}")

        sleep_with_cancel(1, cancel_callback)

    if log_callback:
        page_html = page.html[:500] if page else "no page"
        log_callback(f"[Debug] 页面内容片段: {page_html}")

    raise Exception("未找到「使用邮箱注册」按钮")


def _signup_page_looks_wrong(current_url="", page_html=""):
    """判断是否没真正落到注册入口（已登录 Grok / CF 中间页等）。"""
    url = str(current_url or "").lower()
    html = str(page_html or "").lower()
    if "just a moment" in html or "checking your browser" in html:
        return "cloudflare"
    if "accounts.x.ai" in url and ("sign-up" in url or "signup" in url):
        return ""
    if "grok.com" in url and "accounts.x.ai" not in url:
        return "grok-logged-in"
    if "sign-in" in url and "sign-up" not in url:
        return "sign-in"
    return ""


def open_signup_page(log_callback=None, cancel_callback=None):
    global browser, page
    raise_if_cancelled(cancel_callback)
    if browser is None:
        start_browser(log_callback=log_callback)
        if log_callback:
            log_callback("[*] 浏览器已启动")

    last_error = None
    for attempt in range(1, 4):
        raise_if_cancelled(cancel_callback)
        open_step_tab(SIGNUP_URL, step_label="1-注册页", log_callback=log_callback)
        sleep_with_cancel(2, cancel_callback)
        current_url = ""
        page_html = ""
        try:
            current_url = str(getattr(page, "url", "") or "")
        except Exception:
            current_url = ""
        if log_callback:
            log_callback(f"[*] 当前URL: {current_url}")

        # 注册入口也可能卡 Cloudflare 中间页
        try:
            page_html = str(getattr(page, "html", "") or "")[:2000]
        except Exception:
            page_html = ""
        wrong = _signup_page_looks_wrong(current_url, page_html)

        # 注册首页常预加载 turnstile 脚本，但「Sign up with email」已可点 —— 优先点按钮，别干等 CF
        has_email_signup = False
        try:
            has_email_signup = bool(
                page.run_js(
                    r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
  return [node.innerText, node.textContent, node.getAttribute('aria-label'), node.getAttribute('title')]
    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
return Array.from(document.querySelectorAll('button, a, [role="button"]')).some((node) => {
  if (!isVisible(node) || node.disabled || node.getAttribute('aria-disabled') === 'true') return false;
  const t = nodeText(node).replace(/\s+/g, '').toLowerCase();
  return t.includes('使用邮箱注册') || t.includes('signupwithemail') || t.includes('sign up with email')
    || (t.includes('email') && (t.includes('sign') || t.includes('注册')));
});
                    """
                )
            )
        except Exception:
            has_email_signup = False

        cf_gate = {}
        try:
            cf_gate = _probe_cloudflare_gate() or {}
        except Exception:
            cf_gate = {}
        real_cf_block = (
            wrong == "cloudflare"
            or bool(cf_gate.get("verifyingText"))
            or str(cf_gate.get("reason") or "") in {"interstitial", "widget"}
            or (
                bool(cf_gate.get("present"))
                and not bool(cf_gate.get("ready"))
                and not has_email_signup
            )
        )
        if real_cf_block and not has_email_signup:
            if log_callback:
                reason = cf_gate.get("reason") or wrong or "unknown"
                log_callback(
                    f"[CF] 注册入口：检测到人机验证，自动处理"
                    f"（尝试 {attempt}/3, reason={reason}）"
                )
            _ensure_turnstile_passed(
                log_callback=log_callback,
                cancel_callback=cancel_callback,
                stage="注册入口",
                min_interval=3.0,
                force=True,
            )
            sleep_with_cancel(1.5, cancel_callback)
            try:
                current_url = str(getattr(page, "url", "") or "")
                page_html = str(getattr(page, "html", "") or "")[:2000]
            except Exception:
                pass
            wrong = _signup_page_looks_wrong(current_url, page_html)
        elif has_email_signup and log_callback and attempt == 1 and cf_gate.get("present") and not cf_gate.get("ready"):
            # 调试：避免再被误判卡住时无迹可查
            log_callback(
                f"[CF] 注册入口：页面有预加载 Turnstile，但邮箱注册按钮可用，跳过等待"
                f"（reason={cf_gate.get('reason') or 'n/a'}）"
            )

        if wrong in {"grok-logged-in", "sign-in"}:
            if log_callback:
                log_callback(
                    f"[*] 打开注册页后落到 {wrong}（{current_url[:120]}），"
                    f"执行会话隔离并重开注册页（{attempt}/3）"
                )
            try:
                _try_light_login_isolation(log_callback=log_callback, purpose="next_account")
            except Exception as iso_exc:
                if log_callback:
                    log_callback(f"[Debug] 注册前隔离失败: {iso_exc}")
            clear_browser_session_data(log_callback=log_callback)
            sleep_with_cancel(0.8, cancel_callback)
            continue

        try:
            click_email_signup_button(
                log_callback=log_callback, cancel_callback=cancel_callback
            )
            return
        except Exception as exc:
            last_error = exc
            msg = str(exc)
            if log_callback:
                log_callback(f"[Debug] 注册入口未就绪（尝试 {attempt}/3）: {msg}")
            # 常见于上一账号会话残留：重隔离后再开
            try:
                _try_light_login_isolation(log_callback=log_callback, purpose="next_account")
            except Exception:
                pass
            clear_browser_session_data(log_callback=log_callback)
            if attempt >= 3:
                break
            sleep_with_cancel(1.0, cancel_callback)

    if last_error:
        raise last_error
    raise Exception("未找到「使用邮箱注册」按钮")


def has_profile_form(log_callback=None):
    refresh_active_page()
    try:
        return bool(
            page.run_js(
                """
const givenInput = document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]');
return !!(givenInput && familyInput && passwordInput);
            """
            )
        )
    except Exception:
        return False


def _email_page_advanced_once(email):
    """检测邮箱提交后页面是否真正前进（离开邮箱输入阶段）。

    点击注册按钮只代表触发了点击，不代表表单真的提交成功。
    若 Cloudflare 挑战未过或页面卡住，按钮点击无实际效果，
    邮箱输入框会一直停留，导致后续空等验证码。

    判定“已前进”的依据：
      - 出现验证码输入框（OTP / code 输入），或
      - 原本可见可用的邮箱输入框已消失/不可用

    返回:
      - True：页面已前进，提交生效
      - False：仍停留在邮箱输入页
    """
    try:
        return bool(
            page.run_js(
                """
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.getAttribute('aria-label'),
        node.getAttribute('placeholder'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
        node.getAttribute('data-testid'),
    ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim().toLowerCase();
}
// 1. 出现验证码输入框 => 已前进
const codeInput = Array.from(document.querySelectorAll('input')).find((node) => {
    if (!isVisible(node)) return false;
    const type = (node.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file'].includes(type)) return false;
    const meta = textOf(node);
    const inMode = (node.getAttribute('inputmode') || '').toLowerCase();
    return (
        meta.includes('code') || meta.includes('otp') || meta.includes('verif') ||
        meta.includes('验证') || meta.includes('one-time') || inMode === 'numeric' ||
        node.getAttribute('autocomplete') === 'one-time-code'
    );
});
if (codeInput) return true;
// 2. 邮箱输入框已消失/不可用 => 已前进
const emailInput = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'))
    .find((node) => isVisible(node) && !node.disabled && !node.readOnly);
if (!emailInput) return true;
return false;
                """
            )
        )
    except Exception:
        return False


def _wait_email_page_advanced(email, wait=4.0, cancel_callback=None):
    """点击提交后，在有限窗口内轮询确认页面确实前进。

    给页面/网络一点反应时间：若窗口内检测到已前进则返回 True，
    否则返回 False，由调用方继续重试点击或最终超时换邮箱。
    """
    deadline = time.time() + wait
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if _email_page_advanced_once(email):
            return True
        sleep_with_cancel(0.4, cancel_callback)
    return False


def fill_email_and_submit(timeout=45, log_callback=None, cancel_callback=None):
    raise_if_cancelled(cancel_callback)
    email, dev_token = get_email_and_token()
    if not email or not dev_token:
        raise Exception("获取邮箱失败")
    if log_callback:
        log_callback(f"[*] 已创建邮箱: {email}")
    deadline = time.time() + timeout
    last_diag_time = 0
    last_reclick_time = 0
    last_snapshot = None
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = page.run_js(
            r"""
const email = arguments[0];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function describeInput(node) {
    return [
        `type=${node.getAttribute('type') || ''}`,
        `name=${node.getAttribute('name') || ''}`,
        `id=${node.getAttribute('id') || ''}`,
        `placeholder=${node.getAttribute('placeholder') || ''}`,
        `aria=${node.getAttribute('aria-label') || ''}`,
        `testid=${node.getAttribute('data-testid') || ''}`,
    ].join(' ').replace(/\s+/g, ' ').trim().slice(0, 160);
}
function describeAction(node) {
    return textOf(node).slice(0, 120);
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const visibleInputs = Array.from(document.querySelectorAll('input, textarea'))
    .filter((node) => isVisible(node) && !node.disabled && !node.readOnly)
    .map(describeInput)
    .slice(0, 8);
const visibleActions = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map(describeAction)
    .filter(Boolean)
    .slice(0, 10);
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) {
    return {
        state: 'not-ready',
        url: location.href,
        title: document.title,
        inputs: visibleInputs,
        buttons: visibleActions,
    };
}
input.focus(); input.click();
const valueProto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
const valueSetter = Object.getOwnPropertyDescriptor(valueProto, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new Event('change', { bubbles: true }));
const inputType = (input.getAttribute('type') || '').toLowerCase();
const isValid = inputType !== 'email' || input.checkValidity();
if ((input.value || '').trim() !== email || !isValid) {
    return {
        state: 'fill-failed',
        value: input.value || '',
        valid: isValid,
        input: describeInput(input),
        url: location.href,
    };
}
input.blur();
return {
    state: 'filled',
    input: describeInput(input),
    url: location.href,
};
            """,
            email,
        )
        state = filled.get("state") if isinstance(filled, dict) else filled
        if isinstance(filled, dict):
            last_snapshot = filled
        if state == "not-ready":
            now = time.time()
            if now - last_reclick_time >= 3:
                reclicked = page.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('href'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function scoreEntry(node) {
    const compact = nodeText(node).replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱注册')) return 100;
    if (compact.includes('メールで登録') || compact.includes('メールアドレスで登録') || compact.includes('メールでサインアップ')) return 100;
    if (compact.includes('メール') && (compact.includes('登録') || compact.includes('サインアップ'))) return 95;
    if (lower.includes('signupwithemail')) return 95;
    if (lower.includes('continuewithemail')) return 90;
    if (lower.includes('email') && (lower.includes('sign') || lower.includes('continue') || lower.includes('use') || lower.includes('with'))) return 80;
    if (lower === 'email' || lower.includes('邮箱') || compact.includes('メール')) return 70;
    return 0;
}
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map((node) => ({ node, score: scoreEntry(node), text: nodeText(node) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
if (!candidates.length) return false;
candidates[0].node.click();
return candidates[0].text || true;
                """)
                last_reclick_time = now
                if reclicked and log_callback:
                    detail = f": {reclicked}" if isinstance(reclicked, str) else ""
                    log_callback(f"[Debug] 邮箱输入框未出现，已再次触发邮箱注册入口{detail}")
            if log_callback and now - last_diag_time >= 5:
                last_diag_time = now
                inputs = " | ".join((filled or {}).get("inputs", [])[:6]) if isinstance(filled, dict) else ""
                buttons = " | ".join((filled or {}).get("buttons", [])[:8]) if isinstance(filled, dict) else ""
                url = (filled or {}).get("url", page.url if page else "") if isinstance(filled, dict) else (page.url if page else "")
                log_callback(f"[Debug] 等待邮箱输入框: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if state != "filled":
            if log_callback:
                log_callback(f"[Debug] 邮箱输入框已出现，但写入失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        sleep_with_cancel(0.8, cancel_callback)
        clicked = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input || !(input.value || '').trim()) return false;
const inputType = (input.getAttribute('type') || '').toLowerCase();
if (inputType === 'email' && !input.checkValidity()) return false;
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const submitButton = buttons.find((node) => {
    const text = textOf(node).replace(/\s+/g, '');
    const lower = text.toLowerCase();
    return (
        text === '注册' ||
        text.includes('注册') ||
        text.includes('继续') ||
        text.includes('下一步') ||
        text.includes('确认') ||
        lower.includes('signup') ||
        lower.includes('sign up') ||
        lower.includes('continue') ||
        lower.includes('next') ||
        lower.includes('createaccount') ||
        lower.includes('submit')
    );
});
if (submitButton) {
    submitButton.click();
    return textOf(submitButton) || true;
}
const form = input.closest('form');
if (form) {
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return 'form-submit';
}
input.focus();
input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
return 'enter';
            """
        )
        if clicked:
            # 点击按钮 != 表单真正提交成功：CF 挑战未过或页面卡住时点击无效果，
            # 邮件不会发出。必须确认页面已离开邮箱输入阶段（邮箱框消失或出现验证码框），
            # 否则继续循环重试点击，最终超时抛异常触发换邮箱重试。
            if _wait_email_page_advanced(email, cancel_callback=cancel_callback):
                if log_callback:
                    detail = f" ({clicked})" if isinstance(clicked, str) else ""
                    log_callback(f"[*] 已填写邮箱并提交: {email}{detail}")
                return email, dev_token
            if log_callback and time.time() - last_diag_time >= 5:
                last_diag_time = time.time()
                log_callback(f"[Debug] 已点击注册但页面未前进，重试提交: {email}")
        sleep_with_cancel(0.5, cancel_callback)
    if last_snapshot:
        inputs = " | ".join(last_snapshot.get("inputs", [])[:6])
        buttons = " | ".join(last_snapshot.get("buttons", [])[:8])
        url = last_snapshot.get("url", page.url if page else "")
        raise Exception(
            f"未找到邮箱输入框或注册按钮，最后页面: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}"
        )
    raise Exception("未找到邮箱输入框或注册按钮")


def fill_code_and_submit(email, dev_token, timeout=180, log_callback=None, cancel_callback=None):
    def _resend_code():
        page.run_js(
            r"""
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = nodes.find((node) => {
  const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('重新发送') || t.includes('resend') || t.includes('再次发送');
});
if (target && !target.disabled) { target.click(); return true; }
return false;
            """
        )

    code = get_oai_code(
        dev_token,
        email,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
        resend_callback=_resend_code,
    )
    if not code:
        raise Exception("获取验证码失败")
    clean_code = str(code).replace("-", "").strip()
    deadline = time.time() + timeout

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = page.run_js(
            """
const code = String(arguments[0] || '').trim();
if (!code) return 'empty-code';

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function setInputValue(input, value) {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
}

const aggregate = Array.from(document.querySelectorAll(
  'input[data-input-otp=\"true\"], input[name=\"code\"], input[autocomplete=\"one-time-code\"], input[inputmode=\"numeric\"], input[inputmode=\"text\"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 6) > 1);

if (aggregate) {
    aggregate.focus();
    aggregate.click();
    setInputValue(aggregate, code);
    return String(aggregate.value || '').replace(/\\s+/g, '') ? 'filled-aggregate' : 'aggregate-failed';
}

const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
    if (!isVisible(node) || node.disabled || node.readOnly) return false;
    const maxLength = Number(node.maxLength || 0);
    const ac = String(node.autocomplete || '').toLowerCase();
    return maxLength === 1 || ac === 'one-time-code';
});

if (otpBoxes.length >= code.length) {
    for (let i = 0; i < code.length; i += 1) {
        const ch = code[i] || '';
        const box = otpBoxes[i];
        box.focus();
        box.click();
        setInputValue(box, ch);
        box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ch }));
        box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ch }));
    }
    const merged = otpBoxes.slice(0, code.length).map((x) => String(x.value || '').trim()).join('');
    return merged.length ? 'filled-boxes' : 'boxes-failed';
}

return 'not-ready';
            """,
            clean_code,
        )

        if filled == "not-ready":
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if "failed" in str(filled):
            if log_callback:
                log_callback(f"[Debug] 验证码填写失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue

        clicked = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const buttons = Array.from(document.querySelectorAll('button[type=\"submit\"], button')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});

const btn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return (
        t.includes('确认邮箱') ||
        t.includes('继续') ||
        t.includes('下一步') ||
        t.includes('confirm') ||
        t.includes('continue') ||
        t.includes('next')
    );
});

if (!btn) return 'no-button';
btn.focus();
btn.click();
return 'clicked';
            """
        )

        if clicked == "clicked" or clicked == "no-button":
            if log_callback:
                log_callback(f"[*] 已填写验证码并提交: {code}")
            sleep_with_cancel(1.5, cancel_callback)
            return code

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("验证码已获取，但自动填写/提交失败")


def _get_turnstile_diagnostic(active_page):
    """读取 Turnstile 组件加载状态，不执行绕过或注入验证结果。"""
    if active_page is None:
        return {"error": "page unavailable"}
    try:
        result = active_page.run_js(
            """
const responseInput = document.querySelector('input[name="cf-turnstile-response"]');
const scripts = Array.from(document.scripts || []);
const iframes = Array.from(document.querySelectorAll('iframe'));
const widgets = Array.from(document.querySelectorAll(
  '[class*="turnstile" i], [id*="turnstile" i], [data-sitekey], .cf-turnstile'
));
return {
  readyState: document.readyState,
  hasTurnstileGlobal: !!(window.turnstile && typeof window.turnstile === 'object'),
  hasResponseInput: !!responseInput,
  responseLength: String((responseInput && responseInput.value) || '').trim().length,
  iframeCount: iframes.length,
  turnstileIframeCount: iframes.filter((node) => String(node.src || '').toLowerCase().includes('turnstile')).length,
  turnstileScriptCount: scripts.filter((node) => String(node.src || '').toLowerCase().includes('turnstile')).length,
  widgetCount: widgets.length,
  scriptUrls: scripts.map((node) => String(node.src || '')).filter(Boolean).slice(-8),
  iframeUrls: iframes.map((node) => String(node.src || '')).filter(Boolean).slice(-8),
};
            """
        )
        return result if isinstance(result, dict) else {"raw": str(result)}
    except Exception as exc:
        return {"error": f"{exc.__class__.__name__}: {exc}"}


def _format_turnstile_diagnostic(diagnostic):
    if not diagnostic:
        return "none"
    keys = (
        "readyState", "hasTurnstileGlobal", "hasResponseInput", "responseLength",
        "iframeCount", "turnstileIframeCount", "turnstileScriptCount", "widgetCount",
    )
    return "; ".join(f"{key}={diagnostic.get(key)}" for key in keys if key in diagnostic)



def _probe_cloudflare_gate():
    """读取当前页 Cloudflare/Turnstile 门状态（依赖 DEVICE helper 中的 cloudflareGateStatus）。"""
    if page is None:
        return {"present": False, "ready": True, "tokenLen": 0, "verifyingText": False}
    try:
        status = _run_device_page_js("return cloudflareGateStatus();") or {}
        if isinstance(status, dict):
            return status
    except Exception:
        pass
    # 兜底：不依赖 helper 注入时的轻量探测
    try:
        status = page.run_js(
            r"""
const bodyText = String((document.body && (document.body.innerText || document.body.textContent)) || '').slice(0, 8000);
const titleText = String(document.title || '');
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const turnstileIframe = document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], iframe[src*="cf-chl"]');
const turnstileBox = document.querySelector('div.cf-turnstile, #cf-turnstile, .cf-turnstile, [data-sitekey]');
let widgetVisible = false;
try {
  for (const node of [turnstileIframe, turnstileBox, cfInput].filter(Boolean)) {
    const style = window.getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    if (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0 && rect.width > 0 && rect.height > 0) {
      widgetVisible = true; break;
    }
  }
} catch (e) {}
const token = String((cfInput && cfInput.value) || '').trim();
const tokenOk = token.length >= 80;
const verifyingText = /正在验证|验证您是真人|just a moment|checking your browser|verify you are human|attention required|请完成人机验证|请确认您是真人/i.test(bodyText + ' ' + titleText);
const present = !!(verifyingText || cfInput || widgetVisible || turnstileIframe);
if (!present) return { present: false, ready: true, tokenLen: token.length, verifyingText: false, reason: 'none' };
if (tokenOk) return { present: true, ready: true, tokenLen: token.length, verifyingText: false, reason: 'token-ok' };
if (verifyingText) return { present: true, ready: false, tokenLen: token.length, verifyingText: true, reason: 'interstitial' };
if (widgetVisible || turnstileIframe) return { present: true, ready: false, tokenLen: token.length, verifyingText: false, reason: 'widget' };
return { present: true, ready: false, tokenLen: token.length, verifyingText: false, reason: 'cf-input' };
            """
        )
        return status if isinstance(status, dict) else {"present": False, "ready": True, "tokenLen": 0}
    except Exception:
        return {"present": False, "ready": True, "tokenLen": 0, "verifyingText": False}


def _backfill_turnstile_token(token):
    """把 Turnstile token 写回页面 cf-turnstile-response，并触发 input/change。"""
    if page is None:
        return 0
    token = str(token or "").strip()
    if not token:
        return 0
    try:
        synced = page.run_js(
            """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return 0;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
            """,
            token,
        )
        try:
            return int(synced or 0)
        except (TypeError, ValueError):
            return len(token) if synced else 0
    except Exception:
        return 0


# 登录/Grok 入口共用的 Turnstile 节流状态：stage -> last_attempt_ts
_turnstile_stage_last_attempt = {}


def _login_cf_max_failures():
    try:
        return max(1, int(config.get("cpa_login_cf_max_failures", 2) or 2))
    except (TypeError, ValueError):
        return 2


def _turnstile_widget_absent(diagnostic):
    """脚本/隐藏域在，但可见 widget/iframe 不在 —— Docker 下常见“点不到”。"""
    if not isinstance(diagnostic, dict) or diagnostic.get("error"):
        return False
    try:
        response_len = int(diagnostic.get("responseLength") or 0)
        iframe_n = int(diagnostic.get("turnstileIframeCount") or 0)
        widget_n = int(diagnostic.get("widgetCount") or 0)
    except (TypeError, ValueError):
        return False
    has_global = bool(diagnostic.get("hasTurnstileGlobal"))
    has_input = bool(diagnostic.get("hasResponseInput"))
    return (
        has_global
        and has_input
        and response_len < 80
        and iframe_n <= 0
        and widget_n <= 0
    )


def _note_login_turnstile_outcome(result, state=None, log_callback=None, stage="Grok登录"):
    """累计登录 CF 失败：首次失败刷新登录页，达到阈值则中止本账号。

    state: {fail_streak:int, refreshed:bool}
    """
    state = dict(state or {})
    state.setdefault("fail_streak", 0)
    state.setdefault("refreshed", False)
    result = str(result or "")
    if result in {"passed", "already-ok", "not-present"}:
        state["fail_streak"] = 0
        return state
    if result == "skipped":
        return state

    # failed
    state["fail_streak"] = int(state.get("fail_streak") or 0) + 1
    max_fail = _login_cf_max_failures()
    if not state.get("refreshed"):
        if log_callback:
            log_callback(
                f"[CF] {stage}：Turnstile 失败，刷新账户登录页后重试"
                f"（{state['fail_streak']}/{max_fail}）"
            )
        try:
            open_step_tab(
                "https://accounts.x.ai/sign-in?redirect=grok-com",
                step_label="2-Grok邮箱登录",
                log_callback=log_callback,
            )
        except Exception:
            try:
                if page is not None:
                    page.get("https://accounts.x.ai/sign-in?redirect=grok-com")
            except Exception:
                pass
        state["refreshed"] = True
        try:
            time.sleep(1.5)
        except Exception:
            pass
        return state
    if state["fail_streak"] >= max_fail:
        _request_browser_restart_next("login-cf-failed", log_callback)
        raise RuntimeError(
            f"Grok 登录 Cloudflare/Turnstile 连续失败（{state['fail_streak']} 次），"
            "中止本账号 Device 授权"
        )
    return state


def _ensure_turnstile_passed(
    log_callback=None,
    cancel_callback=None,
    stage="登录页",
    min_interval=6.0,
    force=False,
):
    """登录与 Grok 入口共用：检测到未通过的 Turnstile 时自动点击并回填。

    Returns:
        not-present | already-ok | passed | skipped | failed
    """
    global page, _turnstile_stage_last_attempt
    if page is None:
        return "failed"

    status = _probe_cloudflare_gate()
    present = bool((status or {}).get("present"))
    ready = bool((status or {}).get("ready", True))
    try:
        token_len = int((status or {}).get("tokenLen") or 0)
    except (TypeError, ValueError):
        token_len = 0

    if not present:
        return "not-present"
    if ready and token_len >= 80:
        return "already-ok"
    # present 但 ready（无 token 的 verifying 文案消失）也视为可继续
    if ready and not (status or {}).get("verifyingText"):
        return "already-ok"

    now = time.time()
    stage_key = str(stage or "default")
    last = float(_turnstile_stage_last_attempt.get(stage_key, 0.0) or 0.0)
    try:
        interval = max(0.0, float(min_interval or 0.0))
    except (TypeError, ValueError):
        interval = 6.0
    if not force and interval > 0 and (now - last) < interval:
        return "skipped"
    _turnstile_stage_last_attempt[stage_key] = now

    if log_callback:
        log_callback(f"[CF] {stage}：自动处理 Turnstile（当前 token 长度={token_len}）...")
    try:
        token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
        if token:
            synced = _backfill_turnstile_token(token)
            if log_callback:
                log_callback(
                    f"[CF] {stage}：Turnstile 通过并回填，长度={synced or len(str(token))}"
                )
            return "passed"
    except Exception as exc:
        if log_callback:
            msg = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
            if len(msg) > 160:
                msg = msg[:160] + "..."
            log_callback(f"[Debug] [CF] {stage}：Turnstile 处理失败: {msg}")
        return "failed"
    return "failed"


def getTurnstileToken(log_callback=None, cancel_callback=None):
    global page
    if page is None:
        raise Exception("页面未就绪，无法执行 Turnstile")

    try:
        page.run_js(
            "try { if (window.turnstile && typeof turnstile.reset === 'function') turnstile.reset(); } catch(e) {}"
        )
    except Exception:
        pass

    last_diagnostic_at = 0.0
    last_diagnostic_sig = ""
    max_rounds = 24
    for round_i in range(0, max_rounds):
        raise_if_cancelled(cancel_callback)
        try:
            token = page.run_js(
                """
try {
  const byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput) return byInput;
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    const resp = String(turnstile.getResponse() || '').trim();
    if (resp) return resp;
  }
  // 部分页面 token 写在其它隐藏域
  const alts = document.querySelectorAll('input[name*="turnstile" i], textarea[name*="turnstile" i]');
  for (const node of alts) {
    const v = String((node && node.value) || '').trim();
    if (v.length >= 80) return v;
  }
  return '';
} catch(e) { return ''; }
                """
            )
            token = str(token or "").strip()
            if len(token) >= 80:
                if log_callback:
                    log_callback(f"[*] Turnstile 已通过，token长度={len(token)}")
                return token

            now = time.time()
            # Docker 下常见 widgetCount=0 但仍可出 token：少打重复 Debug
            if log_callback and now - last_diagnostic_at >= 8:
                diagnostic = _get_turnstile_diagnostic(page)
                sig = _format_turnstile_diagnostic(diagnostic)
                if sig != last_diagnostic_sig:
                    log_callback(f"[Debug] Turnstile 组件状态: {sig}")
                    last_diagnostic_sig = sig
                last_diagnostic_at = now

            challenge_input = page.ele("@name=cf-turnstile-response")
            if challenge_input:
                wrapper = challenge_input.parent()
                iframe = None
                try:
                    iframe = wrapper.shadow_root.ele("tag:iframe")
                except Exception:
                    iframe = None
                if iframe:
                    try:
                        iframe.run_js(
                            """
window.dtp = 1;
function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
let sx = getRandomInt(800, 1200);
let sy = getRandomInt(400, 700);
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: sx });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: sy });
                            """
                        )
                    except Exception:
                        pass
                    try:
                        body_sr = iframe.ele("tag:body").shadow_root
                        btn = body_sr.ele("tag:input")
                        if btn:
                            btn.click()
                    except Exception:
                        pass
                else:
                    # 无 shadow iframe 时仍尝试点父容器 / 触发 execute
                    try:
                        page.run_js(
                            """
try {
  const input = document.querySelector('input[name="cf-turnstile-response"]');
  const box = (input && input.closest('.cf-turnstile, [data-sitekey], [class*="turnstile" i]'))
    || document.querySelector('.cf-turnstile, [data-sitekey], [class*="turnstile" i]');
  if (box && typeof box.click === 'function') box.click();
  if (window.turnstile && typeof turnstile.execute === 'function') {
    try { turnstile.execute(); } catch (e) {}
  }
} catch (e) {}
                            """
                        )
                    except Exception:
                        pass
            else:
                # 兜底：尝试触发页面上可见的 Turnstile 容器
                page.run_js(
                    """
const nodes = Array.from(document.querySelectorAll('div,span,iframe,.cf-turnstile,[data-sitekey]')).filter((n) => {
  const txt = (n.className || '') + ' ' + (n.id || '') + ' ' + (n.getAttribute?.('src') || '');
  return String(txt).toLowerCase().includes('turnstile') || n.hasAttribute?.('data-sitekey');
});
if (nodes.length && typeof nodes[0].click === 'function') nodes[0].click();
try {
  if (window.turnstile && typeof turnstile.execute === 'function') turnstile.execute();
} catch (e) {}
                    """
                )
        except Exception as exc:
            msg = str(exc)
            # 主动失败要向上抛，避免被静默吞掉
            if "Turnstile 获取 token 失败" in msg:
                raise
        # 前几轮更密轮询，尽快发现 token 已回填
        sleep_with_cancel(0.5 if round_i < 12 else 1.0, cancel_callback)

    raise Exception("Turnstile 获取 token 失败")


def build_profile():
    given_name_pool = [
        "Neo", "Ethan", "Liam", "Noah", "Lucas", "Mason", "Ryan", "Leo",
        "Owen", "Aiden", "Elio", "Aron", "Ivan", "Nolan", "Evan", "Kai",
        "Caleb", "Adam", "Ezra", "Miles", "Logan", "Carter", "Hunter", "Jason",
        "Brian", "Dylan", "Alex", "Colin", "Blake", "Gavin", "Henry", "Julian",
        "Kevin", "Louis", "Marcus", "Nathan", "Oscar", "Peter", "Quinn", "Robin",
        "Simon", "Tristan", "Victor", "Wesley", "Xavier", "Yuri", "Zane", "Felix",
        "Aaron", "Damian",
    ]
    family_name_pool = [
        "Lin", "Wang", "Zhao", "Liu", "Chen", "Zhang", "Xu", "Sun",
        "Guo", "He", "Yang", "Wu", "Zhou", "Tang", "Qin", "Shi",
        "Fang", "Peng", "Cao", "Deng", "Fan", "Fu", "Gao", "Han",
        "Hu", "Jiang", "Kong", "Lu", "Ma", "Nie", "Pan", "Qiao",
        "Ren", "Shao", "Tian", "Xie", "Yan", "Yao", "Yu", "Zeng",
        "Bai", "Duan", "Hou", "Jin", "Kang", "Luo", "Mao", "Song",
        "Wei", "Xiong",
    ]
    given_name = random.choice(given_name_pool)
    family_name = random.choice(family_name_pool)
    # x.ai 密码规则：大小写+数字+特殊字符；避免 # / + 等在个别场景下不稳定的字符
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    body = "".join(secrets.choice(alphabet) for _ in range(10))
    password = f"Na7!{body}"
    return given_name, family_name, password


def fill_profile_and_submit(timeout=120, log_callback=None, cancel_callback=None):
    given_name, family_name, password = build_profile()
    deadline = time.time() + timeout
    form_filled_once = False
    wait_cf_since = None
    last_cf_retry_at = 0.0
    last_cf_log_at = 0.0

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if not form_filled_once:
            filled = page.run_js(
                """
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => {
        return isVisible(node) && !node.disabled && !node.readOnly;
    }) || null;
}

function setInputValue(input, value) {
    if (!input) return false;
    input.focus();
    input.click();
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.blur();
    return String(input.value || '').trim() === String(value || '').trim();
}

const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"], input[aria-label*="名"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"], input[aria-label*="姓"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');

if (!givenInput || !familyInput || !passwordInput) return 'not-ready';

const ok1 = setInputValue(givenInput, givenName);
const ok2 = setInputValue(familyInput, familyName);
const ok3 = setInputValue(passwordInput, password);

if (!ok1 || !ok2 || !ok3) return 'fill-failed';

const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});

// 必须等待 Cloudflare 校验通过后再提交
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], div.cf-turnstile, #cf-turnstile, .cf-turnstile, [data-sitekey]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solvedByToken = token.length >= 80;
    if (!solvedByToken) return 'wait-cloudflare:' + token.length;
}

if (submitBtn) {
    return 'ready-to-submit';
}
return 'filled-no-submit';
            """,
                given_name,
                family_name,
                password,
            )

            if isinstance(filled, str) and filled.startswith("wait-cloudflare"):
                form_filled_once = True
                token_len = filled.split(":", 1)[1] if ":" in filled else "0"
                now = time.time()
                if log_callback and (now - last_cf_log_at >= 3.0):
                    last_cf_log_at = now
                    log_callback(f"[*] 资料已填写，等待 Cloudflare 人机验证通过... 当前token长度={token_len}")
                if token_len == "0":
                    pause_seconds = random.uniform(1, 3)
                    if log_callback and (time.time() - last_cf_log_at >= 3.0):
                        last_cf_log_at = time.time()
                        log_callback(f"[*] Cloudflare token 为空，暂停 {pause_seconds:.1f}s 后继续检测")
                    sleep_with_cancel(pause_seconds, cancel_callback)
                now = time.time()
                if wait_cf_since is None:
                    wait_cf_since = now
                # 卡住后自动二次复用 Turnstile 组件
                if now - wait_cf_since >= 12 and now - last_cf_retry_at >= 8:
                    if log_callback:
                        log_callback("[*] Cloudflare 验证卡住，开始二次复用 Turnstile...")
                    try:
                        token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                        if token:
                            synced = page.run_js(
                                """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                                """,
                                token,
                            )
                            if log_callback:
                                log_callback(f"[*] Turnstile 二次复用完成，回填长度={synced}")
                    except Exception as cf_exc:
                        if log_callback:
                            log_callback(f"[Debug] Turnstile 二次复用失败: {cf_exc}")
                    last_cf_retry_at = now
                sleep_with_cancel(0.8, cancel_callback)
                continue

            if filled in ("ready-to-submit", "filled-no-submit"):
                form_filled_once = True
            elif filled == "fill-failed" and log_callback:
                log_callback("[Debug] 资料输入失败，重试中...")
                sleep_with_cancel(0.5, cancel_callback)
                continue
            elif filled == "not-ready":
                sleep_with_cancel(0.5, cancel_callback)
                continue

        submit_state = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], div.cf-turnstile, #cf-turnstile, .cf-turnstile, [data-sitekey]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solvedByToken = token.length >= 80;
    if (!solvedByToken) return 'wait-cloudflare:' + token.length;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'no-submit-button:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'submitted';
            """
        )

        if isinstance(submit_state, str) and submit_state.startswith("wait-cloudflare"):
            token_len = submit_state.split(":", 1)[1] if ":" in submit_state else "0"
            now = time.time()
            if log_callback and (now - last_cf_log_at >= 3.0):
                last_cf_log_at = now
                log_callback(f"[*] 等待 Cloudflare 人机验证通过后再提交... 当前token长度={token_len}")
            now = time.time()
            if wait_cf_since is None:
                wait_cf_since = now
            if now - wait_cf_since >= 12 and now - last_cf_retry_at >= 8:
                if log_callback:
                    log_callback("[*] 提交前仍卡住，自动再次复用 Turnstile...")
                try:
                    token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                    if token:
                        synced = page.run_js(
                            """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                            """,
                            token,
                        )
                        if log_callback:
                            log_callback(f"[*] Turnstile 二次复用完成，回填长度={synced}")
                except Exception as cf_exc:
                    if log_callback:
                        log_callback(f"[Debug] Turnstile 二次复用失败: {cf_exc}")
                last_cf_retry_at = now
            sleep_with_cancel(0.8, cancel_callback)
            continue

        if submit_state == "submitted":
            if log_callback:
                log_callback(f"[*] 已填写注册资料并提交: {given_name} {family_name}")
            # 提交后确认是否离开资料表单。
            # 注意：页面跳转时 DrissionPage 常抛「页面被刷新」，这通常表示注册提交成功后的导航，
            # 不能当成失败中断，否则会跳过后续 Grok 登录 / Device 授权。
            confirm_deadline = time.time() + 45
            left_form = False
            saw_navigation = False
            while time.time() < confirm_deadline:
                raise_if_cancelled(cancel_callback)
                try:
                    # 必须返回 JSON 字符串：DrissionPage 对 JS object 有时会「js结果解析错误」
                    status_raw = page.run_js(
                        r"""
const url = location.href || '';
const body = String((document.body && (document.body.innerText || document.body.textContent)) || '').slice(0, 6000);
const passwordInput = document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');
const givenInput = document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const stillOnProfile = !!(passwordInput && givenInput);
const hasError = /错误的邮箱|密码错误|无法创建|创建失败|invalid|unable|could not|try again|请重试/i.test(body)
  && stillOnProfile;
const left = !stillOnProfile
  || /grok\.com|sign-in|oauth2|welcome|成功|completed|account created/i.test(url + body);
return JSON.stringify({
  status: hasError ? 'error' : (left ? 'left' : 'stay'),
  url: url,
  stillOnProfile: !!stillOnProfile
});
                        """
                    )
                except Exception as nav_exc:
                    msg = str(nav_exc or "")
                    # 页面刷新/断开：提交后导航成功信号，首刷即可确认
                    if (
                        "页面被刷新" in msg
                        or "页面已刷新" in msg
                        or "PageDisconnected" in msg
                        or "disconnected" in msg.lower()
                        or "js结果解析错误" in msg
                        or isinstance(nav_exc, PageDisconnectedError)
                    ):
                        # js结果解析错误常发生在页面跳转瞬间对象未序列化成功，按导航成功处理
                        saw_navigation = True
                        left_form = True
                        if log_callback:
                            if "js结果解析错误" in msg:
                                log_callback(
                                    "[*] 注册提交后页面状态探测异常（多半是跳转中），确认成功并继续登录授权"
                                )
                            else:
                                log_callback("[*] 注册提交后页面已跳转/刷新，确认成功并继续登录授权")
                        try:
                            refresh_active_page()
                        except Exception:
                            pass
                        sleep_with_cancel(0.6, cancel_callback)
                        break
                    raise

                status = {}
                if isinstance(status_raw, dict):
                    status = status_raw
                elif isinstance(status_raw, str) and status_raw.strip():
                    try:
                        parsed = json.loads(status_raw)
                        if isinstance(parsed, dict):
                            status = parsed
                        else:
                            status = {"status": str(parsed)}
                    except Exception:
                        # 兼容旧返回：纯字符串 left/stay/error
                        status = {"status": status_raw.strip()}
                else:
                    status = {"status": str(status_raw or "")}

                status_name = str(status.get("status") or "")

                if status_name == "left":
                    left_form = True
                    break
                if status_name == "error":
                    if log_callback:
                        log_callback("[!] 注册资料提交后页面报错，注册可能未成功")
                    raise Exception("注册资料提交后页面显示错误，账号可能未创建成功")
                sleep_with_cancel(0.8, cancel_callback)

            if not left_form and saw_navigation:
                # 已发生导航但短暂检测不到表单状态时，按成功继续登录授权
                left_form = True
                if log_callback:
                    log_callback("[*] 注册提交后已发生页面跳转，按成功进入后续登录授权")

            if not left_form:
                if log_callback:
                    log_callback("[!] 注册资料提交后仍停留在资料页，可能人机验证未通过或提交无效")
                raise Exception("注册资料提交后未离开资料页，账号可能未创建成功")
            if log_callback:
                log_callback("[*] 注册资料提交已确认，继续登录授权")
            return {"given_name": given_name, "family_name": family_name, "password": password}
        wait_cf_since = None
        if isinstance(submit_state, str) and submit_state.startswith("no-submit-button") and log_callback:
            visible_buttons = submit_state.split(":", 1)[1] if ":" in submit_state else ""
            suffix = f" 可见按钮: {visible_buttons}" if visible_buttons else ""
            log_callback(f"[Debug] 未找到提交按钮，继续等待页面稳定...{suffix}")

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("最终注册页资料填写失败")


def _describe_sso_wait_state(active_page, cookie_names, last_error=""):
    """返回 SSO 等待阶段的可读状态，避免页面异常被静默吞掉。"""
    try:
        url = str(getattr(active_page, "url", "") or "")
    except Exception:
        url = ""
    try:
        title = str(getattr(active_page, "title", "") or "")
    except Exception:
        title = ""
    names = ",".join(sorted(str(name) for name in (cookie_names or set()))) or "none"
    detail = f"url={url or 'unknown'}; title={title or 'unknown'}; cookies={names}"
    if last_error:
        detail += f"; last_error={str(last_error)[:240]}"
    return detail


def wait_for_sso_cookie(timeout=None, log_callback=None, cancel_callback=None):
    if timeout is None:
        try:
            timeout = max(30, int(config.get("sso_cookie_timeout", 300) or 300))
        except (TypeError, ValueError):
            timeout = 300
    deadline = time.time() + timeout
    last_seen_names = set()
    last_submit_retry = 0.0
    last_cf_retry_at = 0.0
    final_no_submit_state = ""
    final_no_submit_since = None
    final_no_submit_timeout = 25
    last_error = ""
    last_diagnostic_at = 0.0

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            refresh_active_page()
            if page is None:
                sleep_with_cancel(1, cancel_callback)
                continue

            # 仍停留在“完成注册”页时，若 Cloudflare 已通过，周期性重试点击提交
            now = time.time()
            if now - last_submit_retry >= 2.5:
                retried = page.run_js(
                    r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const titleHit = !!Array.from(document.querySelectorAll('h1,h2,div,span')).find((el) => {
    const t = (el.textContent || '').replace(/\s+/g, '');
    const lower = t.toLowerCase();
    return t.includes('完成注册') || lower.includes('completeyoursignup') || lower.includes('completesignup');
});
if (!titleHit) return 'not-final-page';

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"], div.cf-turnstile, #cf-turnstile, .cf-turnstile, [data-sitekey]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solved = token.length >= 80;
    if (!solved) return 'final-page-wait-cf:' + token.length;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'final-page-no-submit:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'final-page-clicked-submit';
                    """
                )
                last_submit_retry = now
                if log_callback and (retried == "final-page-clicked-submit" or (isinstance(retried, str) and retried.startswith("final-page-no-submit"))):
                    log_callback(f"[Debug] 最终页状态: {retried}")
                if isinstance(retried, str) and retried.startswith("final-page-no-submit"):
                    if retried != final_no_submit_state:
                        final_no_submit_state = retried
                        final_no_submit_since = now
                    elif final_no_submit_since and now - final_no_submit_since >= final_no_submit_timeout:
                        raise AccountRetryNeeded(
                            f"最终注册页状态 {final_no_submit_timeout}s 未变化且未找到提交按钮，重试当前账号: {retried}"
                        )
                else:
                    final_no_submit_state = ""
                    final_no_submit_since = None
                if log_callback and isinstance(retried, str) and retried.startswith("final-page-wait-cf"):
                    token_len = retried.split(":", 1)[1] if ":" in retried else "0"
                    log_callback(f"[Debug] 最终页状态: final-page-wait-cf, token长度={token_len}")
                    if now - last_cf_retry_at >= 10:
                        if log_callback:
                            log_callback("[*] 最终页 Cloudflare 卡住，自动二次复用 Turnstile...")
                        try:
                            token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                            if token:
                                synced = page.run_js(
                                    """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                                    """,
                                    token,
                                )
                                if log_callback:
                                    log_callback(f"[*] 最终页 Turnstile 二次复用完成，回填长度={synced}")
                        except Exception as cf_exc:
                            if log_callback:
                                log_callback(f"[Debug] 最终页 Turnstile 二次复用失败: {cf_exc}")
                        last_cf_retry_at = now

            # 本地无痕会保留注册页、Grok 页和授权页多个标签；SSO 可能写在注册标签，
            # 当前活动标签不一定是它，所以扫描全部标签。
            sso = _find_sso_cookie_in_browser_tabs()
            if sso:
                if log_callback:
                    log_callback("[*] 已获取到 sso cookie")
                return sso

            cookies = page.cookies(all_domains=True, all_info=True) or []
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()

                if name:
                    last_seen_names.add(name)

                if name == "sso" and value:
                    if log_callback:
                        log_callback("[*] 已获取到 sso cookie")
                    return value
        except PageDisconnectedError:
            last_error = "PageDisconnectedError"
            refresh_active_page()
        except AccountRetryNeeded:
            raise
        except RegistrationCancelled:
            raise
        except Exception as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"

        now = time.time()
        if log_callback and now - last_diagnostic_at >= 5:
            log_callback(
                f"[Debug] 等待 sso 状态: "
                f"{_describe_sso_wait_state(page, last_seen_names, last_error)}"
            )
            last_diagnostic_at = now

        sleep_with_cancel(1, cancel_callback)

    raise Exception(
        "等待超时：未获取到 sso cookie。"
        f" {_describe_sso_wait_state(page, last_seen_names, last_error)}"
    )


class GrokRegisterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Grok 注册机")
        self.root.geometry("1120x900")
        self.root.minsize(960, 700)
        self.is_running = False
        self.batch_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        self.stop_requested = False
        self.ui_queue = queue.Queue()
        self.accounts_output_file = ""
        self.setup_ui()

    def setup_ui(self):
        load_config()
        main_frame = tk.Frame(self.root, bg=UI_BG, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(3, weight=1)

        config_frame = tk.LabelFrame(
            main_frame,
            text="配置",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=10,
            pady=10,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        config_frame.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        config_frame.grid_columnconfigure(1, weight=1, minsize=260)
        config_frame.grid_columnconfigure(3, weight=1, minsize=260)

        def add_label(row, column, text):
            tk_label(config_frame, text=text, bg=UI_PANEL_BG).grid(
                row=row,
                column=column,
                sticky=tk.W,
                padx=(0, 6),
                pady=3,
            )

        def add_field(widget, row, column, columnspan=1, sticky=tk.EW):
            widget.grid(
                row=row,
                column=column,
                columnspan=columnspan,
                sticky=sticky,
                padx=(0, 14),
                pady=3,
            )

        add_label(0, 0, "邮箱服务商:")
        self.email_provider_var = tk.StringVar(value=config.get("email_provider", "duckmail"))
        self.email_provider_combo = tk_option_menu(config_frame, self.email_provider_var, ["duckmail", "yyds", "cloudflare"], width=12)
        add_field(self.email_provider_combo, 0, 1, sticky=tk.W)

        add_label(0, 2, "注册数量:")
        self.count_var = tk.StringVar(value=str(config.get("register_count", 1)))
        self.count_spinbox = tk.Spinbox(
            config_frame,
            from_=1,
            to=2500,
            width=8,
            textvariable=self.count_var,
            bg=UI_ENTRY_BG,
            fg=UI_FG,
            insertbackground=UI_FG,
            buttonbackground=UI_BUTTON_BG,
            disabledbackground="#2f2f2f",
            disabledforeground=UI_MUTED_FG,
            relief=tk.SOLID,
        )
        add_field(self.count_spinbox, 0, 3, sticky=tk.W)

        add_label(1, 0, "注册选项:")
        self.nsfw_var = tk.BooleanVar(value=config.get("enable_nsfw", True))
        self.nsfw_check = tk_checkbutton(config_frame, text="注册后开启 NSFW", variable=self.nsfw_var)
        add_field(self.nsfw_check, 1, 1, sticky=tk.W)

        add_label(1, 2, "代理（可选）:")
        self.proxy_var = tk.StringVar(value=config.get("proxy", ""))
        self.proxy_entry = tk_entry(config_frame, textvariable=self.proxy_var, width=34)
        add_field(self.proxy_entry, 1, 3)

        add_label(2, 0, "DuckMail API Key:")
        self.api_key_var = tk.StringVar(value=config.get("duckmail_api_key", ""))
        self.api_key_entry = tk_entry(config_frame, textvariable=self.api_key_var, width=34)
        add_field(self.api_key_entry, 2, 1)

        add_label(2, 2, "Cloudflare 鉴权模式:")
        self.cloudflare_auth_mode_var = tk.StringVar(value=config.get("cloudflare_auth_mode", "none"))
        self.cloudflare_auth_mode_combo = tk_option_menu(
            config_frame, self.cloudflare_auth_mode_var, ["query-key", "bearer", "x-api-key", "x-admin-auth", "none"], width=12
        )
        add_field(self.cloudflare_auth_mode_combo, 2, 3, sticky=tk.W)

        add_label(3, 0, "Cloudflare API Base:")
        self.cloudflare_api_base_var = tk.StringVar(value=config.get("cloudflare_api_base", ""))
        self.cloudflare_api_base_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_base_var, width=72)
        add_field(self.cloudflare_api_base_entry, 3, 1, columnspan=3)

        add_label(4, 0, "Cloudflare API Key:")
        self.cloudflare_api_key_var = tk.StringVar(value=config.get("cloudflare_api_key", ""))
        self.cloudflare_api_key_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_key_var, width=34)
        add_field(self.cloudflare_api_key_entry, 4, 1)

        add_label(4, 2, "CF 路径:")
        self.cloudflare_paths_var = tk.StringVar(
            value=",".join(
                [
                    config.get("cloudflare_path_domains", "/api/domains"),
                    config.get("cloudflare_path_accounts", "/api/new_address"),
                    config.get("cloudflare_path_token", "/api/token"),
                    config.get("cloudflare_path_messages", "/api/mails"),
                ]
            )
        )
        self.cloudflare_paths_entry = tk_entry(config_frame, textvariable=self.cloudflare_paths_var, width=34)
        add_field(self.cloudflare_paths_entry, 4, 3)

        add_label(5, 0, "Cloudflare 收信域名:")
        self.default_domains_var = tk.StringVar(value=str(config.get("defaultDomains", "")))
        self.default_domains_entry = tk_entry(config_frame, textvariable=self.default_domains_var, width=34)
        add_field(self.default_domains_entry, 5, 1)

        add_label(5, 2, "Cloudflare 全局密码:")
        self.cloudflare_custom_auth_var = tk.StringVar(value=str(config.get("cloudflare_custom_auth", "")))
        self.cloudflare_custom_auth_entry = tk_entry(config_frame, textvariable=self.cloudflare_custom_auth_var, width=34)
        add_field(self.cloudflare_custom_auth_entry, 5, 3)

        add_label(6, 0, "CPA 直出(SSO→auth):")
        self.cpa_auto_add_var = tk.BooleanVar(value=bool(config.get("cpa_auto_add", False)))
        self.cpa_auto_add_check = tk_checkbutton(config_frame, variable=self.cpa_auto_add_var)
        add_field(self.cpa_auto_add_check, 6, 1, sticky=tk.W)

        add_label(7, 0, "CPA auth 目录:")
        self.cpa_auth_dir_var = tk.StringVar(value=str(config.get("cpa_auth_dir", "")))
        self.cpa_auth_dir_entry = tk_entry(config_frame, textvariable=self.cpa_auth_dir_var, width=72)
        add_field(self.cpa_auth_dir_entry, 7, 1, columnspan=3)

        add_label(8, 0, "CPA 远程地址:")
        self.cpa_remote_url_var = tk.StringVar(value=str(config.get("cpa_remote_url", "")))
        self.cpa_remote_url_entry = tk_entry(config_frame, textvariable=self.cpa_remote_url_var, width=40)
        add_field(self.cpa_remote_url_entry, 8, 1)

        add_label(8, 2, "CPA 管理密钥:")
        self.cpa_management_key_var = tk.StringVar(value=str(config.get("cpa_management_key", "")))
        self.cpa_management_key_entry = tk_entry(config_frame, textvariable=self.cpa_management_key_var, width=28)
        add_field(self.cpa_management_key_entry, 8, 3)

        btn_frame = tk.Frame(main_frame, bg=UI_BG)
        btn_frame.grid(row=1, column=0, sticky=tk.EW, pady=(0, 6))
        self.start_btn = tk_button(btn_frame, text="开始注册", command=self.start_registration)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = tk_button(btn_frame, text="停止", command=self.stop_registration, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.clear_btn = tk_button(btn_frame, text="清空日志", command=self.clear_log)
        self.clear_btn.pack(side=tk.LEFT, padx=5)

        status_frame = tk.Frame(main_frame, bg=UI_BG)
        status_frame.grid(row=2, column=0, sticky=tk.EW, pady=(0, 6))
        self.status_var = tk.StringVar(value="就绪")
        tk_label(status_frame, text="状态: ").pack(side=tk.LEFT)
        self.status_label = tk.Label(status_frame, textvariable=self.status_var, bg=UI_BG, fg="green")
        self.status_label.pack(side=tk.LEFT)
        self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0")
        tk.Label(status_frame, textvariable=self.stats_var, bg=UI_BG, fg=UI_FG).pack(side=tk.RIGHT)
        log_frame = tk.LabelFrame(
            main_frame,
            text="日志",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=5,
            pady=5,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        log_frame.grid(row=3, column=0, sticky=tk.NSEW)
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=18,
            width=60,
            bg="#111111",
            fg="#f5f5f5",
            insertbackground="#f5f5f5",
            selectbackground="#345a8a",
            selectforeground="#ffffff",
            relief=tk.SOLID,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#555555",
        )
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        self.log("[*] GUI 已就绪，配置已加载")
        self.log(f"[*] 当前邮箱服务商: {self.email_provider_var.get()} | 注册数量: {self.count_var.get()}")

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        self.log_text.insert(tk.END, f"{line}\n")
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def update_stats(self):
        self.stats_var.set(f"成功: {self.success_count} | 失败: {self.fail_count}")

    def _set_running_ui(self, running):
        self.is_running = running
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.status_var.set("运行中..." if running else "就绪")
        self.status_label.config(foreground="blue" if running else "green")

    def should_stop(self):
        return self.stop_requested or not self.is_running

    def start_registration(self):
        if self.is_running:
            self.log("[!] 当前已有任务在运行")
            return

        config["email_provider"] = self.email_provider_var.get().strip() or "duckmail"
        config["enable_nsfw"] = bool(self.nsfw_var.get())
        config["proxy"] = self.proxy_var.get().strip()
        config["duckmail_api_key"] = self.api_key_var.get().strip()
        config["cloudflare_api_base"] = self.cloudflare_api_base_var.get().strip()
        config["cloudflare_api_key"] = self.cloudflare_api_key_var.get().strip()
        config["cloudflare_auth_mode"] = self.cloudflare_auth_mode_var.get().strip() or "none"
        config["defaultDomains"] = self.default_domains_var.get().strip()
        config["cloudflare_custom_auth"] = self.cloudflare_custom_auth_var.get().strip()
        config["cpa_auto_add"] = bool(self.cpa_auto_add_var.get())
        config["cpa_auth_dir"] = self.cpa_auth_dir_var.get().strip()
        config["cpa_remote_url"] = self.cpa_remote_url_var.get().strip()
        config["cpa_management_key"] = self.cpa_management_key_var.get().strip()
        raw_paths = [x.strip() for x in self.cloudflare_paths_var.get().split(",") if x.strip()]
        if len(raw_paths) >= 4:
            config["cloudflare_path_domains"] = raw_paths[0] if raw_paths[0].startswith("/") else ("/" + raw_paths[0])
            config["cloudflare_path_accounts"] = raw_paths[1] if raw_paths[1].startswith("/") else ("/" + raw_paths[1])
            config["cloudflare_path_token"] = raw_paths[2] if raw_paths[2].startswith("/") else ("/" + raw_paths[2])
            config["cloudflare_path_messages"] = raw_paths[3] if raw_paths[3].startswith("/") else ("/" + raw_paths[3])
        save_config()
        if config["email_provider"] == "cloudflare" and not config["cloudflare_api_base"]:
            self.log("[!] Cloudflare 模式需要先填写 Cloudflare API Base")
            return
        try:
            count = int(self.count_var.get())
        except Exception:
            self.log("[!] 注册数量无效")
            return
        config["register_count"] = count
        save_config()
        self.stop_requested = False
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.accounts_output_file = os.path.join(
            os.path.dirname(__file__), f"accounts_{now}.txt"
        )
        self.update_stats()
        self._set_running_ui(True)
        self.log(f"[*] 配置已保存，开始执行。目标数量: {count}")
        self.log(f"[*] 成功账号将实时保存到: {self.accounts_output_file}")
        threading.Thread(
            target=self.run_registration,
            args=(count,),
            daemon=True,
        ).start()

    def stop_registration(self):
        self.stop_requested = True
        self.log("[!] 用户停止注册")

    def run_registration(self, count):
        try:
            start_browser(log_callback=self.log)
            self.log("[*] 浏览器已启动")
            i = 0
            retry_count_for_slot = 0
            max_slot_retry = 3
            while i < count:
                if self.should_stop():
                    break
                self.log(f"--- 开始第 {i + 1}/{count} 个账号 ---")
                try:
                    email = ""
                    dev_token = ""
                    code = ""
                    mail_ok = False
                    max_mail_retry = 3
                    for mail_try in range(1, max_mail_retry + 1):
                        self.log(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
                        open_signup_page(
                            log_callback=self.log, cancel_callback=self.should_stop
                        )
                        self.log("[*] 2. 创建邮箱并提交")
                        email, dev_token = fill_email_and_submit(
                            log_callback=self.log, cancel_callback=self.should_stop
                        )
                        self.log(f"[*] 邮箱: {email}")
                        self.log(f"[Debug] 邮箱credential(jwt): {dev_token}")
                        try:
                            with open(
                                os.path.join(os.path.dirname(__file__), "mail_credentials.txt"),
                                "a",
                                encoding="utf-8",
                            ) as f:
                                f.write(f"{email}\t{dev_token}\n")
                        except Exception:
                            pass
                        self.log("[*] 3. 拉取验证码")
                        try:
                            code = fill_code_and_submit(
                                email,
                                dev_token,
                                log_callback=self.log,
                                cancel_callback=self.should_stop,
                            )
                            mail_ok = True
                            break
                        except Exception as mail_exc:
                            msg = str(mail_exc)
                            if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                                self.log(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                                restart_browser(log_callback=self.log)
                                sleep_with_cancel(1, self.should_stop)
                                continue
                            raise

                    if not mail_ok:
                        raise Exception("验证码阶段失败，已达到最大重试次数")
                    self.log(f"[*] 验证码: {code}")
                    self.log("[*] 4. 填写资料")
                    profile = fill_profile_and_submit(
                        log_callback=self.log, cancel_callback=self.should_stop
                    )
                    self.log(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
                    self.log("[*] 5. 准备 CPA 授权")
                    if _can_start_device_flow_without_sso(email, profile.get("password", "")):
                        sso = ""
                        self.log("[*] 当前为 Device Flow，跳过 SSO Cookie 等待，直接进入 Grok 登录授权")
                    else:
                        self.log("[*] 当前流程需要 SSO，开始等待 sso cookie")
                        sso = wait_for_sso_cookie(
                            log_callback=self.log, cancel_callback=self.should_stop
                        )
                    if config.get("enable_nsfw", True):
                        if not sso:
                            self.log("[!] Device Flow 未获取 SSO，跳过基于 SSO 的 NSFW 设置")
                        else:
                            self.log("[*] 6. 开启 NSFW")
                            cf_clearance, browser_ua = extract_cf_clearance_and_ua(self.log)
                            nsfw_ok, nsfw_msg = enable_nsfw_for_token(
                                sso, cf_clearance=cf_clearance, user_agent=browser_ua, log_callback=self.log
                            )
                            if nsfw_ok:
                                self.log(f"[+] NSFW 开启成功: {nsfw_msg}")
                            else:
                                self.log(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
                    self.results.append({"email": email, "sso": sso, "profile": profile})
                    try:
                        line = f"{email}----{profile.get('password','')}----{sso}\n"
                        with open(self.accounts_output_file, "a", encoding="utf-8") as f:
                            f.write(line)
                    except Exception as file_exc:
                        self.log(f"[Debug] 保存账号文件失败: {file_exc}")
                    cpa_ok = add_sso_to_cpa(
                        sso,
                        email=email,
                        password=profile.get("password", ""),
                        log_callback=self.log,
                    )
                    retry_count_for_slot = 0
                    i += 1
                    if config.get("cpa_auto_add", False) and not cpa_ok:
                        self.fail_count += 1
                        self.log(
                            f"[!] 账号已注册但 CPA 入库失败: {email}"
                            "（邮箱密码已保存，请检查 CPA 服务后可手动补授权）"
                        )
                        _request_browser_restart_next("cpa-auth-failed", self.log)
                    else:
                        self.success_count += 1
                        self.log(f"[+] 注册成功: {email}")
                    if (
                        self.success_count > 0
                        and self.success_count % MEMORY_CLEANUP_INTERVAL == 0
                        and i < count
                    ):
                        cleanup_runtime_memory(
                            log_callback=self.log,
                            reason=f"已成功 {self.success_count} 个账号，执行定期清理",
                        )
                except RegistrationCancelled:
                    self.log("[!] 注册被用户停止")
                    break
                except AccountRetryNeeded as exc:
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= max_slot_retry:
                        self.log(
                            f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                        )
                    else:
                        self.fail_count += 1
                        self.log(
                            f"[-] 当前账号已达到最大重试次数，跳过: {exc}"
                        )
                        retry_count_for_slot = 0
                        i += 1
                except Exception as exc:
                    self.fail_count += 1
                    retry_count_for_slot = 0
                    i += 1
                    self.log(f"[-] 注册失败: {exc}")
                finally:
                    self.update_stats()
                    if self.should_stop():
                        break
                    if not _should_restart_after_account(i, count, self.should_stop()):
                        continue
                    try:
                        if browser is None:
                            start_browser(log_callback=self.log)
                        else:
                            _reset_browser_for_next_account(log_callback=self.log)
                        # 停止后不再调用 cancel_callback，避免 finally 里二次抛出 RegistrationCancelled
                        time.sleep(1)
                    except RegistrationCancelled:
                        break
                    except Exception as restart_exc:
                        if self.should_stop():
                            break
                        self.log(f"[Debug] 轮次清理/重启浏览器失败: {restart_exc}")
        except RegistrationCancelled:
            self.log("[!] 注册被用户停止")
        except Exception as exc:
            self.log(f"[!] 任务异常: {exc}")
        finally:
            try:
                stop_browser()
            except BaseException:
                pass
            self._set_running_ui(False)
            self.log("[*] 任务结束")


class CliStopController:
    def __init__(self):
        self.stop_requested = False

    def should_stop(self):
        return self.stop_requested

    def stop(self):
        self.stop_requested = True


def cli_log(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def read_cli_count(default_count):
    try:
        default_count = int(default_count)
    except (TypeError, ValueError):
        default_count = 1
    if default_count < 1:
        default_count = 1

    while True:
        try:
            raw = input(
                f"请输入本次注册数量（回车使用 {default_count}，输入 q 退出）: "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return None

        if raw.lower() in ("q", "quit", "exit"):
            return None
        if not raw:
            return default_count

        try:
            count = int(raw)
        except ValueError:
            cli_log("[!] 注册数量必须是正整数，请重新输入")
            continue
        if count < 1:
            cli_log("[!] 注册数量必须大于 0，请重新输入")
            continue
        return count


def run_registration_cli(count):
    controller = CliStopController()

    # 一次 Ctrl+C 可靠置停：SIGINT 处理器直接设停止标志，不依赖异常在
    # curl_cffi C 回调里向上传播（那里 KeyboardInterrupt 会被吞掉，导致
    # 第一次 Ctrl+C 无效、循环继续跑下一个账号）。连按两次 Ctrl+C 时第二次
    # 恢复默认行为强制中断。
    _prev_sigint = signal.getsignal(signal.SIGINT)

    def _on_sigint(signum, frame):
        if controller.should_stop():
            # 第二次：恢复默认并重新抛出，强制中断
            signal.signal(signal.SIGINT, _prev_sigint)
            raise KeyboardInterrupt
        controller.stop()
        cli_log("[!] 收到 Ctrl+C，正在停止（再按一次强制中断）")

    signal.signal(signal.SIGINT, _on_sigint)
    success_count = 0
    fail_count = 0
    retry_count_for_slot = 0
    max_slot_retry = 3
    accounts_output_file = os.path.join(
        os.path.dirname(__file__),
        f"accounts_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
    )
    cli_log(f"[*] 终端模式启动，目标数量: {count}")
    cli_log(f"[*] 成功账号将实时保存到: {accounts_output_file}")
    try:
        start_browser(log_callback=cli_log)
        cli_log("[*] 浏览器已启动")
        i = 0
        while i < count:
            if controller.should_stop():
                break
            cli_log(f"--- 开始第 {i + 1}/{count} 个账号 ---")
            try:
                email = ""
                dev_token = ""
                code = ""
                mail_ok = False
                max_mail_retry = 3
                for mail_try in range(1, max_mail_retry + 1):
                    cli_log(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
                    open_signup_page(
                        log_callback=cli_log, cancel_callback=controller.should_stop
                    )
                    cli_log("[*] 2. 创建邮箱并提交")
                    email, dev_token = fill_email_and_submit(
                        log_callback=cli_log, cancel_callback=controller.should_stop
                    )
                    cli_log(f"[*] 邮箱: {email}")
                    cli_log(f"[Debug] 邮箱credential(jwt): {dev_token}")
                    try:
                        with open(
                            os.path.join(os.path.dirname(__file__), "mail_credentials.txt"),
                            "a",
                            encoding="utf-8",
                        ) as f:
                            f.write(f"{email}\t{dev_token}\n")
                    except Exception:
                        pass
                    cli_log("[*] 3. 拉取验证码")
                    try:
                        code = fill_code_and_submit(
                            email,
                            dev_token,
                            log_callback=cli_log,
                            cancel_callback=controller.should_stop,
                        )
                        mail_ok = True
                        break
                    except Exception as mail_exc:
                        msg = str(mail_exc)
                        if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                            cli_log(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                            restart_browser(log_callback=cli_log)
                            sleep_with_cancel(1, controller.should_stop)
                            continue
                        raise

                if not mail_ok:
                    raise Exception("验证码阶段失败，已达到最大重试次数")
                cli_log(f"[*] 验证码: {code}")
                cli_log("[*] 4. 填写资料")
                profile = fill_profile_and_submit(
                    log_callback=cli_log, cancel_callback=controller.should_stop
                )
                cli_log(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
                cli_log("[*] 5. 准备 CPA 授权")
                if _can_start_device_flow_without_sso(email, profile.get("password", "")):
                    sso = ""
                    cli_log("[*] 当前为 Device Flow，跳过 SSO Cookie 等待，直接进入 Grok 登录授权")
                else:
                    cli_log("[*] 当前流程需要 SSO，开始等待 sso cookie")
                    sso = wait_for_sso_cookie(
                        log_callback=cli_log, cancel_callback=controller.should_stop
                    )
                if config.get("enable_nsfw", True):
                    if not sso:
                        cli_log("[!] Device Flow 未获取 SSO，跳过基于 SSO 的 NSFW 设置")
                    else:
                        cli_log("[*] 6. 开启 NSFW")
                        cf_clearance, browser_ua = extract_cf_clearance_and_ua(log_callback=cli_log)
                        nsfw_ok, nsfw_msg = enable_nsfw_for_token(
                            sso, cf_clearance=cf_clearance, user_agent=browser_ua, log_callback=cli_log
                        )
                        if nsfw_ok:
                            cli_log(f"[+] NSFW 开启成功: {nsfw_msg}")
                        else:
                            cli_log(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
                try:
                    line = f"{email}----{profile.get('password','')}----{sso}\n"
                    with open(accounts_output_file, "a", encoding="utf-8") as f:
                        f.write(line)
                except Exception as file_exc:
                    cli_log(f"[Debug] 保存账号文件失败: {file_exc}")
                cpa_ok = add_sso_to_cpa(
                    sso,
                    email=email,
                    password=profile.get("password", ""),
                    log_callback=cli_log,
                )
                retry_count_for_slot = 0
                i += 1
                if config.get("cpa_auto_add", False) and not cpa_ok:
                    fail_count += 1
                    cli_log(
                        f"[!] 账号已注册但 CPA 入库失败: {email}"
                        "（邮箱密码已保存，请检查 CPA 服务后可手动补授权）"
                    )
                    _request_browser_restart_next("cpa-auth-failed", cli_log)
                else:
                    success_count += 1
                    cli_log(f"[+] 注册成功: {email}")
                cli_log(f"[*] 当前统计: 成功 {success_count} | 失败 {fail_count}")
                if success_count > 0 and success_count % MEMORY_CLEANUP_INTERVAL == 0 and i < count:
                    cleanup_runtime_memory(
                        log_callback=cli_log,
                        reason=f"已成功 {success_count} 个账号，执行定期清理",
                    )
            except RegistrationCancelled:
                cli_log("[!] 注册被停止")
                break
            except AccountRetryNeeded as exc:
                retry_count_for_slot += 1
                if retry_count_for_slot <= max_slot_retry:
                    cli_log(
                        f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                    )
                else:
                    fail_count += 1
                    retry_count_for_slot = 0
                    i += 1
                    cli_log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
            except Exception as exc:
                fail_count += 1
                retry_count_for_slot = 0
                i += 1
                cli_log(f"[-] 注册失败: {exc}")
            finally:
                if controller.should_stop():
                    break
                if not _should_restart_after_account(i, count, controller.should_stop()):
                    continue
                try:
                    if browser is None:
                        start_browser(log_callback=cli_log)
                    else:
                        _reset_browser_for_next_account(log_callback=cli_log)
                    # 停止后不再调用 cancel_callback，避免 finally 里二次抛出 RegistrationCancelled
                    time.sleep(1)
                except KeyboardInterrupt:
                    controller.stop()
                    cli_log("[!] 收到 Ctrl+C，正在停止（再按一次强制中断）")
                    break
                except RegistrationCancelled:
                    break
                except Exception as restart_exc:
                    if controller.should_stop():
                        break
                    cli_log(f"[Debug] 轮次清理/重启浏览器失败: {restart_exc}")
    except KeyboardInterrupt:
        controller.stop()
        cli_log("[!] 收到 Ctrl+C，正在停止并清理")
    except RegistrationCancelled:
        cli_log("[!] 注册被停止")
    except Exception as exc:
        cli_log(f"[!] 任务异常: {exc}")
    finally:
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except Exception:
            pass
        try:
            cleanup_runtime_memory(log_callback=cli_log, reason="任务结束")
        except BaseException:
            pass
        try:
            cli_log(f"[*] 任务结束。成功 {success_count} | 失败 {fail_count}")
        except BaseException:
            pass
        try:
            signal.signal(signal.SIGINT, _prev_sigint)
        except Exception:
            pass


def main_cli():
    load_config()
    default_count = config.get("register_count", 1) or 1
    cli_log("[*] CLI 已加载配置")
    cli_log(f"[*] 当前邮箱服务商: {config.get('email_provider', 'duckmail')}")
    cli_log("[*] 输入 start 开始，输入 q 退出；每次 start 都可重新输入注册数量")
    while True:
        try:
            command = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            cli_log("[!] CLI 已退出")
            return

        if command in ("q", "quit", "exit"):
            cli_log("[*] CLI 已退出")
            return
        if command != "start":
            cli_log("[!] 请输入 start 开始，或输入 q 退出")
            continue

        count = read_cli_count(default_count)
        if count is None:
            cli_log("[*] CLI 已退出")
            return
        try:
            run_registration_cli(count)
        except KeyboardInterrupt:
            # 清理阶段仍可能漏出，保证 CLI 干净退出
            cli_log("[!] 已停止，返回主提示")


def main():
    if len(sys.argv) > 1 and sys.argv[1].strip().lower() in ("start", "cli", "--cli"):
        main_cli()
        return
    root = tk.Tk()
    setup_light_theme(root)
    app = GrokRegisterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass

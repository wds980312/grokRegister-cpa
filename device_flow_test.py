#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只测试已有账号的 Grok 登录 + CPA Device 授权，不执行注册。

用法:
  python3 device_flow_test.py --account accounts_20260716_095535.txt

也可以直接传邮箱和密码（不要把密码写进命令历史）:
  python3 device_flow_test.py --email name@example.com
  # 脚本会安全地交互读取密码
"""

import argparse
import getpass
import glob
import os
import sys

import grok_register_ttk as app


def read_account(path):
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("----", 2)
            if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
                return parts[0].strip(), parts[1].strip()
    raise ValueError(f"账号文件没有找到 email----password 记录: {path}")


def latest_account_file():
    paths = sorted(glob.glob("accounts_*.txt"), key=os.path.getmtime, reverse=True)
    return paths[0] if paths else ""


def main():
    parser = argparse.ArgumentParser(description="单独测试 Grok 登录和 CPA Device 授权")
    parser.add_argument("--account", default="", help="已有 accounts_*.txt 文件")
    parser.add_argument("--email", default="", help="已有账号邮箱")
    args = parser.parse_args()

    email = args.email.strip()
    password = ""
    if email:
        password = getpass.getpass("Grok 密码: ")
    else:
        path = args.account.strip() or latest_account_file()
        if not path:
            raise SystemExit("未找到 accounts_*.txt，请使用 --account 或 --email")
        email, password = read_account(path)
        print(f"[*] 使用已有账号: {email}")

    app.load_config()
    app.config["cpa_auto_add"] = True
    app.config["cpa_auth_flow"] = "device"
    app.config["cpa_prepare_grok_web"] = True
    app.config["cpa_restart_browser_before_login"] = True

    def log(message):
        print(message, flush=True)

    try:
        app.start_browser(log_callback=log)
        log("[*] 浏览器已启动，开始独立授权测试")
        app._run_cpa_device_flow(
            str(app.config.get("cpa_remote_url") or "").strip(),
            str(app.config.get("cpa_management_key") or "").strip(),
            email=email,
            password=password,
            log_callback=log,
        )
        log("[+] 独立 Device 授权测试完成")
        return 0
    except KeyboardInterrupt:
        log("[!] 测试已停止")
        return 130
    except Exception as exc:
        log(f"[-] 独立 Device 授权测试失败: {exc}")
        return 1
    finally:
        try:
            app.stop_browser()
        except BaseException:
            pass


if __name__ == "__main__":
    sys.exit(main())

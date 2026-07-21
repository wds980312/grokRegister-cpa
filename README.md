<div align="center">

[![Grok Register — 注册即入库 CLIProxyAPI](assets/banner.png)](https://github.com/Git-creat7/grokRegister-cpa)

批量注册 Grok 账号，注册成功后自动把 OAuth 凭证写入 [CLIProxyAPI (CPA)](https://github.com/router-for-me/CLIProxyAPI)：支持本地 auth 目录热加载，也支持 Management API 远程上传。

<p>
  <a href="https://github.com/Git-creat7/grokRegister-cpa/stargazers"><img src="https://img.shields.io/github/stars/Git-creat7/grokRegister-cpa?style=flat&logo=github" alt="GitHub stars"></a>
  <a href="https://github.com/Git-creat7/grokRegister-cpa/network/members"><img src="https://img.shields.io/github/forks/Git-creat7/grokRegister-cpa?style=flat&logo=github" alt="GitHub forks"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-success.svg" alt="GUI + CLI">
  <img src="https://img.shields.io/badge/Output-CLIProxyAPI-orange.svg" alt="CLIProxyAPI">
</p>

</div>

---

> 仅用于自动化流程研究、测试环境验证和个人学习。请遵守目标网站服务条款、当地法律法规与第三方服务限制。

## 核心流程

默认推荐 **Device Flow**（`cpa_auth_flow: device`）：

```text
打开注册页 → 创建临时邮箱 → 收验证码 → 填资料 / 过人机验证
   → 退出页轻量隔离（清掉注册态 SSO）
   → 邮箱登录 Grok（自动处理登录页 Turnstile）
   → 检测到 sso 后打开 CPA Device 授权链接
   → Continue / Allow（卡住会刷新自救，仍失败则重生授权码）
   → CPA 自动入库（status=ok），立即可用
```

旧 **OAuth** 流程（`cpa_auth_flow: oauth`）仍可用：注册后取 SSO → 授权码换 token → 写本地 `cpa_auth_dir` 和/或上传 Management API。

## 功能

- 注册成功后自动入库 CPA：Device Flow（推荐）或旧 OAuth 流程
- Docker Web 控制台 + noVNC 浏览器画面；也支持 GUI / CLI
- 浏览器后端：Docker Chromium / 本地 Chrome 无痕 / BitBrowser
- 注册页、登录页、Grok 入口自动处理 Cloudflare Turnstile
- 账号间退出页轻量隔离；登录 CF 连败中止，避免未登录硬进 Device
- Device 授权：Continue 后自救刷新，超时重生授权码
- DuckMail / YYDS / Cloudflare 临时邮箱
- 注册后可选开启 NSFW
- 页面卡住重试、验证码失败换邮箱、浏览器重启与内存清理
- CLI：一次 `Ctrl+C` 安全停止，清理阶段不刷 traceback；再按一次强制中断

## 环境要求

- Python 3.9+（或直接使用 Docker）
- Google Chrome / Chromium（Docker 镜像已内置）
- 可用的 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)
- 能访问注册页、临时邮箱 API、`accounts.x.ai` / `auth.x.ai` 的网络

## 安装

```bash
git clone https://github.com/Git-creat7/grokRegister-cpa.git
cd grokRegister-cpa
pip install -r requirements.txt
cp config.example.json config.json
```

编辑 `config.json` 后运行。

### Windows 一键启动

1. 按 [DEPLOYMENT.md](DEPLOYMENT.md) 用 Python 3.13 创建 `.venv` 并安装依赖
2. 双击 `start-gui.cmd` 开图形界面，或 `start-cli.cmd` 开命令行（输入 `start` 开始）

## 配置

| 配置项 | 说明 |
| --- | --- |
| `cpa_auto_add` | 是否开启 CPA 自动入库 |
| `cpa_auth_flow` | CPA 入库方式：`device`（授权后 CPA 自动保存）或 `oauth`（旧 SSO 换 token 流程） |
| `cpa_device_timeout` | Device Flow 等待授权超时时间，单位秒 |
| `cpa_prepare_grok_web` | Device Flow 前是否先打开 Grok Web 等待登录初始化 |
| `cpa_grok_web_wait_seconds` | Grok Web 登录/初始化等待时间，单位秒 |
| `cpa_auto_click_device` | Device Flow 是否自动填写登录并点击授权按钮，默认 true |
| `cpa_login_isolation` | 登录前隔离：`auto` 退出页优先 / `restart` 总是重启 / `clear` 只清会话 / `off` 关闭 |
| `cpa_login_cf_max_failures` | 登录页 Turnstile 连续失败达到次数后中止本账号 Device（默认 2） |
| `cpa_device_allow_rescue_seconds` | Device 点 Continue 后多久仍无 Allow 则刷新授权页自救（默认 10） |
| `cpa_device_allow_wait_seconds` | Device 点 Continue 后多久仍无 Allow 则重生授权码（默认 25） |
| `cpa_device_action_settle_seconds` | 点击 Continue/Allow 后的最短稳定等待（默认 10） |
| `cpa_auth_dir` | 本地 CPA auth 目录；写入 `xai-<email>.json`，可留空 |
| `cpa_remote_url` | 远程 CPA 地址，如 `http://你的CPA地址:8317` |
| `cpa_management_key` | 远程 CPA 管理密钥（`remote-management.secret-key` 明文） |
| `email_provider` | `duckmail` / `yyds` / `cloudflare` |
| `register_count` | 目标注册数量 |
| `proxy` | 代理；换 token 的 OAuth 请求也走此代理 |
| `browser_incognito` | 浏览器无痕模式；Docker 缺省开启，填 `false` 可关闭 |
| `browser_clear_data` | 是否清理 Cookie 和站点存储；缓存由 BitBrowser 启动前清理配置负责 |
| `browser_new_tab_per_step` | 关键步骤是否新开标签（注册页 / Grok登录 / Device授权），默认 false，复用一个标签 |
| `browser_backend` | 浏览器后端：`chromium` 或 `bitbrowser` |
| `browser_ip_check_url` | 浏览器代理出口 IP 检测地址，默认 `https://api.ipify.org?format=json` |
| `browser_ip_check_timeout` | BitBrowser 启动后等待有效公网 IP 的最长秒数 |
| `bitbrowser_check_public_ip` | BitBrowser 启动后是否检测浏览器实际出口 IP |
| `browser_reset_strategy` | 账号切换时的浏览器重置：`auto` 优先清理会话，清理失败再重启；`clear` 仅清理；`restart` 每轮完整重启 |
| `local_chrome_debug_address` | 本地 Chrome CDP 地址；Docker 中默认连接宿主机 `9222` 端口 |
| `bitbrowser_api_url` | BitBrowser 本地 API；Docker 中使用 `http://host.docker.internal:54345` |
| `bitbrowser_profile_id` | BitBrowser 测试环境 ID |
| `enable_nsfw` | 注册后是否尝试开启 NSFW |
| `cloudflare_api_base` | Cloudflare 临时邮箱 API 根地址 |
| `cloudflare_api_key` | 默认匿名模式留空；admin 模式填 `ADMIN_PASSWORD` |
| `cloudflare_auth_mode` | `none` / `bearer` / `x-api-key` / `x-admin-auth` / `query-key` |
| `cloudflare_custom_auth` | Worker 全局密码（`PASSWORDS`），注入 `x-custom-auth` |
| `cloudflare_path_*` | domains / accounts / token / messages 路径 |
| `defaultDomains` | Cloudflare 默认收信域名 |

### Cloudflare 邮箱（默认匿名）

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "",
  "cloudflare_auth_mode": "none",
  "cloudflare_path_domains": "/api/domains",
  "cloudflare_path_accounts": "/api/new_address",
  "cloudflare_path_token": "/api/token",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "你的收信域名.com"
}
```

匿名创建失败（例如 Turnstile）时可改 admin 创建：

```json
{
  "cloudflare_api_key": "你的 ADMIN_PASSWORD",
  "cloudflare_auth_mode": "x-admin-auth",
  "cloudflare_path_accounts": "/admin/new_address"
}
```

调试创建接口：

```bash
python cf_mail_debug.py \
  --api-base "https://你的-worker-api-域名" \
  --auth-mode x-admin-auth \
  --api-key "你的 ADMIN_PASSWORD" \
  --create-path /admin/new_address \
  --domain "你的收信域名.com"
```

Worker 若配置了全局 `PASSWORDS`，再加：

```json
{ "cloudflare_custom_auth": "你的全局访问密码" }
```

## CPA 自动入库

SSO 不是 CPA 凭据。程序会：

1. 用 SSO 走授权码流程（`referrer=grok-build`）向 `auth.x.ai` 换 `access_token` / `refresh_token`
2. 组装 `type=xai` 扁平 auth（`cli-chat-proxy.grok.com`）
3. 本地：`cpa_auth_dir` → `xai-<email>.json`（CPA 热加载）
4. 远程：`POST {cpa_remote_url}/v0/management/auth-files?name=...`（需管理密钥）

### 本地目录

```json
{
  "cpa_auto_add": true,
  "cpa_auth_dir": "你的CPA auth目录"
}
```

`cpa_auth_dir` 填 CPA 实际监听的 auth 目录路径即可。

### BitBrowser（合法测试）

BitBrowser 在宿主机运行，Docker 通过本地 API 和 CDP 连接。配置 `browser_backend` 为
`bitbrowser`，填写 `bitbrowser_profile_id`。Docker 中 API 地址使用
`http://host.docker.internal:54345`，本地直接运行时使用 `http://127.0.0.1:54345`。
BitBrowser API 应保持本机可访问，不要暴露到公网。项目只负责打开和关闭指定测试环境，
不会自动处理验证码或修改指纹参数。每次任务启动、每个账号开始前和任务结束时，
会清理 Cookie 和当前页面站点存储；缓存文件由 BitBrowser 的“启动浏览器前删除缓存文件”负责，
不会删除 BitBrowser 指纹环境。

### CPA Device Flow

当 `cpa_auth_flow` 设置为 `device` 时（推荐），注册成功后主流程为：

1. **轻量隔离**：优先打开 `accounts.x.ai/sign-out` 清掉注册态会话（单独 `clearCookies` 往往不够）
2. **邮箱登录 Grok**：打开登录页，自动处理登录页 Turnstile；密码提交后若检测到 sso，可提前进入 Device，不必死等 Grok UI
3. **Device 授权**：请求 CPA `/v0/management/xai-auth-url`，打开授权链接并自动点 Continue / Allow
4. **卡住自救**：Continue 后约 10s 仍无 Allow → 刷新同链接自救一次；约 25s 仍无 → 重生授权码（最多 `cpa_device_refresh_retries` 次）
5. **入库**：CPA 侧 `status=ok` 后自动保存凭证，本工具不再额外上传 `auth-files`

关键配置示例（密钥请只写在本地 `config.json`，不要提交仓库）：

```json
{
  "cpa_auto_add": true,
  "cpa_auth_flow": "device",
  "cpa_remote_url": "http://host.docker.internal:8317",
  "cpa_management_key": "你的管理密钥明文",
  "cpa_prepare_grok_web": true,
  "cpa_auto_click_device": true,
  "cpa_login_isolation": "auto",
  "cpa_device_allow_rescue_seconds": 10,
  "cpa_device_allow_wait_seconds": 25
}
```

Docker 容器访问宿主机 CPA 时，`cpa_remote_url` 常用 `http://host.docker.internal:8317`。  
若遇 Cloudflare 等需人工处理页面，可把 `cpa_auto_click_device` 设为 `false`，通过 noVNC 手动点。

登录页 Turnstile 连续失败或登录始终未确认时，会**中止本账号 Device**（邮箱密码仍写入 `accounts_*.txt`），并标记下一账号强制重启浏览器，避免脏环境连跪。


### 本地 Chrome 无痕模式

控制台的“浏览器环境”可以选择“本地 Chrome 无痕”。先在宿主机运行：

```bash
sh start-local-chrome.sh
```

脚本会用独立资料目录、无痕模式和 CDP `9222` 端口启动 Google Chrome。然后在控制台选择
“本地 Chrome 无痕”并开始任务。Chrome 会沿用宿主机 Clash 的规则代理；如果只想让
`auth.grok.com` 走日本节点，应在 Clash 规则中单独配置该域名，其他流量继续直连。
Docker 通过 `host.docker.internal` 连接本地 Chrome，不会改变 BitBrowser 配置。

### 远程 Management API

```json
{
  "cpa_auto_add": true,
  "cpa_auth_dir": "",
  "cpa_remote_url": "http://你的CPA地址:8317",
  "cpa_management_key": "你的管理密钥明文"
}
```

要求 CPA：`remote-management.allow-remote` 按访问方式配置；密钥为配置里的明文（启动后配置文件可能被写成 bcrypt，上传仍用明文）。

本地与远程可同时开启。日志前缀：`[CPA]`。

### 独立转换

已有 SSO 时可脱离注册流程：

```bash
# 写本地目录
python sso_to_auth_json.py --sso sso_list.txt --cpa-auth-dir /path/to/auths

# 上传远程 CPA
python sso_to_auth_json.py --sso sso_list.txt \
  --cpa-remote-url http://你的CPA地址:8317 \
  --cpa-management-key '你的管理密钥'

# 单个 cookie + 代理
python sso_to_auth_json.py --sso-cookie 'eyJ...' \
  --cpa-auth-dir ./auths \
  --proxy http://127.0.0.1:7890
```

`sso_list.txt`：一行一个 SSO，或 `邮箱----密码----sso`。

### 为什么必须用授权码流程

这是本项目区别于普通 SSO→token 脚本的关键，踩过坑后固化下来：

- **SSO 不能直接喂给 CPA。** CPA 走 OAuth，需要 `access_token` / `refresh_token`，SSO cookie 只是换 token 的入场券。
- **必须带 `referrer=grok-build`。** xAI 后端要求 access_token 携带 `referrer=grok-build` claim，否则 grok build 通道（`cli-chat-proxy.grok.com`）拒绝，调用 chat 时报 `permission-denied / Access to the chat endpoint is denied`。早期用 device flow 换的 token **不带**这个 claim，会全部失效。
- **解法：授权码流程（Authorization Code + PKCE）。** 在 `/oauth2/authorize` 和 consent 提交两处注入 `referrer=grok-build`，换出的 token 才带此 claim。程序换完会自动校验，日志显示 `access_token 已带 referrer=grok-build`。
- **base_url 必须是 `cli-chat-proxy.grok.com/v1`。** 写入的 auth 记录 `base_url` 指向 grok build 免费通道；若为空，CPA 会回退到计费通道 `api.x.ai/v1`，同样触发 `permission-denied`。

如果 CPA 里已有旧的失效号（`base_url=api.x.ai/v1` 或 `referrer=None`），用本节的独立转换脚本以相同邮箱重新生成一遍覆盖即可（文件名按 `xai-<email>.json` 命名，会原地覆盖）。

## 运行

### CLI

```bash
python grok_register_ttk.py cli
```

提示后输入 `start`，再输入本次注册数量；直接回车使用 `config.json` 中的默认值。
任务结束后 CLI 会继续等待下一次输入，不会自动退出。输入 `q`、`quit` 或 `exit` 退出。

```text
> start
请输入本次注册数量（回车使用 1，输入 q 退出）: 3
...
> start
请输入本次注册数量（回车使用 1，输入 q 退出）: 1
```

注册数量只对当前任务生效，不会自动修改配置文件。任务执行中按一次 `Ctrl+C` 会停止当前任务并返回主提示；在主提示处按 `Ctrl+C` 可退出。

### Docker Web 控制台

Docker 默认启动本机 Web 控制台：

```bash
docker compose up -d --build
```

打开 [http://127.0.0.1:18081](http://127.0.0.1:18081)，即可输入数量、开始或停止任务、查看实时日志。遇到 Cloudflare 或设备授权时，点击页面上的“打开浏览器画面”，在 [http://127.0.0.1:18082/vnc.html](http://127.0.0.1:18082/vnc.html) 操作容器内 Chrome。两个页面都只绑定本机，容器配置了 `restart: unless-stopped`，Docker 服务重启后会自动恢复。

停止服务：

```bash
docker compose down
```

如需使用命令行模式：

```bash
docker compose run --rm grok-register cli
```

### GUI

```bash
python grok_register_ttk.py
```

可在界面里改：邮箱服务商、代理、Cloudflare（API Base / 鉴权 / 收信域名 / 全局密码）、CPA 开关、auth 目录、远程地址与管理密钥。点击「开始注册」时会写回 `config.json`。

## 输出文件

| 文件 | 内容 |
| --- | --- |
| `accounts_*.txt` | 邮箱、密码、SSO |
| `mail_credentials.txt` | 临时邮箱凭证 |

均含敏感信息，已在 `.gitignore` 中忽略。`config.json` 也不提交，请用 `config.example.json` 复制。

## 稳定性

- 每账号结束后重启浏览器
- 每成功 5 个账号做一次内存清理
- 邮箱提交后确认页面前进，避免空等验证码
- 未收到验证码时换邮箱重试
- 最终页卡住时重试当前账号

## 常见问题

**CPA 没出现新账号**  
检查 `cpa_auto_add`、`cpa_auth_dir` 或 `cpa_remote_url` + `cpa_management_key`；看 `[CPA]` 日志是否换 token / 上传成功；本机/服务器能否访问 `auth.x.ai`。

**远程上传失败**  
确认 CPA 管理 API 已启用、密钥明文正确；远程访问需 `allow-remote: true`。可用：

```bash
curl -H "Authorization: Bearer <管理密钥>" \
  http://你的CPA地址:8317/v0/management/auth-files
```

`cpa_remote_url` 填 CPA 实例根地址，不要附带 OpenAI 兼容接口的 `/v1`。程序会自动追加 `/v0/management/auth-files`。

**Device 授权 Continue 后长时间不出 Allow**

正常情况下 2～5 秒内会出现 Allow。若卡住，程序会先刷新授权页自救，再重生授权码；看日志中的「刷新授权页自救」「重新生成授权链接」。仍失败请检查 CPA 服务与网络。

**登录页 Turnstile 一直失败**

Docker Chromium 比本机 Chrome 更容易触发人机。程序会自动点/回填；连续失败会中止本账号并在下一号强制重启浏览器。可换网络、代理出口，或改用「本地 Chrome 无痕」。

**创建 Cloudflare 邮箱时 curl 超时**

如果当前网络需要代理访问 `workers.dev`，请在 GUI 的“代理”字段或 `config.json` 的 `proxy` 中显式填写代理地址。不要只依赖终端的 `HTTP_PROXY` / `HTTPS_PROXY`，从桌面启动 GUI 时可能不会继承这些环境变量。

**开启 NSFW 时返回 403**

设置出生日期可能被 `grok.com` 的 Cloudflare 防护拦截。该步骤失败不会影响账号保存和 CPA 入库；不需要敏感内容时可关闭“注册后开启 NSFW”。

**CLI 为什么还开浏览器**  
CLI 只是不启动 Tk；注册页、Turnstile、SSO 仍依赖真实浏览器。

**NSFW 失败**  
常见为 Cloudflare 拦截。账号仍会保存并入库 CPA。

**国内服务器调模型超时**  
入库成功只说明凭证到了 CPA；调用上游 `cli-chat-proxy.grok.com` 还需服务器出网可达（或配置 CPA `proxy-url`）。

**CPA 返回 `503 auth_unavailable: no auth available`**  
不是网络超时，而是 CPA 当前没有可用的 xAI auth。检查：auth 是否写入并被热加载、token 是否带 `referrer=grok-build`、账号是否 403 权限拒绝或 429 免费额度耗尽。free 号走 `cli-chat-proxy` 的 build 通道，额度与权限由上游控制，可能抖动。

**chat 报 `permission-denied` / Access to the chat endpoint is denied**  
token 缺 `referrer=grok-build`，或 `base_url` 误指向 `api.x.ai`。用本仓库授权码流程重转覆盖对应 `xai-<email>.json`。

## 目录结构

```text
.
├── grok_register_ttk.py      # 主程序（GUI / CLI + CPA 入库）
├── sso_to_auth_json.py       # SSO → CPA 转换（可独立运行）
├── cf_mail_debug.py          # Cloudflare 邮箱调试
├── config.example.json
├── requirements.txt
├── start-gui.cmd             # Windows 启动 GUI
├── start-cli.cmd             # Windows 启动 CLI
├── DEPLOYMENT.md             # 本机 / Windows 部署
├── tests/
└── assets/banner.png
```

## Star History

<a href="https://www.star-history.com/?type=date&repos=Git-creat7%2FgrokRegister-cpa">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Git-creat7/grokRegister-cpa&type=date&theme=dark&legend=top-left&sealed_token=nLCws8QCmosQswlx1hTjASUcz8r72ZEKXOP1C8WmTFqosF65NL66q77qlMIbBZ6Kqic0cOqA5VisinVcERXNFlwMZqx0ET8872ALY3-k8rvCyvNqa-RxzMLV_oOrrAV54D0E6Pfv4WWTmaA6WYQBr2U5dizobLbasNLXpKTnZZJI7-uRL0zomGISzIGq" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Git-creat7/grokRegister-cpa&type=date&legend=top-left&sealed_token=nLCws8QCmosQswlx1hTjASUcz8r72ZEKXOP1C8WmTFqosF65NL66q77qlMIbBZ6Kqic0cOqA5VisinVcERXNFlwMZqx0ET8872ALY3-k8rvCyvNqa-RxzMLV_oOrrAV54D0E6Pfv4WWTmaA6WYQBr2U5dizobLbasNLXpKTnZZJI7-uRL0zomGISzIGq" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Git-creat7/grokRegister-cpa&type=date&legend=top-left&sealed_token=nLCws8QCmosQswlx1hTjASUcz8r72ZEKXOP1C8WmTFqosF65NL66q77qlMIbBZ6Kqic0cOqA5VisinVcERXNFlwMZqx0ET8872ALY3-k8rvCyvNqa-RxzMLV_oOrrAV54D0E6Pfv4WWTmaA6WYQBr2U5dizobLbasNLXpKTnZZJI7-uRL0zomGISzIGq" />
 </picture>
</a>

## License

[MIT](LICENSE)

## Acknowledgments

Thanks to [linux.do](https://linux.do) and [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI).

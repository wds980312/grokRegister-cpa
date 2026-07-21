# 本机部署

以下步骤使用 Python 3.13 在仓库内创建 `.venv` 隔离环境。

## 启动

- 图形界面：双击 `start-gui.cmd`
- 命令行：双击 `start-cli.cmd`，输入 `start` 后开始任务

## 首次使用前配置

编辑 `config.json`，至少填写可用的临时邮箱配置：

- Cloudflare：`cloudflare_api_base`、`defaultDomains`，必要时填写认证配置
- DuckMail：将 `email_provider` 改为 `duckmail` 并填写 `duckmail_api_key`
- YYDS：将 `email_provider` 改为 `yyds` 并填写 `yyds_api_key` 或 `yyds_jwt`

如需自动写入 CLIProxyAPI，再配置 `cpa_auto_add` 及本地 auth 目录或远程 Management API 参数。

## 重新安装依赖

```powershell
uv python install 3.13
uv venv --python 3.13 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
```

运行环境还需要安装 Chrome 或 Chromium。

## Docker 启动

Docker 镜像内置 Python 3.13、Chromium 和 Xvfb，默认启动本机 Web 控制台。首次使用前先复制并编辑配置：

```bash
cp config.example.json config.json
docker compose up -d --build
```

打开 `http://127.0.0.1:18081`，在页面中输入本次注册数量并开始任务。遇到 Cloudflare 或设备授权时，点击页面上的“打开浏览器画面”，访问 `http://127.0.0.1:18082/vnc.html` 操作容器内 Chrome。配置文件和账号输出会保留在仓库目录中。

页面提供数量、开始、停止、运行状态和实时日志。页面只允许本机访问；容器设置了 `restart: unless-stopped`，Docker 服务重启后会自动恢复。

停止服务：

```bash
docker compose down
```

如需使用命令行模式：

```bash
docker compose run --rm grok-register cli
```

也可以只构建镜像：

```bash
docker compose build
```

## 配置与敏感信息

1. 只复制示例配置，不要把真实密钥提交到 Git：

```bash
cp config.example.json config.json
```

2. `config.json`、`accounts_*.txt`、本地 auth 输出已在 `.gitignore` 中忽略。
3. Device Flow 推荐在 `config.json` 中设置（示例）：

```json
{
  "cpa_auto_add": true,
  "cpa_auth_flow": "device",
  "cpa_remote_url": "http://host.docker.internal:8317",
  "cpa_management_key": "你的管理密钥",
  "email_provider": "yyds",
  "browser_backend": "chromium"
}
```

4. Web 控制台与 noVNC 仅绑定 `127.0.0.1`，不要改成 `0.0.0.0` 暴露到公网。


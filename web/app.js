const form = document.querySelector("#startForm");
const countInput = document.querySelector("#countInput");
const browserBackend = document.querySelector("#browserBackend");
const startButton = document.querySelector("#startButton");
const stopButton = document.querySelector("#stopButton");
const actionMessage = document.querySelector("#actionMessage");
const stateLabel = document.querySelector("#stateLabel");
const successCount = document.querySelector("#successCount");
const failedCount = document.querySelector("#failedCount");
const providerLabel = document.querySelector("#providerLabel");
const defaultCount = document.querySelector("#defaultCount");
const logOutput = document.querySelector("#logOutput");
const logHint = document.querySelector("#logHint");
const copyLogsButton = document.querySelector("#copyLogsButton");
const browserLink = document.querySelector("#browserLink");
const localChromeHint = document.querySelector("#localChromeHint");

const stateText = {
  idle: "空闲",
  starting: "启动中",
  running: "运行中",
  stopping: "停止中",
  completed: "已完成",
  error: "异常",
};

let lastState = "idle";
// 只在首次加载时用服务端默认值填充表单，之后轮询不再覆盖用户修改
let controlsHydrated = false;
let renderedLogs = null;
let copyFeedbackTimer = null;

function setMessage(message, type = "") {
  actionMessage.textContent = message;
  actionMessage.className = `action-message ${type}`.trim();
}

function updateBrowserLink(backend) {
  if (localChromeHint) {
    localChromeHint.hidden = backend !== "local_chrome";
  }
  if (backend === "local_chrome") {
    browserLink.textContent = "本机 Chrome 窗口";
    browserLink.href = "#";
    browserLink.removeAttribute("target");
    browserLink.classList.add("browser-link-disabled");
    return;
  }
  browserLink.textContent = "打开浏览器画面";
  browserLink.href = "http://127.0.0.1:18082/vnc.html?autoconnect=true&resize=scale";
  browserLink.target = "_blank";
  browserLink.classList.remove("browser-link-disabled");
}

function isLogNearBottom() {
  const remaining = logOutput.scrollHeight - logOutput.scrollTop - logOutput.clientHeight;
  return remaining <= 24;
}

function isSelectingLogText() {
  const selection = window.getSelection && window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return false;
  const anchor = selection.anchorNode;
  return !!(anchor && logOutput.contains(anchor.nodeType === 1 ? anchor : anchor.parentNode));
}

function renderLogs(logs) {
  const nextLogs = Array.isArray(logs) && logs.length ? logs : ["等待启动..."];
  const firstRender = renderedLogs === null;
  const sharesPrefix = !firstRender
    && renderedLogs.length <= nextLogs.length
    && renderedLogs.every((line, index) => line === nextLogs[index]);
  // 用户上翻查看/正在框选日志时不强制滚到底，避免“一直往下推”
  const shouldStickToBottom = firstRender || (isLogNearBottom() && !isSelectingLogText());

  if (firstRender || !sharesPrefix) {
    logOutput.textContent = nextLogs.join("\n");
  } else if (nextLogs.length > renderedLogs.length) {
    const appended = nextLogs.slice(renderedLogs.length).join("\n");
    logOutput.append(document.createTextNode(`${renderedLogs.length ? "\n" : ""}${appended}`));
  }

  renderedLogs = nextLogs.slice();
  if (shouldStickToBottom) {
    logOutput.scrollTop = logOutput.scrollHeight;
  }
}

function renderStatus(status) {
  const busy = ["starting", "running", "stopping"].includes(status.state);
  const stateChanged = lastState !== status.state;
  lastState = status.state;

  stateLabel.textContent = stateText[status.state] || status.state;
  stateLabel.dataset.state = status.state;
  successCount.textContent = status.success;
  failedCount.textContent = status.failed;
  providerLabel.textContent = `邮箱服务商: ${status.provider}`;
  defaultCount.textContent = status.default_count;

  // 首次灌入默认数量/环境；后续轮询只更新状态与日志，不回写表单
  if (!controlsHydrated) {
    countInput.value = status.default_count;
    browserBackend.value = status.browser_backend || browserBackend.value;
    controlsHydrated = true;
  }

  startButton.disabled = busy;
  stopButton.disabled = !busy;
  countInput.disabled = busy;
  browserBackend.disabled = busy;
  updateBrowserLink(browserBackend.value);

  logHint.textContent = busy
    ? "实时更新中"
    : (stateChanged && status.state === "completed" ? "任务完成" : "等待任务");
  renderLogs(status.logs);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

async function refreshStatus() {
  try {
    renderStatus(await request("/api/status"));
    if (actionMessage.classList.contains("error")) setMessage("");
  } catch (error) {
    setMessage(error.message, "error");
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage("正在启动...", "working");
  try {
    renderStatus(await request("/api/start", {
      method: "POST",
      body: JSON.stringify({
        count: countInput.value,
        browser_backend: browserBackend.value,
      }),
    }));
    setMessage("任务已启动", "success");
  } catch (error) {
    setMessage(error.message, "error");
  }
});

stopButton.addEventListener("click", async () => {
  setMessage("正在停止...", "working");
  try {
    renderStatus(await request("/api/stop", { method: "POST", body: "{}" }));
    setMessage("已发送停止请求", "success");
  } catch (error) {
    setMessage(error.message, "error");
  }
});

browserBackend.addEventListener("change", () => {
  updateBrowserLink(browserBackend.value);
});

copyLogsButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(logOutput.textContent);
    copyLogsButton.textContent = "已复制";
    window.clearTimeout(copyFeedbackTimer);
    copyFeedbackTimer = window.setTimeout(() => {
      copyLogsButton.textContent = "复制日志";
    }, 1600);
  } catch (error) {
    setMessage("复制日志失败，请手动选择复制", "error");
  }
});

refreshStatus();
window.setInterval(refreshStatus, 1000);

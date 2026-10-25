/* Ratel 摸鱼台 — 前端逻辑 v2
 * 运行在 DBX 沙箱 iframe。通过 window.dbxPlugin 与宿主/Sidecar 通信：
 *   - invoke: 向 Go sidecar 发 JSON-RPC（连接 / 发命令 / 查房间 / 重连）
 *   - onEvent: 接收 sidecar 反向推送（ratel/status、ratel/rooms、ratel/raw、ratel/prompt）
 * 命令台: Tab 页签 + 当前页签命令条；房间游戏模式(typeDesc)自动切页签，手动点选优先。
 */
(function () {
  "use strict";

  // 命令页签。cmd 行为: 默认直接发送；act:"fill" 填入输入框等玩家补参数；act:"append" 往输入框追加一个字符。
  var TABS = [
    {
      key: "global", title: "全局", always: true,
      cmds: [
        { label: "e", hint: "退出/返回", cmd: "e" }
      ]
    },
    {
      key: "room", title: "开局/房间", always: true,
      cmds: [
        { label: "v", hint: "刷新房间列表", cmd: "v" },
        { label: "s", hint: "开始游戏", cmd: "s" },
        { label: "ready", hint: "准备", cmd: "ready" },
        { label: "set", hint: "设置（补：ds/ct/sk/lz/pwd/pn + on/off）", cmd: "set ", act: "fill" }
      ]
    },
    {
      key: "ddz", title: "斗地主/跑得快", keys: ["斗地主", "跑得快"],
      cmds: [
        { label: "p", hint: "不出", cmd: "p" }
      ]
    },
    {
      key: "texas", title: "德州扑克", keys: ["德州"],
      cmds: [
        { label: "check", hint: "看牌", cmd: "check" },
        { label: "call", hint: "跟注", cmd: "call" },
        { label: "raise", hint: "加注（补金额）", cmd: "raise ", act: "fill" },
        { label: "allin", hint: "全下", cmd: "allin" },
        { label: "fold", hint: "弃牌", cmd: "fold" }
      ]
    },
    {
      key: "mahjong", title: "大众麻将", keys: ["麻将"],
      cmds: [
        { label: "pass", hint: "过", cmd: "pass" },
        { label: "W", hint: "万（补数字 1-9）", cmd: "W", act: "fill" },
        { label: "T", hint: "条（补数字 1-9）", cmd: "T", act: "fill" },
        { label: "O", hint: "筒（补数字 1-9）", cmd: "O", act: "fill" },
        { label: "D", hint: "东", cmd: "D" },
        { label: "N", hint: "南", cmd: "N" },
        { label: "X", hint: "西", cmd: "X" },
        { label: "B", hint: "北", cmd: "B" },
        { label: "P", hint: "碰", cmd: "P" },
        { label: "G", hint: "杠", cmd: "G" },
        { label: "H", hint: "胡", cmd: "H" }
      ]
    },
    {
      key: "liar", title: "骗子酒馆", keys: ["酒馆", "liar"],
      cmds: [
        { label: "k", hint: "K（追加到输入）", cmd: "k", act: "append" },
        { label: "q", hint: "Q（追加）", cmd: "q", act: "append" },
        { label: "a", hint: "A（追加）", cmd: "a", act: "append" },
        { label: "s", hint: "S（追加）", cmd: "s", act: "append" },
        { label: "x", hint: "X（追加）", cmd: "x", act: "append" },
        { label: "c", hint: "质疑", cmd: "c" }
      ]
    }
  ];

  // typeDesc → tab key 映射（按服务端 GameTypes 文案匹配）
  function matchTabKey(typeDesc) {
    if (!typeDesc) return null;
    var t = typeDesc.toLowerCase();
    for (var i = 0; i < TABS.length; i++) {
      var tab = TABS[i];
      if (!tab.keys) continue;
      for (var j = 0; j < tab.keys.length; j++) {
        if (t.indexOf(tab.keys[j].toLowerCase()) !== -1) return tab.key;
      }
    }
    return null;
  }

  // ---- DBX 宿主桥 ----
  var dbx = window.dbxPlugin || {};

  function invoke(method, params, timeoutMs) {
    try {
      return dbx.invoke(method, params || {}, { timeoutMs: timeoutMs || 8000 });
    } catch (e) {
      return Promise.reject(e);
    }
  }

  var OFFICIAL_SERVER_HOST = "ratel.isnico.com";
  var OFFICIAL_SERVER_PORT = 9998;

  var state = {
    connected: false,
    activeTab: "global",   // 当前显示的页签
    autoTab: null,         // 服务端模式推出来的页签
    pinned: false,         // 用户手动点过页签后，自动切换让位
    logLines: 0
  };

  // ---- DOM ----
  var el = {
    status: document.getElementById("status"),
    btnConnect: document.getElementById("btnConnect"),
    btnReconnect: document.getElementById("btnReconnect"),
    btnDisconnect: document.getElementById("btnDisconnect"),
    btnRefresh: document.getElementById("btnRefreshRooms"),
    btnSend: document.getElementById("btnSend"),
    btnClearLog: document.getElementById("btnClearLog"),
    serverPreset: document.getElementById("serverPreset"),
    host: document.getElementById("host"),
    port: document.getElementById("port"),
    nick: document.getElementById("nick"),
    proto: document.getElementById("proto"),
    cmdInput: document.getElementById("cmdInput"),
    cmdMode: document.getElementById("cmdMode"),
    deckTabs: document.getElementById("deckTabs"),
    deckChips: document.getElementById("deckChips"),
    log: document.getElementById("log"),
    logCount: document.getElementById("logCount")
  };

  // ---- 主题 ----
  function applyTheme(theme) {
    var appearance = "";
    try {
      var currentTheme = theme || dbx.theme;
      if (typeof currentTheme === "string") appearance = currentTheme;
      else appearance = (currentTheme && currentTheme.appearance) || "";
      // DBX 也会把当前主题写到插件根节点，作为没有完整事件负载时的兜底。
      if (!appearance) appearance = document.documentElement.getAttribute("data-dbx-theme") || "";
    } catch (e) { /* 开发态 */ }
    // 只接受 DBX 文档约定的值，避免异常事件把样式切到未知主题。
    if (appearance !== "light" && appearance !== "dark") appearance = "dark";
    document.documentElement.setAttribute("data-appearance", appearance);
  }

  // DBX 在切换主题时通过环境事件推送新的 theme；不能只在打开工作台时读取一次。
  function onDbxEnvironment(event) {
    var payload = event && (event.detail || event.data) || {};
    // 兼容 { theme }、{ data: { theme } }、直接 theme 对象等桥接负载。
    var theme = payload.theme || (payload.data && payload.data.theme) ||
      (payload.value && payload.value.theme) || payload;
    applyTheme(theme);
  }

  window.addEventListener("dbx-plugin-env", onDbxEnvironment);
  // 主题 token/属性由宿主直接注入根节点，监听它可覆盖旧版或异常事件桥。
  if (window.MutationObserver) {
    new MutationObserver(function () { applyTheme(); }).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-dbx-theme"]
    });
  }

  function ts() {
    var d = new Date();
    function p(n) { return n < 10 ? "0" + n : n; }
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function logLine(text, cls) {
    var line = document.createElement("div");
    line.className = "line " + (cls || "");
    var time = document.createElement("span");
    time.className = "ts";
    time.textContent = ts();
    line.appendChild(time);
    line.appendChild(document.createTextNode(text));
    el.log.appendChild(line);
    el.log.scrollTop = el.log.scrollHeight;
    state.logLines++;
    if (state.logLines > 500) {
      el.log.removeChild(el.log.firstChild);
      state.logLines--;
    }
    el.logCount.textContent = state.logLines ? (state.logLines + " 行") : "";
  }

  function setStatus(state_, text) {
    el.status.className = "status " + state_;
    el.status.textContent = "● " + text;
  }

  function setConnected(on) {
    state.connected = on;
    el.btnConnect.disabled = on;
    el.btnReconnect.disabled = !on;
    el.btnDisconnect.disabled = !on;
    el.btnSend.disabled = !on;
    if (on) setStatus("on", "已连接");
    else setStatus("idle", "未连接");
  }

  // ---- 连接参数 ----
  function syncServerPreset() {
    var official = el.serverPreset.value === "official";
    el.host.readOnly = official;
    if (official) {
      el.host.value = OFFICIAL_SERVER_HOST;
      el.port.value = String(OFFICIAL_SERVER_PORT);
    }
  }

  function toRpcParams() {
    return {
      runtime: {
        host: (el.host.value || "").trim() || OFFICIAL_SERVER_HOST,
        port: parseInt(el.port.value, 10) || OFFICIAL_SERVER_PORT
      },
      external_config: {
        protocol: el.proto.value || "ws",
        nickname: (el.nick.value || "").trim() || "dbx"
      }
    };
  }

  // ---- 命令台渲染 ----
  function activeTab() {
    var t = TABS.filter(function (x) { return x.key === state.activeTab; })[0];
    return t || TABS[0];
  }

  function renderTabs() {
    el.deckTabs.innerHTML = "";
    TABS.forEach(function (t) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "tab" + (t.key === state.activeTab ? " active" : "") + (t.key === state.autoTab ? " auto" : "");
      b.textContent = t.title;
      if (t.key === state.autoTab) b.title = "当前房间模式";
      b.addEventListener("click", function () {
        state.activeTab = t.key;
        state.pinned = true;
        renderTabs();
        renderChips();
      });
      el.deckTabs.appendChild(b);
    });
  }

  function runChip(c) {
    if (c.act === "fill") {
      // 填入输入框，光标定位末尾，等玩家补参数
      el.cmdInput.value = c.cmd;
      el.cmdInput.focus();
      return;
    }
    if (c.act === "append") {
      // 每按一下往输入框追加一个字符（骗子酒馆连续选牌）
      el.cmdInput.value += c.cmd;
      el.cmdInput.focus();
      return;
    }
    sendCommand(c.cmd);
  }

  function renderChips() {
    el.deckChips.innerHTML = "";
    activeTab().cmds.forEach(function (c) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      b.title = c.hint;
      b.appendChild(document.createTextNode(c.label));
      var hint = document.createElement("span");
      hint.className = "hint";
      hint.textContent = c.hint;
      b.appendChild(hint);
      b.addEventListener("click", function () { runChip(c); });
      el.deckChips.appendChild(b);
    });
  }

  // 房间模式驱动页签自动切换（用户手动点选后让位，除非模式变化）
  function setAutoMode(typeDesc) {
    var key = matchTabKey(typeDesc);
    if (typeDesc) {
      logLine("房间模式: " + typeDesc, "mode");
      if (key) {
        state.autoTab = key;
        if (!state.pinned || key !== state.activeTab) {
          state.activeTab = key;
          state.pinned = false;
        }
        el.cmdMode.textContent = typeDesc;
      }
    } else {
      state.autoTab = null;
      el.cmdMode.textContent = "全局";
    }
    renderTabs();
    renderChips();
  }

  // ---- 操作 ----
  async function doConnect() {
    var params = toRpcParams();
    setStatus("busy", "连接中…");
    try {
      await invoke("connection/test", params, 8000);
      await invoke("connection/connect", params, 8000);
      logLine("已连接 " + params.runtime.host + ":" + params.runtime.port + "（" + params.external_config.protocol + "）", "sys");
    } catch (err) {
      logLine("连接失败: " + (err && err.message ? err.message : String(err)), "error");
      setStatus("off", "连接失败");
    }
  }

  async function doDisconnect() {
    try { await invoke("connection/disconnect", {}, 5000); } catch (e) { /* 幂等 */ }
    setConnected(false);
    logLine("已断开", "sys");
  }

  async function doReconnect() {
    try { await invoke("ratel/reconnect", toRpcParams(), 8000); logLine("已重连", "sys"); }
    catch (err) { logLine("重连失败: " + (err && err.message ? err.message : String(err)), "error"); }
  }

  async function sendCommand(cmd) {
    if (!state.connected) { logLine("尚未连接，无法发送 " + cmd, "error"); return; }
    var text = (cmd || "").trim();
    if (!text) return;
    try {
      await invoke("ratel/sendCommand", { command: text }, 5000);
      logLine("> " + text, "sent");
      if (el.cmdInput.value === cmd) el.cmdInput.value = "";
    } catch (err) {
      logLine("发送失败: " + (err && err.message ? err.message : String(err)), "error");
    }
  }

  async function refreshRooms() {
    if (!state.connected) return;
    try {
      await invoke("ratel/sendCommand", { command: "v" }, 5000);
      var data = await invoke("ratel/rooms", {}, 5000);
      var rooms = (data && data.rooms) || [];
      // 取最活跃房间的模式驱动页签
      var active = rooms.filter(function (r) {
        return r.stateDesc === "进行中" || (r.players > 0);
      })[0] || rooms[0];
      setAutoMode(active ? (active.typeDesc || "") : "");
    } catch (err) { /* 由推送事件覆盖 */ }
  }

  // ---- sidecar 反向事件 ----
  function onDbxEvent(evt) {
    if (!evt || !evt.method) return;
    var p = evt.params || {};
    switch (evt.method) {
      case "ratel/status":
        if (p.connected) {
          setConnected(true);
          logLine("服务端确认连接，昵称 " + (p.nickname || ""), "sys");
        } else {
          setConnected(false);
          logLine("连接断开: " + (p.reason || ""), "error");
        }
        break;
      case "ratel/rooms":
        if (p.rooms) {
          var active = p.rooms.filter(function (r) { return r.stateDesc === "进行中" || r.players > 0; })[0] || p.rooms[0];
          setAutoMode(active ? (active.typeDesc || "") : "");
        }
        break;
      case "ratel/raw":
        if (p.sent) return; // 命令回显已由前端记录
        logLine(p.text || "", "");
        break;
      case "ratel/prompt":
        // 服务端进入/退出交互模式
        if (p.interactive) setStatus("on", "对局中");
        else if (state.connected) setStatus("on", "已连接");
        break;
    }
  }

  function bindEvents() {
    el.serverPreset.addEventListener("change", syncServerPreset);
    el.btnConnect.addEventListener("click", doConnect);
    el.btnDisconnect.addEventListener("click", doDisconnect);
    el.btnReconnect.addEventListener("click", doReconnect);
    el.btnRefresh.addEventListener("click", refreshRooms);
    el.btnClearLog.addEventListener("click", function () { el.log.innerHTML = ""; state.logLines = 0; el.logCount.textContent = ""; });
    el.btnSend.addEventListener("click", function () { sendCommand(el.cmdInput.value); });
    el.cmdInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); sendCommand(el.cmdInput.value); }
    });
    if (dbx.onEvent) {
      try { dbx.onEvent(onDbxEvent); } catch (e) { /* 宿主桥异常 */ }
    }
  }

  function init() {
    applyTheme();
    syncServerPreset();
    renderTabs();
    renderChips();
    bindEvents();
    setConnected(false);
    logLine("Ratel 摸鱼台已加载。左侧填写服务器后点「连接」。", "sys");
    if (dbx.ready) {
      dbx.ready.then(function () { applyTheme(); }).catch(function () {});
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();

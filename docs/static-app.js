/*
 * 在线只读版适配层。
 *
 * 它不修改原 Flask 前端，而是：
 *   1. 在页面启动前接管 /api/* 请求，用解密后的静态数据回答；
 *   2. 用 localStorage 保存自选、排序和列偏好；
 *   3. 用一个访问密码解锁 AES-GCM 加密的 data.enc.json。
 *
 * 这样原来的表格、卡片、筛选、详情、导出代码全部可以原样复用。
 */
(function () {
  "use strict";

  const PASS_KEY = "shuju_online_pass";
  const REMEMBER_KEY = "shuju_online_remember";
  const FAV_KEY = "shuju_online_favorites";
  const PREF_KEY = "shuju_online_prefs_v2";
  const USER_KEY = "shuju_online_imported_batch";

  const appState = {
    payload: null,
    favorites: [],
    prefs: {},
    userBatch: null
  };

  let resolveReady;
  let readyResolved = false;
  const ready = new Promise(resolve => { resolveReady = resolve; });
  let unlockBusy = false;

  // ── 隐藏没有意义的控件；导入、刷新、导出、分享都保留 ───────
  const style = document.createElement("style");
  style.textContent = `
    #stopBtn, .actions .sep, .drop-zone { display: none !important; }

    .online-lock {
      position: fixed; inset: 0; z-index: 1000;
      display: flex; align-items: center; justify-content: center;
      padding: 20px; padding-bottom: calc(20px + env(safe-area-inset-bottom));
      background: linear-gradient(150deg, #eef4ff 0%, #f7f9fc 52%, #eef7f1 100%);
      opacity: 1; transition: opacity 220ms ease;
    }
    .online-lock.hide { opacity: 0; pointer-events: none; }
    .online-lock-card {
      width: min(380px, 100%); padding: 26px 22px 22px;
      border: 1px solid #dbe3ef; border-radius: 18px; background: #ffffff;
      box-shadow: 0 24px 70px rgba(15,23,42,0.16);
    }
    .online-lock-icon { font-size: 30px; line-height: 1; margin-bottom: 12px; }
    .online-lock h1 { margin: 0 0 6px; font-size: 21px; color: #111827; }
    .online-lock p { margin: 0 0 18px; color: #667085; font-size: 13px; line-height: 1.65; }
    .online-lock input[type="password"] {
      width: 100%; height: 48px; padding: 0 13px;
      border: 1px solid #b8c1d0; border-radius: 9px;
      font-size: 16px; color: #111827; background: #ffffff; outline: none;
    }
    .online-lock input[type="password"]:focus {
      border-color: #1f6feb; box-shadow: 0 0 0 3px rgba(31,111,235,0.12);
    }
    .online-lock-row {
      display: flex; align-items: center; justify-content: space-between;
      gap: 12px; margin: 12px 0 16px; color: #667085; font-size: 12px;
    }
    .online-lock-row label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
    .online-lock-row input { width: 15px; height: 15px; margin: 0; accent-color: #1f6feb; }
    .online-lock button {
      width: 100%; min-height: 46px; border: 0; border-radius: 9px;
      background: #1f6feb; color: #ffffff; font-size: 15px; font-weight: 700;
      cursor: pointer;
    }
    .online-lock button:disabled { opacity: 0.65; cursor: wait; }
    .online-lock-error {
      min-height: 20px; margin-top: 10px; color: #c73535; font-size: 12px;
      line-height: 1.5;
    }
    .online-lock-foot {
      margin-top: 14px; color: #98a2b3; font-size: 11px; line-height: 1.6;
      text-align: center;
    }
    .online-readonly-badge {
      display: inline-flex; align-items: center;
      padding: 1px 6px; border-radius: 999px;
      background: #eef2f7; color: #667085; font-size: 10px; font-weight: 600;
    }

    /* 手机上给导入/刷新/导出/分享留出空间 */
    @media (max-width: 760px) {
      .actions { flex-wrap: wrap; }
      .actions label.btn { flex: 1 1 96px; }
      #updateBtn { flex: 1 1 96px; }
      #exportBtn { flex: 0 0 44px; }
      #mobileShareBtn { display: inline-flex !important; flex: 1 1 100%; }
    }
  `;
  document.head.appendChild(style);

  // ── 解锁界面 ─────────────────────────────────────────────
  const lock = document.createElement("div");
  lock.className = "online-lock";
  lock.innerHTML = `
    <div class="online-lock-card" role="dialog" aria-modal="true" aria-labelledby="onlineLockTitle">
      <div class="online-lock-icon">📈</div>
      <h1 id="onlineLockTitle">指数数据</h1>
      <p>这是在线只读版。输入访问密码后即可查看，手机和电脑都可以打开。</p>
      <input id="onlinePassInput" type="password" inputmode="text"
             autocomplete="current-password" placeholder="访问密码" aria-label="访问密码">
      <div class="online-lock-row">
        <label><input id="onlineRemember" type="checkbox"> 记住这台设备</label>
        <span id="onlineLockStatus"></span>
      </div>
      <button id="onlineUnlockBtn" type="button">打开数据</button>
      <div class="online-lock-error" id="onlineLockError"></div>
      <div class="online-lock-foot">数据由 GitHub Pages 托管，不依赖任何人的电脑开机</div>
    </div>
  `;
  document.body.appendChild(lock);
  lock.style.display = "none";

  const passInput = lock.querySelector("#onlinePassInput");
  const rememberInput = lock.querySelector("#onlineRemember");
  const unlockBtn = lock.querySelector("#onlineUnlockBtn");
  const lockError = lock.querySelector("#onlineLockError");
  const lockStatus = lock.querySelector("#onlineLockStatus");

  // ── AES-GCM 解密 ─────────────────────────────────────────
  function base64ToBytes(value) {
    const binary = atob(String(value || ""));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  async function decryptPayload(encrypted, passphrase) {
    if (!window.crypto || !window.crypto.subtle) {
      throw new Error("当前浏览器不支持加密解密，请使用最新版 Chrome / Edge / Safari");
    }
    const baseKey = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(passphrase),
      "PBKDF2",
      false,
      ["deriveKey"]
    );
    const key = await crypto.subtle.deriveKey(
      {
        name: "PBKDF2",
        salt: base64ToBytes(encrypted.salt),
        iterations: Number(encrypted.iterations) || 250000,
        hash: "SHA-256"
      },
      baseKey,
      { name: "AES-GCM", length: 256 },
      false,
      ["decrypt"]
    );
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: base64ToBytes(encrypted.iv) },
      key,
      base64ToBytes(encrypted.data)
    );

    let bytes = new Uint8Array(plain);
    if (encrypted.compression === "gzip") {
      if (typeof DecompressionStream !== "function") {
        throw new Error("当前浏览器不支持压缩数据，请更新浏览器后重试");
      }
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
      bytes = new Uint8Array(await new Response(stream).arrayBuffer());
    }
    return JSON.parse(new TextDecoder().decode(bytes));
  }

  async function loadPayload(passphrase) {
    const encryptedResp = await fetch("./data.enc.json", { cache: "no-store" });
    if (encryptedResp.ok) {
      const encrypted = await encryptedResp.json();
      return decryptPayload(encrypted, passphrase);
    }

    // 本地开发可以用未加密的 data.json；线上仓库不会包含这个文件。
    const plainResp = await fetch("./data.json", { cache: "no-store" });
    if (plainResp.ok) return plainResp.json();
    throw new Error("找不到数据文件，请先运行构建脚本");
  }

  // ── 本地偏好 ─────────────────────────────────────────────
  function readJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function loadUserBatch() {
    const saved = readJson(USER_KEY, null);
    if (saved && saved.batch && saved.indexData) appState.userBatch = saved;
  }

  function saveUserBatch() {
    if (!appState.userBatch) return;
    try {
      localStorage.setItem(USER_KEY, JSON.stringify(appState.userBatch));
    } catch (_) {
      // 浏览器存储已满时静默失败，当前页面仍可使用导入的数据
    }
  }

  function flattenImportRows(rows) {
    const keys = new Set();
    rows.forEach(row => {
      if (row && row.fields && typeof row.fields === "object") {
        Object.keys(row.fields).forEach(key => keys.add(key));
      }
    });
    const columns = ["指数代码", "指数名称", "发布时间"]
      .concat(Array.from(keys).sort((a, b) => a.localeCompare(b, "zh-CN")));
    const data = rows.map((row, index) => {
      const flat = {
        id: index + 1,
        "指数代码": row.index_code || "",
        "指数名称": row.index_name || "",
        "发布时间": row.data_date || ""
      };
      if (row.fields && typeof row.fields === "object") {
        Object.entries(row.fields).forEach(([key, value]) => { flat[key] = value; });
      }
      return flat;
    });
    return { data, columns };
  }

  function applyPayload(payload) {
    appState.payload = payload;
    appState.staticIndexData = payload.indexData || { data: [], columns: [], batch: null };
    loadUserBatch();

    if (appState.userBatch && appState.userBatch.batch) {
      const base = Array.isArray(payload.batches) ? payload.batches : [];
      payload.batches = [
        appState.userBatch.batch,
        ...base.filter(batch => batch.id !== appState.userBatch.batch.id)
      ];
    }

    const savedFavs = readJson(FAV_KEY, null);
    appState.favorites = Array.isArray(savedFavs)
      ? savedFavs
      : (Array.isArray(payload.favorites) ? payload.favorites : []);

    const savedPrefs = readJson(PREF_KEY, null);
    appState.prefs = (savedPrefs && typeof savedPrefs === "object")
      ? savedPrefs
      : (payload.preferences && typeof payload.preferences === "object" ? payload.preferences : {});
  }

  function saveFavorites() {
    localStorage.setItem(FAV_KEY, JSON.stringify(appState.favorites));
  }

  function savePrefs() {
    localStorage.setItem(PREF_KEY, JSON.stringify(appState.prefs));
  }

  // ── 用静态数据实现原 /api/* 接口 ─────────────────────────
  function jsonResponse(data, status) {
    return new Response(JSON.stringify(data), {
      status: status || 200,
      headers: { "Content-Type": "application/json; charset=utf-8" }
    });
  }

  function parseBody(init) {
    try {
      if (!init || !init.body) return {};
      if (typeof init.body === "string") return JSON.parse(init.body);
      return {};
    } catch (_) {
      return {};
    }
  }

  function handleApi(url, init) {
    const payload = appState.payload || {};
    const method = String((init && init.method) || "GET").toUpperCase();
    const parsed = new URL(url, window.location.origin);
    const path = parsed.pathname;

    if (path === "/api/sources") return jsonResponse(payload.sources || []);
    if (path.startsWith("/api/sources/")) {
      const id = Number(path.split("/").pop());
      const source = (payload.sources || []).find(s => Number(s.id) === id);
      return source ? jsonResponse(source) : jsonResponse({ error: "数据源不存在" }, 404);
    }
    if (path === "/api/batches") return jsonResponse(payload.batches || []);
    if (path === "/api/periods") return jsonResponse(payload.periods || { periods: [], default: "" });
    if (path === "/api/index-data") {
      const batchId = Number(parsed.searchParams.get("batch_id"));
      if (appState.userBatch && appState.userBatch.indexData &&
          (!batchId || Number(appState.userBatch.batch.id) === batchId)) {
        return jsonResponse(appState.userBatch.indexData);
      }
      return jsonResponse(appState.staticIndexData || { data: [], columns: [], batch: null });
    }
    if (path.startsWith("/api/index-data/")) {
      const id = Number(path.split("/").pop());
      const source = appState.userBatch && appState.userBatch.indexData
        ? appState.userBatch.indexData
        : (appState.staticIndexData || {});
      const row = (source.data || []).find(r => Number(r.id) === id);
      return row ? jsonResponse(row) : jsonResponse({ error: "记录不存在" }, 404);
    }

    if (path === "/api/favorites/check") {
      const codes = parseBody(init).codes || [];
      const result = {};
      const owned = new Set(appState.favorites.map(f => f.index_code));
      codes.forEach(code => { result[code] = owned.has(code); });
      return jsonResponse(result);
    }
    if (path === "/api/favorites") {
      if (method === "GET") return jsonResponse(appState.favorites);
      if (method === "POST") {
        const body = parseBody(init);
        const code = String(body.index_code || "").trim();
        if (!code) return jsonResponse({ error: "缺少 index_code" }, 400);
        if (!appState.favorites.some(f => f.index_code === code)) {
          appState.favorites.push({
            index_code: code,
            index_name: body.index_name || "",
            source_id: body.source_id || 1
          });
          saveFavorites();
        }
        return jsonResponse({ ok: true, id: code });
      }
    }
    if (path.startsWith("/api/favorites/")) {
      const code = decodeURIComponent(path.slice("/api/favorites/".length));
      if (method === "DELETE") {
        appState.favorites = appState.favorites.filter(f => f.index_code !== code);
        saveFavorites();
        return jsonResponse({ ok: true });
      }
    }

    if (path === "/api/preferences") return jsonResponse(appState.prefs);
    if (path.startsWith("/api/preferences/")) {
      const key = decodeURIComponent(path.slice("/api/preferences/".length));
      if (method === "PUT") {
        const body = parseBody(init);
        if (body.value === undefined) return jsonResponse({ error: "缺少 value" }, 400);
        appState.prefs[key] = body.value;
        savePrefs();
        return jsonResponse({ ok: true });
      }
      return jsonResponse({ key, value: appState.prefs[key] === undefined ? null : appState.prefs[key] });
    }

    if (path === "/api/network") {
      return jsonResponse({
        lan_ip: "",
        port: window.location.port,
        url: window.location.href,
        hostname: window.location.hostname,
        client_is_local: true
      });
    }
    if (path === "/api/shutdown") return jsonResponse({ ok: true });
    if (path === "/api/import") {
      if (method !== "POST") return jsonResponse({ error: "Method not allowed" }, 405);
      const body = parseBody(init);
      const rows = Array.isArray(body.rows) ? body.rows : [];
      if (!rows.length) return jsonResponse({ error: "没有数据行" }, 400);

      const flat = flattenImportRows(rows);
      const now = new Date();
      const previousId = appState.userBatch && appState.userBatch.batch
        ? appState.userBatch.batch.id
        : null;
      const batch = {
        id: now.getTime(),
        source_id: 1,
        batch_date: now.toISOString().slice(0, 10),
        status: "success",
        record_count: rows.length,
        error_msg: "",
        created_at: now.toISOString().slice(0, 19).replace("T", " ")
      };
      appState.userBatch = {
        batch,
        indexData: { data: flat.data, columns: flat.columns, batch }
      };
      saveUserBatch();

      const base = Array.isArray(payload.batches)
        ? payload.batches.filter(b => b.id !== batch.id && b.id !== previousId)
        : [];
      payload.batches = [batch, ...base];
      return jsonResponse({ ok: true, batch_id: batch.id, record_count: rows.length });
    }
    if (path === "/api/fetch") {
      return jsonResponse({ error: "在线版每天自动更新；可以点「刷新最新」重新加载已发布的数据。" }, 403);
    }
    return jsonResponse({ error: "Not found" }, 404);
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : (input && input.url) || "";
    if (!url.startsWith("/api/")) return realFetch(input, init);
    return ready.then(() => handleApi(url, init || {}));
  };

  // ── 解锁流程 ─────────────────────────────────────────────
  function setBusy(busy) {
    unlockBusy = busy;
    unlockBtn.disabled = busy;
    unlockBtn.textContent = busy ? "正在打开…" : "打开数据";
    lockStatus.textContent = busy ? "解密中" : "";
  }

  function finishUnlock(payload) {
    applyPayload(payload);
    if (rememberInput.checked) {
      localStorage.setItem(PASS_KEY, passInput.value.trim());
      sessionStorage.removeItem(PASS_KEY);
    } else {
      sessionStorage.setItem(PASS_KEY, passInput.value.trim());
      localStorage.removeItem(PASS_KEY);
    }
    lock.classList.add("hide");
    window.setTimeout(() => lock.remove(), 240);
    if (!readyResolved) {
      readyResolved = true;
      resolveReady(payload);
    }
  }

  async function tryUnlock(fromSaved) {
    if (unlockBusy) return;
    const pass = passInput.value.trim();
    if (!pass) {
      lockError.textContent = "请输入访问密码";
      passInput.focus();
      return;
    }
    lockError.textContent = "";
    setBusy(true);
    try {
      const payload = await loadPayload(pass);
      finishUnlock(payload);
    } catch (err) {
      const message = String(err && err.message || err);
      if (/decrypt|operation|bad|incorrect/i.test(message)) {
        lockError.textContent = "密码不正确，请重新输入";
      } else {
        lockError.textContent = message || "打开失败，请重试";
      }
      if (fromSaved) {
        localStorage.removeItem(PASS_KEY);
        sessionStorage.removeItem(PASS_KEY);
      }
      passInput.select();
    } finally {
      setBusy(false);
    }
  }

  unlockBtn.addEventListener("click", () => tryUnlock(false));
  passInput.addEventListener("keydown", event => {
    if (event.key === "Enter") tryUnlock(false);
  });

  // ── 页面装饰 ─────────────────────────────────────────────
  function decoratePage() {
    const title = document.title;
    if (title) document.title = "指数数据（在线版）";
    const heading = document.querySelector(".brand h1");
    if (heading) heading.textContent = "指数数据";
    const shareBtn = document.getElementById("mobileShareBtn");
    if (shareBtn) {
      shareBtn.textContent = "🔗 分享";
      shareBtn.title = "分享此在线看板";
    }
    const shareTitle = document.getElementById("shareTitle");
    if (shareTitle) shareTitle.textContent = "分享此看板";
    const sharePanel = document.getElementById("sharePanel");
    if (sharePanel) {
      const intro = sharePanel.querySelector("p");
      if (intro) intro.textContent = "把下面的网址发给任何手机或电脑，打开即可使用。";
      const hint = sharePanel.querySelector(".share-hint");
      if (hint) hint.textContent = "不需要电脑开机，也不需要连接同一个网络；导入的数据保存在当前浏览器。";
    }
    const emptyCopy = document.querySelector(".empty-copy");
    if (emptyCopy) emptyCopy.textContent = "当前数据为空，可以导入 CSV 或点「刷新最新」。";
    const meta = document.querySelector(".brand .meta");
    if (meta && !meta.textContent.trim()) meta.textContent = "在线版";
  }

  function setupPublicActions() {
    const oldUpdate = document.getElementById("updateBtn");
    if (oldUpdate) {
      const fresh = oldUpdate.cloneNode(true);
      fresh.textContent = "刷新最新";
      fresh.title = "重新加载 GitHub Pages 上的最新数据（每天自动更新）";
      oldUpdate.replaceWith(fresh);
      fresh.addEventListener("click", () => window.location.reload());
    }
  }

  decoratePage();
  window.addEventListener("DOMContentLoaded", setupPublicActions);

  async function bootstrap() {
    // 公开版：直接读取 data.json，不需要密码。
    try {
      const plainResp = await fetch("./data.json", { cache: "no-store" });
      if (plainResp.ok) {
        const payload = await plainResp.json();
        applyPayload(payload);
        if (!readyResolved) {
          readyResolved = true;
          resolveReady(payload);
        }
        return;
      }
    } catch (_) {
      // 没有 data.json 时继续检查是否有加密数据
    }

    let hasEncrypted = false;
    try {
      const head = await fetch("./data.enc.json", { method: "HEAD", cache: "no-store" });
      hasEncrypted = head.ok;
    } catch (_) {
      hasEncrypted = false;
    }

    if (!hasEncrypted) {
      lock.innerHTML = `
        <div class="online-lock-card" role="dialog" aria-modal="true">
          <div class="online-lock-icon">⚠️</div>
          <h1>数据加载失败</h1>
          <p>请确认网络正常后刷新页面。如果刚部署过，GitHub Pages 可能需要几十秒生效。</p>
          <button id="onlineRetryBtn" type="button">刷新页面</button>
        </div>
      `;
      lock.style.display = "flex";
      const retry = lock.querySelector("#onlineRetryBtn");
      if (retry) retry.addEventListener("click", () => window.location.reload());
      return;
    }

    // 兼容本地仍使用加密 data.enc.json 的情况。
    lock.style.display = "flex";
    const savedPass = localStorage.getItem(PASS_KEY) || sessionStorage.getItem(PASS_KEY);
    if (savedPass) {
      passInput.value = savedPass;
      rememberInput.checked = !!localStorage.getItem(PASS_KEY);
      lockStatus.textContent = "自动打开";
      tryUnlock(true);
    } else {
      window.setTimeout(() => passInput.focus(), 120);
    }
  }

  bootstrap();
})();

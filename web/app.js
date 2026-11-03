/* 视频笔记生成器前端逻辑（多视频 / 精细进度 / 产物定位） */
(() => {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const state = {
    sources: [],
    job: null,
    timer: null,
    fsPath: "",
    history: [],
    submitting: false,
  };

  /* ---------------- 基础 ---------------- */
  async function api(path, opts) {
    const r = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (!r.ok) throw new Error((data && (data.error || data.detail)) || ("HTTP " + r.status));
    return data;
  }

  let toastTimer = null;
  function toast(msg, type) {
    const el = $("#toast");
    el.textContent = msg;
    el.className = "toast" + (type ? " " + type : "");
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 3600);
  }

  /* ---------------- 页内确认框（替代 window.confirm） ---------------- */
  let confirmResolve = null;
  function askConfirm(title, msg, okText) {
    return new Promise((resolve) => {
      $("#confirmTitle").textContent = title || "确认操作";
      $("#confirmText").textContent = msg || "";
      $("#confirmOk").textContent = okText || "确认";
      $("#modalConfirm").hidden = false;
      confirmResolve = resolve;
    });
  }
  function closeConfirm(val) {
    const m = $("#modalConfirm");
    if (m) m.hidden = true;
    if (confirmResolve) { const r = confirmResolve; confirmResolve = null; r(!!val); }
  }

  const fmtSize = (n) => {
    n = Number(n) || 0;
    if (n > 1048576) return (n / 1048576).toFixed(1) + " MB";
    if (n > 1024) return (n / 1024).toFixed(0) + " KB";
    return n + " B";
  };
  const fmtDur = (s) => {
    s = Number(s) || 0;
    const m = Math.floor(s / 60), ss = Math.floor(s % 60);
    return m ? m + " 分 " + ss + " 秒" : ss + " 秒";
  };
  const fmtTime = (ts) => {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const p = (x) => String(x).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  };

  /* ---------------- 视频来源 ---------------- */
  function renderSources() {
    const box = $("#sources");
    box.innerHTML = "";
    state.sources.forEach((s, i) => {
      const row = document.createElement("div");
      row.className = "source-row";
      row.dataset.i = i;
      row.innerHTML = `
        <select class="src-type">
          <option value="bili" ${s.type === "bili" ? "selected" : ""}>B站视频</option>
          <option value="local" ${s.type === "local" ? "selected" : ""}>本地视频</option>
          <option value="collection" ${s.type === "collection" ? "selected" : ""}>B站合集 / 多P</option>
          <option value="directory" ${s.type === "directory" ? "selected" : ""}>本地目录</option>
        </select>
        <input class="src-input" placeholder="${s.type === 'directory' ? '选择或粘贴视频目录路径' : s.type === 'collection' ? '合集详情页链接，或合集内的 BV 号 / 视频链接' : s.type === "local" ? "选择或粘贴本地视频路径" : "BV 号或 https://www.bilibili.com/video/BV..."}" value="${esc(s.input)}">
        <button class="btn btn-sm src-read" ${s.loading ? 'disabled' : ''}>${s.loading ? '正在读取…' : ['collection', 'directory'].includes(s.type) ? '读取视频列表' : '读取标题'}</button>
        <button class="btn btn-sm btn-icon btn-danger src-del" title="移除">✕</button>
        <div class="row2"><span class="src-title">${s.title ? "已识别：" + esc(s.title) : ['collection', 'directory'].includes(s.type) ? '开始生成时也会自动读取全部视频' : "标题会自动识别"}</span>
          ${['local', 'directory'].includes(s.type) ? '<button class="btn btn-sm src-browse">浏览…</button>' : ""}</div>
        ${s.type === 'directory' ? `<label class="batch-options"><input type="checkbox" class="src-recursive" ${s.recursive ? 'checked' : ''}> 包含子目录中的视频</label>` : ''}
        ${s.items ? `<details class="batch-list"><summary>已读取 ${s.items.length} 个视频，将全部处理</summary><ol>${s.items.map((item) => `<li>${esc(item.title || item.input)}</li>`).join('')}</ol></details>` : ''}
        ${s.error ? `<div class="batch-error" role="alert">${esc(s.error)}</div>` : ''}`;
      box.appendChild(row);

      row.querySelector(".src-type").addEventListener("change", (e) => {
        s.type = e.target.value;
        s.items = null; s.title = ''; s.error = ''; s.revision = (s.revision || 0) + 1;
        renderSources();
      });
      row.querySelector(".src-input").addEventListener("input", (e) => {
        s.input = e.target.value.trim();
        s.items = null; s.title = ''; s.error = ''; s.revision = (s.revision || 0) + 1;
        row.querySelector('.batch-list')?.remove();
        row.querySelector('.batch-error')?.remove();
        row.querySelector('.src-title').textContent = '内容已修改，将重新读取';
      });
      row.querySelector(".src-read").addEventListener("click", () => {
        if (['collection', 'directory'].includes(s.type)) expandSource(s).catch((e) => toast(e.message, 'err'));
        else readTitle(i);
      });
      row.querySelector('.src-recursive')?.addEventListener('change', (e) => {
        s.recursive = e.target.checked; s.items = null; s.title = ''; s.error = '';
        s.revision = (s.revision || 0) + 1; renderSources();
      });
      row.querySelector(".src-del").addEventListener("click", () => {
        if (state.sources.length <= 1) { toast("至少保留一个视频来源", "err"); return; }
        state.sources.splice(i, 1);
        renderSources();
        updateModeRow();
      });
      const browse = row.querySelector(".src-browse");
      if (browse) browse.addEventListener("click", () => openFs(i));
      if (state.submitting) row.querySelectorAll('input,select,button').forEach((control) => { control.disabled = true; });
    });
    updateModeRow();
  }

  function addSource(type) {
    state.sources.push({ type: type || "bili", input: "", title: "" });
    renderSources();
  }

  function updateModeRow() {
    const multi = state.sources.length > 1 || state.sources.some((s) => ['collection', 'directory'].includes(s.type));
    $("#modeRow").hidden = !multi;
    if (!multi) {
      const r = $('input[name="mode"][value="single"]');
      if (r) r.checked = true;
    }
  }

  function mergeMode() {
    const sel = $('input[name="mode"]:checked');
    return sel ? sel.value : "separate";
  }

  async function readTitle(i) {
    const s = state.sources[i];
    if (!s || !s.input) { toast("先填写视频链接或路径", "err"); return; }
    try {
      const d = await api("/api/videoinfo?input=" + encodeURIComponent(s.input));
      s.title = d.title || s.input;
      renderSources();
      toast("已识别标题：" + s.title, "ok");
    } catch (e) {
      toast("读取标题失败：" + e.message, "err");
    }
  }

  async function expandSource(source) {
    if (!source.input.trim()) throw new Error('请先填写合集链接或本地目录');
    if (source.loading) throw new Error('视频列表正在读取，请稍候');
    const revision = source.revision || 0;
    source.loading = true; source.error = ''; renderSources();
    try {
      const result = await api('/api/sources/expand', { method: 'POST', body: JSON.stringify({
        type: source.type, input: source.input, recursive: !!source.recursive,
      }) });
      if (revision !== (source.revision || 0) || !state.sources.includes(source)) throw new Error('来源已修改，请重新读取列表');
      source.items = result.inputs; source.title = result.title;
      return source.items;
    } catch (error) {
      if (revision === (source.revision || 0)) { source.items = null; source.error = error.message; }
      throw error;
    } finally { source.loading = false; renderSources(); }
  }

  /* ---------------- 提交任务 ---------------- */
  async function submit() {
    const btn = $("#btnSubmit");
    btn.disabled = true;
    state.submitting = true;
    ['btnAddSource', 'btnAddCollection', 'btnAddDirectory'].forEach((id) => { $('#' + id).disabled = true; });
    renderSources();
    $("#submitHint").textContent = "正在读取视频来源…";
    try {
      const sources = state.sources.filter((s) => s.input.trim());
      if (!sources.length) throw new Error('请至少填写一个视频来源');
      const inputs = [], seen = new Set();
      for (const source of sources) {
        const items = ['collection', 'directory'].includes(source.type)
          ? source.items || await expandSource(source)
          : [{ input: source.input.trim(), isLocal: source.type === 'local', title: source.title || '' }];
        for (const item of items) {
          const key = item.isLocal ? item.input.toLowerCase() : item.input;
          if (!seen.has(key)) { seen.add(key); inputs.push(item); }
        }
      }
      const mode = inputs.length > 1 ? mergeMode() : 'single';
      const body = {
        inputs, mode,
        title: $("#title").value.trim() || (inputs.length > 1 && sources.length === 1 && ['collection', 'directory'].includes(sources[0].type) ? sources[0].title : ''),
        format: $("#format").value, quality: $("#quality").value, asrModel: $("#asrModel").value,
        maxHeight: $("#maxHeight").value, maxFrames: $("#maxFrames").value, interval: $("#interval").value,
      };
      $("#submitHint").textContent = '正在创建任务：' + inputs.length + ' 个视频…';
      const d = await api("/api/jobs", { method: "POST", body: JSON.stringify(body) });
      state.job = d.job;
      renderJob(d.job);
      $("#submitHint").textContent = "";
      startPoll();
      loadHistory();
    } catch (e) {
      toast("提交失败：" + e.message, "err");
      $("#submitHint").textContent = "";
    } finally {
      btn.disabled = false;
      state.submitting = false;
      ['btnAddSource', 'btnAddCollection', 'btnAddDirectory'].forEach((id) => { $('#' + id).disabled = false; });
      renderSources();
    }
  }

  /* ---------------- 进度渲染 ---------------- */
  function normFiles(job) {
    const list = [];
    const f = job.files;
    if (Array.isArray(f)) {
      f.forEach((x) => list.push({ name: x.name, size: x.size, url: x.url }));
    } else if (f && typeof f === "object") {
      Object.keys(f).forEach((k) => list.push({ name: k, size: f[k].size, url: f[k].url }));
    }
    return list.sort((a, b) => (a.name.split("/").length - b.name.split("/").length) || a.name.localeCompare(b.name));
  }

  function renderJob(job) {
    if (!job) return;
    $("#jobEmpty").hidden = true;
    $("#jobPanel").hidden = false;
    $("#jobTitle").textContent = job.title || job.input || "任务";
    const modeText = { single: "单视频", separate: "分别生成", merged: "整合生成" }[job.mode] || job.mode;
    $("#jobMeta").textContent = [
      modeText,
      (job.inputs && job.inputs.length > 1) ? job.inputs.length + " 个视频" : "",
      job.quality + " 质量档",
      "开始于 " + fmtTime(job.started),
    ].filter(Boolean).join(" · ");
    $("#btnDeleteJob").onclick = () => confirmDelete(job.id, job.title, job.status);
    $("#btnStopJob").hidden = !["running", "queued"].includes(job.status);
    $("#btnRetryJob").hidden = !["error", "partial", "cancelled"].includes(job.status);
    $("#btnStopJob").onclick = async () => {
      try {
        await api("/api/jobs/" + job.id + "/cancel", { method: "POST", body: "{}" });
        toast("任务已停止，已完成步骤会保留");
      } catch (e) { toast(e.message, "err"); }
    };
    $("#btnRetryJob").onclick = async () => {
      try {
        const d = await api("/api/jobs/" + job.id + "/retry", { method: "POST", body: "{}" });
        state.job = d.job; renderJob(d.job); startPoll();
      } catch (e) { toast(e.message, "err"); }
    };

    const pct = Math.max(0, Math.min(100, Number(job.percent) || 0));
    $("#percentBadge").textContent = (job.status === "done" ? 100 : pct).toFixed(pct >= 99.95 ? 0 : 1).replace(/\.0$/, "") + "%";
    $("#barFill").style.width = (job.status === "done" ? 100 : pct) + "%";
    $("#barFill").style.opacity = job.status === "error" ? ".5" : "1";

    const steps = Array.isArray(job.steps) ? job.steps : [];
    $("#steps").innerHTML = steps.map((s) =>
      `<li class="${s.state}">${esc(s.label)}</li>`).join("");

    let msg = job.message || "";
    if (job.status === "running") msg = msg || "处理中…";
    if (job.status === "done") msg = "已完成";
    if (job.status === "partial") msg = job.message || "部分完成，请查看提示";
    if (job.status === "error") msg = "失败：" + (job.error || "未知错误");
    if (job.status === "cancelled") msg = "已取消";
    $("#stageMsg").textContent = msg;
    $("#elapsed").textContent = "已用：" + fmtDur(job.elapsed || 0);
    $("#eta").textContent = (job.status === "running" && job.eta) ? "预计剩余：" + fmtDur(job.eta) : "";

    const done = ["done", "partial", "error", "cancelled"].includes(job.status);
    $("#resultBox").hidden = !["done", "partial"].includes(job.status);
    if (["done", "partial"].includes(job.status)) {
      $("#savedPath").textContent = job.dir || "";
      const files = normFiles(job);
      $("#fileList").innerHTML = files.length ? files.map((f) =>
        `<div class="file-item"><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.name)}</a>${f.name.endsWith('.md') ? `<button class="btn btn-sm note-preview" data-url="${esc(f.url.replace('/file/', '/preview/'))}">预览笔记</button>` : ''}<span class="size">${fmtSize(f.size)}</span></div>`
      ).join("") : '<div class="hint">暂无文件</div>';
      $$(".note-preview").forEach((button) => button.addEventListener("click", () => {
        $("#notesPreview").src = button.dataset.url;
        showModal("modalPreview");
      }));
      const u = job.usage && (job.usage.usage || job.usage);
      $("#usage").textContent = u
        ? `API 用量：${u.input_tokens || 0} 输入 / ${u.output_tokens || 0} 输出 tokens`
        : "";
      $("#btnOpenFolder").onclick = () => openFolder(job.id);
    }

    if (Array.isArray(job.log) && job.log.length) {
      $("#log").textContent = job.log.join("\n");
      if (job.status === "error") $("#logBox").open = true;
    }
    $("#logBox").hidden = !(Array.isArray(job.log) && job.log.length);
    if (done) stopPoll();
  }

  function startPoll() {
    stopPoll();
    state.timer = setInterval(async () => {
      if (!state.job) return;
      try {
        const d = await api("/api/jobs/" + state.job.id);
        state.job = d.job;
        renderJob(d.job);
        if (["done", "partial", "error", "cancelled"].includes(d.job.status)) {
          stopPoll();
          loadHistory();
          if (d.job.status === "done") toast("笔记已生成，可在 outputs/ 下查看", "ok");
          if (d.job.status === "partial") toast(d.job.message || "部分完成", "err");
          if (d.job.status === "error") toast("任务失败：" + (d.job.error || ""), "err");
        }
      } catch (e) { /* 忽略瞬时错误 */ }
    }, 800);
  }
  function stopPoll() { if (state.timer) { clearInterval(state.timer); state.timer = null; } }

  async function openFolder(id) {
    try {
      const d = await api("/api/jobs/" + id + "/open-folder", { method: "POST", body: "{}" });
      if (!d.ok) toast("打开失败，路径：" + (d.dir || ""), "err");
    } catch (e) { toast("打开文件夹失败：" + e.message, "err"); }
  }

  async function confirmDelete(id, title, status) {
    const running = status === "running" || status === "queued";
    const msg = "确定删除任务「" + (title || id) + "」及其全部产物吗？\n\n" +
      (running ? "该任务正在运行，会先停止进程。\n" : "") +
      "将永久删除 outputs/ 下该任务目录（笔记、转写、抽帧等全部文件），不可恢复。";
    const ok = await askConfirm("删除任务", msg, "确认删除");
    if (!ok) return;
    try {
      const d = await api("/api/jobs/" + id, { method: "DELETE" });
      if (!d.ok) throw new Error(d.detail || "目录被占用，删除未完成");
      toast("任务已删除：" + (d.dir || id), "ok");
      if (state.job && state.job.id === id) {
        stopPoll();
        state.job = null;
        $("#jobPanel").hidden = true;
        $("#jobEmpty").hidden = false;
      }
      loadHistory();
    } catch (e) {
      toast("删除失败：" + e.message, "err");
    }
  }

  /* ---------------- 历史 ---------------- */
  async function loadHistory() {
    try {
      const d = await api("/api/jobs");
      state.history = d.jobs || [];
      const box = $("#history");
      if (!d.jobs || !d.jobs.length) {
        box.innerHTML = '<div class="history-empty">还没有历史任务</div>';
        return;
      }
      box.innerHTML = d.jobs.map((j) => `
        <div class="history-row" data-id="${esc(j.id)}">
          <span class="badge ${esc(j.status)}">${{ done: "完成", partial: "部分完成", running: "运行中", error: "失败", queued: "排队", cancelled: "已取消" }[j.status] || j.status}</span>
          <span class="name" title="${esc(j.title)}">${esc(j.title || j.input)}</span>
          <span class="badge mode">${{ single: "单视频", separate: "分别", merged: "整合" }[j.mode] || j.mode}</span>
          <span class="meta">${j.status === "running" ? (Number(j.percent) || 0).toFixed(0) + "%" : fmtTime(j.started)}</span>
          <span class="meta">
            <button class="btn btn-sm hist-view">查看</button>
            <button class="btn btn-sm hist-open">打开目录</button>
            <button class="btn btn-sm btn-danger hist-del" title="删除该任务及其全部产物">删除</button>
          </span>
        </div>`).join("");
      $$(".history-row").forEach((row) => {
        const id = row.dataset.id;
        row.querySelector(".hist-view").addEventListener("click", () => viewHistory(id));
        row.querySelector(".hist-open").addEventListener("click", () => openFolder(id));
        row.querySelector(".hist-del").addEventListener("click", () => {
          const job = (state.history || []).find((x) => x.id === id) || {};
          confirmDelete(id, job.title || id, job.status);
        });
      });
    } catch (e) { /* ignore */ }
  }

  async function viewHistory(id) {
    try {
      const d = await api("/api/jobs/" + id);
      state.job = d.job;
      renderJob(d.job);
      if (d.job.status === "running") startPoll();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) { toast("读取任务失败：" + e.message, "err"); }
  }

  /* ---------------- 设置 ---------------- */
  async function openSettings() {
    try {
      const d = await api("/api/config");
      $("#setKey").value = "";
      $("#setModel").value = d.model || "";
      $("#setBase").value = d.baseUrl || "";
      $("#keyHint").textContent = d.hasKey
        ? "已配置 API Key（来源：" + (d.keySource || "unknown") + "）"
        : "尚未配置 API Key，请填写后保存";
      showModal("modalSettings");
    } catch (e) { toast("读取配置失败：" + e.message, "err"); }
  }

  async function saveSettings() {
    const body = {};
    const k = $("#setKey").value.trim();
    if (k) body.apiKey = k;
    body.model = $("#setModel").value.trim();
    body.baseUrl = $("#setBase").value.trim();
    try {
      await api("/api/config", { method: "POST", body: JSON.stringify(body) });
      hideModal("modalSettings");
      toast("设置已保存", "ok");
      refreshCookiePill();
    } catch (e) { toast("保存失败：" + e.message, "err"); }
  }

  /* ---------------- 扫码登录 ---------------- */
  let qrKey = null, qrTimer = null, qrGeneration = 0;
  async function openQr() {
    showModal("modalQr");
    startQr();
  }
  async function startQr() {
    const generation = ++qrGeneration;
    clearInterval(qrTimer);
    qrKey = null;
    $("#qrImg").hidden = true;
    $("#qrLoading").hidden = false;
    $("#qrLoading").textContent = "正在生成二维码…";
    $("#qrMsg").textContent = "用哔哩哔哩手机客户端扫一扫";
    try {
      const d = await api("/api/login/qr", { method: "POST", body: "{}" });
      if (generation !== qrGeneration) return;
      qrKey = d.session;
      $("#qrImg").src = d.qrcode || "";
      $("#qrImg").hidden = !d.qrcode;
      $("#qrLoading").hidden = true;
      qrTimer = setInterval(pollQr, 2000);
    } catch (e) {
      if (generation !== qrGeneration) return;
      $("#qrLoading").textContent = "生成失败：" + e.message;
    }
  }
  async function pollQr() {
    if (!qrKey) return;
    const key = qrKey;
    try {
      const d = await api("/api/login/qr?key=" + encodeURIComponent(qrKey));
      if (key !== qrKey) return;
      if (d.status === "ok" || d.status === "success") {
        clearInterval(qrTimer);
        $("#qrMsg").textContent = d.message || "登录凭据已保存";
        toast(d.message || "登录凭据已保存", "ok");
        refreshCookiePill();
        setTimeout(() => { if (key === qrKey) hideModal("modalQr"); }, 1200);
      } else if (d.status === "scanned") {
        $("#qrMsg").textContent = d.message || "已扫码，请在手机上确认";
      } else if (d.status === "expired" || d.status === "error") {
        clearInterval(qrTimer);
        $("#qrMsg").textContent = d.message || "二维码已过期，请刷新";
      } else {
        $("#qrMsg").textContent = d.message || "等待扫码…";
      }
    } catch (e) { /* ignore */ }
  }

  async function refreshCookiePill() {
    try {
      const d = await api("/api/cookies/status");
      const pill = $("#cookiePill");
      pill.title = d.message || "";
      if (d.status === "valid") {
        pill.className = "pill pill-ok";
        pill.textContent = "B站已登录" + (d.username ? " · " + d.username : "") + (d.expiringSoon ? "（即将到期，请重新扫码）" : "");
      } else {
        pill.className = "pill pill-warn";
        pill.textContent = d.status === "unknown" ? "B站登录待验证（凭据已保存）" : (d.message || "B站未登录");
      }
    } catch (e) { /* ignore */ }
  }

  async function importChrome() {
    toast("正在尝试从 Chrome 读取 cookie…");
    try {
      const d = await api("/api/cookies/import", { method: "POST", body: JSON.stringify({}) });
      if (d.ok) {
        toast("已从 Chrome 导入 cookie", "ok");
        refreshCookiePill();
      } else {
        toast(d.hint || "导入失败", "err");
      }
    } catch (e) { toast("导入失败：" + e.message, "err"); }
  }

  /* ---------------- 文件浏览 ---------------- */
  function openFs(i) {
    state.fsRow = i;
    $("#fsHeading").textContent = state.sources[i]?.type === 'directory' ? '选择视频目录' : '选择本地视频';
    $("#btnSelectDirectory").hidden = state.sources[i]?.type !== 'directory';
    showModal("modalFs");
    loadFs(state.sources[i]?.type === 'directory' && state.sources[i].input ? state.sources[i].input : state.fsPath || "");
  }
  async function loadFs(path) {
    try {
      const d = await api("/api/fs?path=" + encodeURIComponent(path || ""));
      state.fsPath = d.path;
      $("#fsPath").value = d.path;
      const drives = $("#fsDrives");
      drives.innerHTML = (d.drives || []).map((x) => `<option value="${esc(x)}" ${x === d.path ? "selected" : ""}>${esc(x)}</option>`).join("");
      $("#fsDirs").innerHTML = (d.dirs || []).map((n) => `<div class="fs-item" data-dir="${esc(n)}">📁 ${esc(n)}</div>`).join("") || '<span class="hint">（无子目录）</span>';
      $("#fsFiles").innerHTML = (d.files || []).map((f) => `<div class="fs-item file" data-file="${esc(f.name)}">🎞 ${esc(f.name)} <span class="hint">${fmtSize(f.size)}</span></div>`).join("") || '<span class="hint">（本目录没有视频文件）</span>';
      $$("#fsDirs .fs-item").forEach((el) => el.addEventListener("click", () => {
        const p = state.fsPath.replace(/[\\/]+$/, "") + "\\" + el.dataset.dir;
        loadFs(p);
      }));
      $$("#fsFiles .fs-item").forEach((el) => el.addEventListener("click", () => {
        if (state.sources[state.fsRow]?.type === 'directory') return;
        const full = state.fsPath.replace(/[\\/]+$/, "") + "\\" + el.dataset.file;
        const row = state.sources[state.fsRow];
        if (row) {
          row.type = "local";
          row.input = full;
          row.title = el.dataset.file.replace(/\.[^.]+$/, "");
          renderSources();
        }
        hideModal("modalFs");
      }));
    } catch (e) { toast("读取目录失败：" + e.message, "err"); }
  }

  /* ---------------- 模态框 ---------------- */
  function showModal(id) { const m = $("#" + id); if (m) m.hidden = false; }
  function hideModal(id) {
    const m = $("#" + id); if (m) m.hidden = true;
    if (id === "modalQr") { clearInterval(qrTimer); qrKey = null; ++qrGeneration; }
  }

  /* ---------------- 初始化 ---------------- */
  function init() {
    addSource("bili");
    $("#btnAddSource").addEventListener("click", () => addSource("bili"));
    $("#btnAddCollection").addEventListener("click", () => addSource("collection"));
    $("#btnAddDirectory").addEventListener("click", () => addSource("directory"));
    $("#btnSelectDirectory").addEventListener('click', () => {
      const source = state.sources[state.fsRow];
      if (source && !state.submitting) {
        source.input = state.fsPath; source.title = ''; source.items = null; source.error = '';
        source.revision = (source.revision || 0) + 1;
        renderSources(); hideModal('modalFs');
      }
    });
    $("#btnSubmit").addEventListener("click", submit);
    $("#btnSettings").addEventListener("click", openSettings);
    $("#btnSaveSettings").addEventListener("click", saveSettings);
    $("#btnQr").addEventListener("click", openQr);
    $("#btnQrRefresh").addEventListener("click", startQr);
    $("#btnImport").addEventListener("click", importChrome);
    $("#fsUp").addEventListener("click", () => {
      const p = state.fsPath.replace(/[\\/]+[^\\/]+$/, "") || "";
      loadFs(p);
    });
    $("#fsDrives").addEventListener("change", (e) => loadFs(e.target.value));
    $("#confirmOk").addEventListener("click", () => closeConfirm(true));
    $("#confirmCancel").addEventListener("click", () => closeConfirm(false));
    $$("[data-close]").forEach((b) => b.addEventListener("click", () => {
      const m = b.closest(".modal");
      if (m && m.id === "modalConfirm") closeConfirm(false);
      else if (m) hideModal(m.id);
    }));
    $$(".modal").forEach((m) => m.addEventListener("click", (e) => {
      if (e.target !== m) return;
      if (m.id === "modalConfirm") closeConfirm(false);
      else hideModal(m.id);
    }));
    refreshCookiePill();
    setInterval(refreshCookiePill, 300000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshCookiePill(); });
    loadHistory();
  }

  document.addEventListener("DOMContentLoaded", init);
})();

/* ============================================================
   DST MC I18n Tool — 前端逻辑
   纯原生 JavaScript：无第三方库、无 CDN、无外网请求
   仅访问同源 /api/*
   ============================================================ */
(function () {
  'use strict';

  /* ---------------- 常量 ---------------- */
  var THEME_KEY = 'dst-i18n-theme';

  var TARGET_LANGS = [
    { value: 'zh_cn', label: '简体中文（zh_cn）' },
    { value: 'zh_tw', label: '繁體中文（zh_tw）' },
    { value: 'ja_jp', label: '日本語（ja_jp）' },
    { value: 'ko_kr', label: '한국어（ko_kr）' },
    { value: 'ru_ru', label: 'Русский（ru_ru）' }
  ];

  var MC_VERSIONS = ['1.16.5', '1.17', '1.18', '1.18.2', '1.19', '1.19.4', '1.20',
    '1.20.1', '1.20.4', '1.21', '1.21.4', '1.21.5'];

  var JOB_POLL_MS = 700;
  var SCAN_POLL_MS = 300;
  var ROW_LIMIT_STEP = 300;
  var SCAN_CANCEL_GRACE_MS = 8000;

  /* ---------------- 全局状态 ---------------- */
  var state = {
    theme: 'dark',
    page: 'mods',
    workspace: null,
    settings: null,
    modsFilter: { q: '', onlyMissing: false },
    kubejsFilter: { q: '' },
    ftbFilter: { q: '' },
    currentMod: null,
    currentDesc: null,
    doc: null,
    targets: {},
    kv: { q: '', onlyUntranslated: false, selected: {}, limit: ROW_LIMIT_STEP },
    translateMode: 'llm',
    job: { id: null, timer: null, data: null, docId: null, modalShown: {}, staged: {}, stagedDocId: null },
    scan: { id: null, timer: null, path: '', name: '', data: null, cancelling: false, cancelDeadline: 0, locked: false },
    browse: { path: '', parent: null, isRoot: true, selected: '', drives: [], dirs: [], suggestions: [] },
    pack: { mode: 'resourcepack', kind: null, desc: null, files: null, scope: 'all' },
    pickFile: { kind: 'kubejs', files: [], selected: '' },
    questionsJobId: null
  };

  var docCache = {};

  /* ---------------- 基础工具 ---------------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function icon(name, cls) {
    return '<svg class="icon ' + (cls || '') + '" viewBox="0 0 24 24" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
  }

  function val(sel) {
    var node = $(sel);
    return node ? String(node.value === undefined || node.value === null ? '' : node.value) : '';
  }

  function checked(sel) {
    var node = $(sel);
    return !!(node && node.checked);
  }

  function num(sel, fallback) {
    var n = parseInt(val(sel), 10);
    return isNaN(n) ? fallback : n;
  }

  function fmtNumber(n) {
    var v = Number(n || 0);
    return v.toLocaleString('zh-CN');
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments;
      var self = this;
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () { timer = null; fn.apply(self, args); }, wait);
    };
  }

  function has(obj, key) {
    return Object.prototype.hasOwnProperty.call(obj, key);
  }

  /* ---------------- HTTP ---------------- */
  function ApiError(message, status) {
    this.name = 'ApiError';
    this.message = message || '请求失败';
    this.status = status || 0;
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  async function api(path, opts) {
    opts = opts || {};
    var init = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined && opts.body !== null) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    var res;
    try {
      res = await fetch(path, init);
    } catch (err) {
      throw new ApiError('无法连接本地服务，请确认 DST MC I18n Tool 仍在运行', 0);
    }
    if (!res.ok) {
      var message = 'HTTP ' + res.status;
      var errorBody = null;
      try {
        var ct = res.headers.get('content-type') || '';
        if (ct.indexOf('json') >= 0) {
          errorBody = await res.json();
          message = errorBody.error || errorBody.message || errorBody.title || message;
        } else {
          var text = await res.text();
          if (text) message = text.slice(0, 300);
        }
      } catch (err2) { /* 保留状态码信息 */ }
      var apiErr = new ApiError(message, res.status);
      apiErr.body = errorBody;
      throw apiErr;
    }
    return res;
  }

  async function apiJson(path, opts) {
    var res = await api(path, opts);
    var ct = res.headers.get('content-type') || '';
    if (ct.indexOf('json') >= 0) return res.json();
    var text = await res.text();
    if (!text) return null;
    try { return JSON.parse(text); } catch (err) { return text; }
  }

  function fileNameFromDisposition(header, fallback) {
    if (!header) return fallback;
    var m = /filename\*=UTF-8''([^;]+)/i.exec(header);
    if (m) { try { return decodeURIComponent(m[1].trim()); } catch (e) { /* fallthrough */ } }
    m = /filename="?([^";]+)"?/i.exec(header);
    if (m) return m[1].trim();
    return fallback;
  }

  async function downloadResponse(res, fallbackName) {
    var blob = await res.blob();
    var name = fileNameFromDisposition(res.headers.get('content-disposition'), fallbackName || 'download.bin');
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      try { URL.revokeObjectURL(url); } catch (e) { /* ignore */ }
      if (a.parentNode) a.parentNode.removeChild(a);
    }, 2000);
    return name;
  }

  /* ---------------- 主题 ---------------- */
  function readStoredTheme() {
    try {
      var t = localStorage.getItem(THEME_KEY);
      return t === 'light' ? 'light' : 'dark';
    } catch (e) { return 'dark'; }
  }

  function applyTheme(theme) {
    state.theme = theme === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', state.theme);
    try { localStorage.setItem(THEME_KEY, state.theme); } catch (e) { /* ignore */ }
    $$('.theme-choice').forEach(function (btn) {
      btn.classList.toggle('active', btn.getAttribute('data-theme-value') === state.theme);
    });
  }

  function toggleTheme() {
    applyTheme(state.theme === 'dark' ? 'light' : 'dark');
  }

  /* ---------------- 提示条 ---------------- */
  var TOAST_ICONS = { success: 'check-circle', error: 'x-circle', warn: 'alert', info: 'info' };

  function toast(message, type, duration) {
    var stack = $('#toast-stack');
    if (!stack) return;
    var kind = TOAST_ICONS[type] ? type : 'info';
    var box = document.createElement('div');
    box.className = 'toast toast-' + kind;
    box.innerHTML = icon(TOAST_ICONS[kind]) +
      '<div class="toast-msg">' + escapeHtml(message) + '</div>' +
      '<button class="toast-close" type="button" title="关闭">×</button>';
    stack.appendChild(box);
    var close = function () {
      if (!box.parentNode) return;
      box.classList.add('leaving');
      setTimeout(function () { if (box.parentNode) box.parentNode.removeChild(box); }, 220);
    };
    box.querySelector('.toast-close').addEventListener('click', close);
    var ttl = duration === undefined ? (kind === 'error' ? 6000 : 3800) : duration;
    if (ttl > 0) setTimeout(close, ttl);
  }

  /* ---------------- 模态框 ---------------- */
  function openModal(id) {
    var box = document.getElementById(id);
    if (!box) return;
    box.hidden = false;
    var focusable = box.querySelector('input, select, textarea, button');
    if (focusable) setTimeout(function () { try { focusable.focus(); } catch (e) { /* ignore */ } }, 30);
  }

  function closeModal(id) {
    var box = document.getElementById(id);
    if (box) box.hidden = true;
  }

  function closeTopModal() {
    var open = $$('.overlay:not([hidden])');
    if (!open.length) return false;
    var top = open[open.length - 1];
    // 解析进行中时不允许关闭进度弹窗（必须先取消解析）
    if (top.id === 'overlay-scan' && isScanning()) {
      toast('正在解析中，请先取消当前解析再重新选择', 'warn');
      return false;
    }
    closeModal(top.id);
    return true;
  }

  function confirmDialog(title, text, okLabel) {
    return new Promise(function (resolve) {
      $('#confirm-title').textContent = title || '确认';
      $('#confirm-text').innerHTML = text || '';
      var ok = $('#confirm-ok');
      ok.textContent = okLabel || '确定';
      var done = function (result) {
        closeModal('overlay-confirm');
        ok.removeEventListener('click', onOk);
        resolve(result);
      };
      var onOk = function () { done(true); };
      ok.addEventListener('click', onOk);
      openModal('overlay-confirm');
    });
  }

  /* ---------------- 导航 ---------------- */
  function showPage(name, navName) {
    $$('.page').forEach(function (page) { page.hidden = true; });
    var target = document.getElementById('page-' + name);
    if (target) target.hidden = false;
    state.page = name;
    var active = navName || name;
    $$('.nav-item').forEach(function (item) {
      item.classList.toggle('active', item.getAttribute('data-page') === active);
    });
    var content = $('#content');
    if (content) content.scrollTop = 0;
  }

  /* ---------------- 工作目录 ---------------- */
  function hasZhCn(mod) {
    var files = (mod && mod.langFiles) || [];
    for (var i = 0; i < files.length; i++) if (files[i].hasZhCn) return true;
    return false;
  }

  function sourceLangFiles(files) {
    return (files || []).filter(function (f) { return String(f.lang || '').toLowerCase() !== 'zh_cn'; });
  }

  function setWorkspace(info) {
    state.workspace = info || null;
    if (info && info.path) {
      // 文档缓存按目录隔离，切换工作目录后必须失效
      docCache = {};
      if (state.doc) {
        state.doc = null;
        state.currentDesc = null;
        state.targets = {};
      }
    }
    renderWorkspaceChrome();
    renderModsPage();
    renderKubejsPage();
    renderFtbPage();
  }

  function renderWorkspaceChrome() {
    var ws = state.workspace;
    var path = ws && ws.path ? ws.path : (isScanning() ? state.scan.path : '');
    var chipPath = $('#workspace-path');
    if (chipPath) chipPath.textContent = path || '未选择工作目录';
    var chip = $('#workspace-chip');
    if (chip) chip.title = path ? (path + '（点击切换工作目录）') : '点击选择工作目录';
    var sidePath = $('#sidebar-workspace');
    if (sidePath) sidePath.textContent = path || '尚未选择';

    var modsCount = ws && ws.mods && ws.mods.mods ? ws.mods.mods.length : 0;
    var kubejsCount = ws && ws.kubejs && ws.kubejs.files ? ws.kubejs.files.length : 0;
    var ftbCount = ws && ws.ftb && ws.ftb.files ? ws.ftb.files.length : 0;
    setBadge('nav-badge-mods', modsCount);
    setBadge('nav-badge-kubejs', kubejsCount);
    setBadge('nav-badge-ftb', ftbCount);
  }

  function setBadge(id, count) {
    var node = document.getElementById(id);
    if (!node) return;
    if (count > 0) {
      node.hidden = false;
      node.textContent = String(count);
    } else {
      node.hidden = true;
      node.textContent = '';
    }
  }

  /* ---------------- 异步扫描 ---------------- */

  var SCAN_LOCK_IDS = [
    'btn-pick-folder', 'workspace-chip', 'btn-sidebar-pick', 'btn-sidebar-reveal', 'btn-sidebar-browse',
    'btn-empty-pick', 'btn-kubejs-pick', 'btn-ftb-pick',
    'btn-mods-refresh', 'btn-kubejs-refresh', 'btn-ftb-refresh', 'browse-confirm'
  ];

  function isScanning() {
    return !!(state.scan && state.scan.id);
  }

  /** 扫描期间禁用所有“选择目录 / 打开文件夹 / 切换目录”按钮（真正的 disabled 属性 + 视觉态）。 */
  function setScanLock(locked) {
    state.scan.locked = !!locked;
    document.body.classList.toggle('scan-locked', !!locked);
    SCAN_LOCK_IDS.forEach(function (id) {
      var node = document.getElementById(id);
      if (!node) return;
      if (locked) {
        if (!node.hasAttribute('data-lock-was-disabled')) {
          node.setAttribute('data-lock-was-disabled', node.disabled ? '1' : '0');
        }
        node.disabled = true;
        node.classList.add('is-scan-locked');
      } else {
        var was = node.getAttribute('data-lock-was-disabled');
        node.removeAttribute('data-lock-was-disabled');
        node.disabled = was === '1';
        node.classList.remove('is-scan-locked');
      }
    });
  }

  /** 扫描刚开始时立刻刷新顶部栏与侧边栏的目录文本（不等扫描结束）。 */
  function applyWorkspacePath(path, name) {
    var chipPath = $('#workspace-path');
    if (chipPath) chipPath.textContent = path || '未选择工作目录';
    var chip = $('#workspace-chip');
    if (chip) {
      chip.title = path ? (path + '（点击切换工作目录）') : '点击选择工作目录';
      if (name) chip.setAttribute('data-name', name);
    }
    var sidePath = $('#sidebar-workspace');
    if (sidePath) {
      sidePath.textContent = path || '尚未选择';
      sidePath.title = path || '';
    }
  }

  function fmtElapsed(ms) {
    var s = (Number(ms) || 0) / 1000;
    if (s < 60) return s.toFixed(1) + ' 秒';
    var m = Math.floor(s / 60);
    return m + ' 分 ' + (s - m * 60).toFixed(0) + ' 秒';
  }

  function renderScanProgress(data) {
    data = data || {};
    var status = data.status || 'running';
    var pct = Math.max(0, Math.min(100, Math.round(Number(data.percent) || 0)));
    var label = data.phaseLabel || (status === 'running' ? '正在解析…' : '解析完成');

    var title = $('#scan-title');
    if (title) {
      title.textContent = status === 'error' ? '解析工作目录失败'
        : (status === 'cancelled' ? '解析已取消' : (status === 'done' ? '解析完成' : '正在解析工作目录'));
    }
    var sub = $('#scan-sub');
    if (sub) sub.textContent = (data.path || state.scan.path || '—') + (data.name ? ('  ·  ' + data.name) : '');

    var phaseLabel = $('#scan-phase-label');
    if (phaseLabel) phaseLabel.textContent = label;
    var percentNode = $('#scan-percent');
    if (percentNode) percentNode.textContent = pct + '%';
    var fill = $('#scan-progress-fill');
    if (fill) {
      fill.style.width = pct + '%';
      fill.className = 'progress-fill' + (status === 'done' ? ' done' : (status === 'error' ? ' error' : ''));
    }
    var current = $('#scan-current-item');
    if (current) {
      current.textContent = data.currentItem ? data.currentItem : (status === 'running' ? '正在准备…' : '—');
      current.title = data.currentItem || '';
    }
    var elapsed = $('#scan-elapsed');
    if (elapsed) elapsed.textContent = '耗时 ' + fmtElapsed(data.elapsedMs);
    var mods = $('#scan-mods');
    if (mods) mods.textContent = '已发现 ' + fmtNumber(data.modsFound || 0) + ' 个模组';
    var countNode = $('#scan-count');
    if (countNode) {
      countNode.textContent = (data.total > 0) ? ((data.current || 0) + ' / ' + data.total) : '';
    }

    var errBox = $('#scan-error');
    if (errBox) {
      if (status === 'error') {
        errBox.hidden = false;
        errBox.innerHTML = icon('alert') + '<span>' + escapeHtml(data.error || '未知错误') + '</span>';
      } else {
        errBox.hidden = true;
        errBox.innerHTML = '';
      }
    }

    var warnBox = $('#scan-warnings');
    var warnings = data.warnings || [];
    if (warnBox) {
      if (warnings.length && status !== 'running') {
        warnBox.hidden = false;
        warnBox.innerHTML = warnings.slice(0, 6).map(function (w) { return '<div>' + escapeHtml(w) + '</div>'; }).join('');
      } else {
        warnBox.hidden = true;
        warnBox.innerHTML = '';
      }
    }

    var cancelBtn = $('#btn-scan-cancel');
    if (cancelBtn) {
      cancelBtn.hidden = status !== 'running';
      cancelBtn.disabled = !!state.scan.cancelling;
      cancelBtn.textContent = state.scan.cancelling ? '正在取消…' : '取消解析';
    }
    var retryBtn = $('#btn-scan-retry');
    if (retryBtn) retryBtn.hidden = status !== 'error';
    var closeBtn = $('#btn-scan-close');
    if (closeBtn) closeBtn.hidden = status !== 'error';
    var footInfo = $('#scan-foot-info');
    if (footInfo) {
      footInfo.textContent = status === 'running'
        ? '解析期间无法切换目录，可随时取消'
        : (status === 'error' ? '可重试，或关闭后重新选择目录' : '');
    }
  }

  function startScanPolling() {
    stopScanPolling();
    state.scan.timer = setInterval(pollScan, SCAN_POLL_MS);
    pollScan();
  }

  function stopScanPolling() {
    if (state.scan.timer) {
      clearInterval(state.scan.timer);
      state.scan.timer = null;
    }
  }

  async function pollScan() {
    var id = state.scan.id;
    if (!id) { stopScanPolling(); return; }
    if (state.scan.cancelDeadline && Date.now() > state.scan.cancelDeadline) {
      finishScanCancelled('取消解析超时，已断开本次解析');
      return;
    }
    var data;
    try {
      data = await apiJson('/api/workspace/scan/' + encodeURIComponent(id));
    } catch (err) {
      if (err.status === 404 || err.status === 405) {
        // 服务端已无该扫描（例如重启），视为取消
        finishScanCancelled('解析任务已结束，请重新选择目录');
        return;
      }
      stopScanPolling();
      state.scan.id = null;
      state.scan.cancelling = false;
      state.scan.cancelDeadline = 0;
      state.scan.data = Object.assign({}, state.scan.data || {}, {
        scanId: id, path: state.scan.path, status: 'error', error: err.message
      });
      setScanLock(false);
      renderScanProgress(state.scan.data);
      return;
    }
    if (!data || !data.scanId) data = Object.assign({ scanId: id, path: state.scan.path }, data || {});
    state.scan.data = data;
    if (data.path) state.scan.path = data.path;
    if (data.name) state.scan.name = data.name;
    renderScanProgress(data);

    if (data.status === 'done') { completeScan(data); return; }
    if (data.status === 'cancelled') { finishScanCancelled(); return; }
    if (data.status === 'error') {
      stopScanPolling();
      state.scan.id = null;
      state.scan.cancelling = false;
      state.scan.cancelDeadline = 0;
      setScanLock(false);
      return;
    }
  }

  async function completeScan(progress) {
    stopScanPolling();
    state.scan.id = null;
    state.scan.cancelling = false;
    state.scan.cancelDeadline = 0;
    state.scan.data = null;
    setScanLock(false);
    closeModal('overlay-scan');

    var info = null;
    try {
      info = await apiJson('/api/workspace');
    } catch (err) {
      toast('读取工作目录数据失败：' + err.message, 'error');
      return;
    }
    if (info && info.path) {
      setWorkspace(info);
      var warnings = (info.warnings && info.warnings.length) ? info.warnings : ((progress && progress.warnings) || []);
      var warn = warnings.length ? warnings[0] : '';
      var modsCount = (info.mods && info.mods.mods) ? info.mods.mods.length : 0;
      toast(warn || ('解析完成：' + (info.name || info.path) + '（' + fmtNumber(modsCount) + ' 个模组）'), warn ? 'warn' : 'success');
    } else {
      setWorkspace(null);
      toast('扫描完成，但未获取到工作目录信息', 'warn');
    }
  }

  function finishScanCancelled(message) {
    stopScanPolling();
    state.scan.id = null;
    state.scan.cancelling = false;
    state.scan.cancelDeadline = 0;
    state.scan.data = null;
    setScanLock(false);
    closeModal('overlay-scan');
    if (state.workspace && state.workspace.path) {
      renderWorkspaceChrome();
    } else {
      applyWorkspacePath('', '');
      setWorkspace(null);
    }
    toast(message || '已取消解析', 'info');
  }

  async function cancelScan() {
    var id = state.scan.id;
    if (!id) return;
    state.scan.cancelling = true;
    state.scan.cancelDeadline = Date.now() + SCAN_CANCEL_GRACE_MS;
    renderScanProgress(state.scan.data || { status: 'running' });
    try {
      await api('/api/workspace/scan/' + encodeURIComponent(id) + '/cancel', { method: 'POST' });
    } catch (err) {
      if (err.status === 404 || err.status === 405) {
        finishScanCancelled('已取消解析');
        return;
      }
      toast('取消解析失败：' + err.message, 'warn');
      state.scan.cancelDeadline = 0;
      state.scan.cancelling = false;
      renderScanProgress(state.scan.data || { status: 'running' });
    }
  }

  function retryScan() {
    var path = state.scan.path;
    if (!path) { finishScanCancelled('请重新选择目录'); return; }
    closeModal('overlay-scan');
    state.scan.id = null;
    state.scan.cancelling = false;
    state.scan.cancelDeadline = 0;
    state.scan.data = null;
    setScanLock(false);
    startScan(path);
  }

  /** 目录不合法（400）：展示候选目录，让用户直接点选，不进入进度界面。 */
  function showInvalidWorkspace(message, candidates, attemptedPath) {
    var box = $('#ws-invalid-text');
    if (box) {
      box.innerHTML = escapeHtml(message || '选择的目录不是有效的 Minecraft / 整合包目录') +
        (attemptedPath ? '<br/><span class="mono hint">' + escapeHtml(attemptedPath) + '</span>' : '');
    }
    var host = $('#ws-invalid-candidates');
    var wrap = $('#ws-invalid-suggest');
    var list = (candidates || []).filter(function (c) { return !!c; });
    if (host && wrap) {
      if (list.length) {
        wrap.hidden = false;
        host.innerHTML = list.map(function (c) {
          return '<button type="button" class="suggest-chip" data-candidate="' + escapeHtml(c) + '" title="' + escapeHtml(c) + '">' +
            escapeHtml(shortPath(c)) + '</button>';
        }).join('');
      } else {
        wrap.hidden = true;
        host.innerHTML = '';
      }
    }
    openModal('overlay-ws-invalid');
  }

  async function startScan(path) {
    if (!path) return null;
    if (isScanning()) {
      toast('正在解析中，请先取消当前解析再重新选择', 'warn');
      return null;
    }

    var data;
    try {
      data = await apiJson('/api/workspace/scan', { method: 'POST', body: { path: path } });
    } catch (err) {
      if (err.status === 400) {
        var cands = (err.body && err.body.candidates) || [];
        showInvalidWorkspace(err.message, cands, path);
        return null;
      }
      if (err.status === 409) {
        var existingId = err.body && err.body.scanId;
        if (existingId) {
          toast('已有一项解析正在进行，已接上进度', 'info');
          state.scan.id = existingId;
          state.scan.path = (err.body && err.body.path) || path;
          state.scan.cancelling = false;
          state.scan.cancelDeadline = 0;
          setScanLock(true);
          renderScanProgress({ scanId: existingId, path: state.scan.path, status: 'running', phaseLabel: '正在解析…' });
          openModal('overlay-scan');
          startScanPolling();
          return null;
        }
        toast('已有解析正在进行：' + err.message, 'warn');
        return null;
      }
      if (err.status === 404 || err.status === 405) {
        // 旧版后端：退回同步 /api/workspace/open
        return await openWorkspacePathLegacy(path);
      }
      toast('打开工作目录失败：' + err.message, 'error');
      return null;
    }

    if (!data || !data.scanId) { toast('服务未返回扫描任务 ID', 'error'); return null; }

    state.scan.id = data.scanId;
    state.scan.path = data.path || path;
    state.scan.name = data.name || '';
    state.scan.data = null;
    state.scan.cancelling = false;
    state.scan.cancelDeadline = 0;

    // 立刻更新顶部栏 / 侧边栏
    applyWorkspacePath(state.scan.path, state.scan.name);
    setScanLock(true);
    renderScanProgress({
      scanId: data.scanId,
      path: state.scan.path,
      name: state.scan.name,
      status: 'running',
      phase: 'prepare',
      phaseLabel: '正在准备…',
      percent: 0,
      elapsedMs: 0,
      modsFound: 0,
      currentItem: ''
    });
    openModal('overlay-scan');
    startScanPolling();
    return data;
  }

  async function openWorkspacePathLegacy(path) {
    try {
      var info = await apiJson('/api/workspace/open', { method: 'POST', body: { path: path } });
      setWorkspace(info);
      var warn = (info && info.warnings && info.warnings.length) ? info.warnings[0] : '';
      toast(warn || ('已打开：' + ((info && info.name) || path)), warn ? 'warn' : 'success');
      return info;
    } catch (err) {
      toast('打开工作目录失败：' + err.message, 'error');
      return null;
    }
  }

  async function openWorkspacePath(path) {
    if (!path) return null;
    if (isScanning()) {
      toast('正在解析中，请先取消当前解析再重新选择', 'warn');
      return null;
    }
    return await startScan(path);
  }

  async function revealWorkspace() {
    if (isScanning()) {
      toast('正在解析中，请先取消当前解析再重新选择', 'warn');
      return;
    }
    var path = (state.workspace && state.workspace.path) || state.scan.path;
    if (!path) { toast('还没有选择工作目录', 'warn'); return; }
    try {
      var res = await apiJson('/api/workspace/reveal', { method: 'POST', body: { path: path } });
      if (!res || !res.ok) toast('无法打开资源管理器：' + ((res && res.error) || '未知错误'), 'error');
    } catch (err) {
      toast('无法打开资源管理器：' + err.message, 'error');
    }
  }

  async function refreshWorkspace() {
    if (isScanning()) {
      toast('正在解析中，请先取消当前解析再重新选择', 'warn');
      return;
    }
    var path = (state.workspace && state.workspace.path) || state.scan.path;
    if (path) { await startScan(path); return; }
    try {
      var info = await apiJson('/api/workspace/refresh', { method: 'POST' });
      setWorkspace(info);
      toast('已重新扫描工作目录', 'success');
    } catch (err) {
      toast('刷新失败：' + err.message, 'error');
    }
  }

  async function pickWorkspaceFolder() {
    if (isScanning()) {
      toast('正在解析中，请先取消当前解析再重新选择', 'warn');
      return false;
    }
    toast('正在打开系统文件夹选择框…', 'info', 2400);
    try {
      var result = await apiJson('/api/workspace/pick', { method: 'POST' });
      if (result && result.path) {
        await openWorkspacePath(result.path);
        return true;
      }
      // 系统对话框不可用（例如无桌面环境）→ 回退内置目录浏览器
      if (result && result.error) {
        toast('系统选择框不可用，已切换到内置目录浏览器', 'warn');
        openBrowseModal();
        return false;
      }
      // 用户取消：保持当前状态
      return true;
    } catch (err) {
      // 系统对话框不可用（无桌面环境 / 接口未实现）→ 回退内置目录浏览器
      openBrowseModal();
      return false;
    }
  }

  /* ---------------- 空状态 ---------------- */
  function fillEmpty(id, iconName, title, desc) {
    var box = document.getElementById(id);
    if (!box) return;
    var h2 = box.querySelector('h2');
    if (h2) h2.textContent = title;
    var p = box.querySelector('p');
    if (p) p.innerHTML = desc;
    var use = box.querySelector('.empty-illustration use');
    if (use) use.setAttribute('href', '#i-' + iconName);
  }


  /* ============================================================
     模组本地化页
     ============================================================ */
  function statCard(iconName, tone, value, label) {
    return '<div class="stat-card">' +
      '<div class="stat-icon ' + (tone || '') + '">' + icon(iconName) + '</div>' +
      '<div><div class="stat-value">' + escapeHtml(value) + '</div>' +
      '<div class="stat-label">' + escapeHtml(label) + '</div></div>' +
      '</div>';
  }

  function renderWorkspaceBanner(container, ws) {
    var existing = container.querySelector('.ws-banner');
    if (existing) existing.parentNode.removeChild(existing);
    if (!ws) return;
    var warnings = ws.warnings || [];
    var candidates = ws.candidates || [];
    if (!warnings.length && !candidates.length) return;
    var box = document.createElement('div');
    box.className = 'notice ws-banner';
    box.style.marginBottom = '14px';
    var html = icon('alert') + '<div><div>' +
      warnings.map(function (w) { return escapeHtml(w); }).join('<br/>') + '</div>';
    if (candidates.length) {
      html += '<div class="browse-suggest"><span class="browse-suggest-label">可能的版本目录：</span>' +
        candidates.map(function (c) {
          return '<button type="button" class="suggest-chip" data-candidate="' + escapeHtml(c) + '">' + escapeHtml(shortPath(c)) + '</button>';
        }).join('') + '</div>';
    }
    html += '</div>';
    box.innerHTML = html;
    box.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-candidate]');
      if (btn) openWorkspacePath(btn.getAttribute('data-candidate'));
    });
    container.insertBefore(box, container.firstChild);
  }

  function shortPath(p) {
    var text = String(p || '');
    if (text.length <= 44) return text;
    return '…' + text.slice(text.length - 43);
  }

  function renderModsPage() {
    var ws = state.workspace;
    var empty = $('#mods-empty');
    var body = $('#mods-body');
    var detail = $('#mod-detail');
    if (!empty || !body || !detail) return;

    detail.hidden = true;
    state.currentMod = null;

    if (!ws || !ws.path) {
      fillEmpty('mods-empty', 'folder-open', '还没有选择工作目录',
        '选择一个整合包或 <code>.minecraft</code> 目录，工具会自动扫描 <code>mods/</code>、<code>kubejs/</code> 与 <code>config/ftbquests/</code>。');
      empty.hidden = false;
      body.hidden = true;
      return;
    }

    var mods = (ws.mods && ws.mods.mods) || [];
    if (!ws.mods || !ws.mods.found) {
      var reason = ws.modsDir ? ('已找到目录 ' + ws.modsDir + '，但没有解析到可用模组。') :
        '当前目录下没有 <code>mods</code> 文件夹。请确认选择的是整合包或版本隔离目录。';
      fillEmpty('mods-empty', 'package', '未找到可本地化的模组', reason);
      empty.hidden = false;
      body.hidden = true;
      return;
    }

    empty.hidden = true;
    body.hidden = false;
    renderWorkspaceBanner(body, ws);

    var totalEntries = 0, translated = 0, missing = 0;
    mods.forEach(function (m) {
      totalEntries += m.entryCount || 0;
      translated += m.translatedCount || 0;
      if (!hasZhCn(m)) missing += 1;
    });

    $('#mods-stats').innerHTML =
      statCard('package', '', fmtNumber(mods.length), '模组总数') +
      statCard('list', '', fmtNumber(totalEntries), '可翻译条目') +
      statCard('check-circle', 'ok', fmtNumber(translated), '已翻译条目') +
      statCard('alert', missing > 0 ? 'warn' : 'ok', fmtNumber(missing), '缺少 zh_cn');

    renderModGrid(mods);
  }

  function renderModGrid(mods) {
    var q = state.modsFilter.q.trim().toLowerCase();
    var list = mods.filter(function (m) {
      if (state.modsFilter.onlyMissing && hasZhCn(m)) return false;
      if (!q) return true;
      var hay = ((m.name || '') + ' ' + (m.modId || '') + ' ' + (m.fileName || '')).toLowerCase();
      return hay.indexOf(q) >= 0;
    });

    var grid = $('#mod-grid');
    var emptyHint = $('#mods-list-empty');
    if (!list.length) {
      grid.innerHTML = '';
      if (emptyHint) emptyHint.hidden = false;
      return;
    }
    if (emptyHint) emptyHint.hidden = true;

    grid.innerHTML = list.map(function (m) {
      var files = sourceLangFiles(m.langFiles);
      var initial = (m.name || m.fileName || '?').trim().charAt(0) || '?';
      var tone = m.error ? 'warn' : (hasZhCn(m) ? 'alt' : '');
      var percent = m.entryCount > 0 ? Math.round(((m.translatedCount || 0) / m.entryCount) * 100) : 0;
      var badges = '';
      if (m.loader) badges += '<span class="badge badge-mono">' + escapeHtml(m.loader) + '</span>';
      if (m.version) badges += '<span class="badge badge-mono">' + escapeHtml(m.version) + '</span>';
      if (hasZhCn(m)) badges += '<span class="badge badge-ok">' + icon('check') + '已有 zh_cn</span>';
      else badges += '<span class="badge badge-warn">缺少 zh_cn</span>';
      if (m.error) badges += '<span class="badge badge-danger">解析异常</span>';
      if (!m.enabled) badges += '<span class="badge">已禁用</span>';
      return '<div class="mod-card' + (m.error ? ' is-error' : '') + '" tabindex="0" role="button" data-mod="' + escapeHtml(m.fileName) + '">' +
        '<div class="mod-card-head">' +
          '<div class="mod-avatar ' + tone + '">' + escapeHtml(initial) + '</div>' +
          '<div class="mod-title-wrap">' +
            '<div class="mod-title">' + escapeHtml(m.name || m.fileName || '未知模组') + '</div>' +
            '<div class="mod-id">' + escapeHtml(m.modId || m.fileName || '') + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="mod-badges">' + badges + '</div>' +
        '<div class="mod-meta">' +
          '<span>' + icon('list') + escapeHtml(files.length) + ' 个语言文件</span>' +
          '<span>' + icon('layers') + escapeHtml(fmtNumber(m.entryCount || 0)) + ' 条</span>' +
        '</div>' +
        '<div class="mod-card-foot">' +
          '<div class="mod-progress">' +
            '<div class="mod-progress-bar"><div class="mod-progress-fill" style="width:' + percent + '%"></div></div>' +
            '<span>' + percent + '%</span>' +
          '</div>' +
          '<span>' + (files.length ? '点击查看' : '无源语言文件') + '</span>' +
        '</div>' +
        '</div>';
    }).join('');
  }

  function findMod(fileName) {
    var ws = state.workspace;
    var mods = (ws && ws.mods && ws.mods.mods) || [];
    for (var i = 0; i < mods.length; i++) {
      if (String(mods[i].fileName).toLowerCase() === String(fileName).toLowerCase()) return mods[i];
    }
    return null;
  }

  function openModDetail(fileName) {
    var mod = findMod(fileName);
    if (!mod) { toast('未找到该模组', 'error'); return; }
    state.currentMod = mod;

    var nameNode = $('#mod-detail-name');
    if (nameNode) nameNode.textContent = mod.name || mod.fileName;

    var files = sourceLangFiles(mod.langFiles);
    var badges = '';
    if (mod.modId) badges += '<span class="badge badge-mono">' + escapeHtml(mod.modId) + '</span>';
    if (mod.version) badges += '<span class="badge badge-mono">' + escapeHtml(mod.version) + '</span>';
    if (mod.loader) badges += '<span class="badge">' + escapeHtml(mod.loader) + '</span>';
    if (mod.isFolder) badges += '<span class="badge">文件夹模组</span>';
    if (hasZhCn(mod)) badges += '<span class="badge badge-ok">' + icon('check') + '已有 zh_cn</span>';
    else badges += '<span class="badge badge-warn">缺少 zh_cn</span>';

    $('#mod-hero').innerHTML =
      '<div class="mod-hero-avatar">' + escapeHtml(((mod.name || mod.fileName || '?').trim().charAt(0) || '?')) + '</div>' +
      '<div class="mod-hero-main">' +
        '<h2>' + escapeHtml(mod.name || mod.fileName) + '</h2>' +
        '<p>' + escapeHtml(mod.fileName) + (mod.path ? ' · ' + escapeHtml(shortPath(mod.path)) : '') + '</p>' +
        '<div class="mod-hero-badges">' + badges + '</div>' +
      '</div>';

    var listNode = $('#mod-files');
    if (!files.length) {
      listNode.innerHTML = '<div class="list-empty">该模组没有可用的源语言文件（可能只内置了中文，或 jar 内没有 assets/&lt;ns&gt;/lang）。</div>';
    } else {
      listNode.innerHTML = files.map(function (f) {
        var zh = f.hasZhCn ? '<span class="badge badge-ok">' + icon('check') + 'zh_cn</span>' : '<span class="badge badge-warn">无中文</span>';
        return '<div class="file-row" data-file="' + escapeHtml(mod.fileName) + '" data-ns="' + escapeHtml(f.namespace) + '" data-lang="' + escapeHtml(f.lang) + '" role="button" tabindex="0">' +
          '<div class="file-icon">' + icon('book') + '</div>' +
          '<div class="file-main">' +
            '<div class="file-name">' + escapeHtml(f.namespace) + ' · ' + escapeHtml(f.lang) + '.json</div>' +
            '<div class="file-path">' + escapeHtml(f.archivePath || '') + '</div>' +
          '</div>' +
          '<div class="file-badges">' + zh +
            '<span class="badge badge-mono">' + escapeHtml(fmtNumber(f.entryCount || 0)) + ' 条</span>' +
          '</div>' +
          '<div class="file-action"><button type="button" class="btn btn-sm btn-ghost file-open">打开</button></div>' +
          '</div>';
      }).join('');
    }

    $('#mods-empty').hidden = true;
    $('#mods-body').hidden = true;
    $('#mod-detail').hidden = false;
  }

  function backToModList() {
    var detail = $('#mod-detail');
    if (detail) detail.hidden = true;
    state.currentMod = null;
    if (!state.workspace || !state.workspace.path) { renderModsPage(); return; }
    var body = $('#mods-body');
    if (body) body.hidden = false;
    var empty = $('#mods-empty');
    if (empty) empty.hidden = true;
  }

  /* ============================================================
     KubeJS / FTB 列表页
     ============================================================ */
  function renderKubejsPage() {
    var ws = state.workspace;
    var empty = $('#kubejs-empty');
    var body = $('#kubejs-body');
    if (!empty || !body) return;

    if (!ws || !ws.path) {
      fillEmpty('kubejs-empty', 'folder-open', '还没有选择工作目录', '选择整合包目录后，这里会列出 <code>kubejs/assets/**/lang</code> 下的语言文件。');
      empty.hidden = false;
      body.hidden = true;
      return;
    }
    var info = ws.kubejs || {};
    var files = sourceLangFiles(info.files);
    if (!info.found || !files.length) {
      var desc = info.root
        ? '已找到 <code>' + escapeHtml(shortPath(info.root)) + '</code>，但没有解析到 <code>assets/&lt;命名空间&gt;/lang/en_us.json</code>。'
        : '当前目录下没有 <code>kubejs</code> 文件夹。';
      fillEmpty('kubejs-empty', 'code', '未找到 KubeJS 语言文件', desc);
      empty.hidden = false;
      body.hidden = true;
      return;
    }
    empty.hidden = true;
    body.hidden = false;

    var totalEntries = 0, translated = 0, missing = 0;
    files.forEach(function (f) {
      totalEntries += f.entryCount || 0;
      if (!f.hasZhCn) missing += 1;
    });
    $('#kubejs-stats').innerHTML =
      statCard('code', '', fmtNumber(files.length), '语言文件') +
      statCard('layers', '', fmtNumber(totalEntries), '条目总数') +
      statCard('alert', missing ? 'warn' : 'ok', fmtNumber(missing), '缺少 zh_cn') +
      statCard('book', '', fmtNumber((info.namespaces || []).length), '命名空间');

    renderLangFileList('kubejs', files, '#kubejs-files', '#kubejs-list-empty', state.kubejsFilter.q);
  }

  function renderFtbPage() {
    var ws = state.workspace;
    var empty = $('#ftb-empty');
    var body = $('#ftb-body');
    if (!empty || !body) return;

    if (!ws || !ws.path) {
      fillEmpty('ftb-empty', 'folder-open', '还没有选择工作目录', '选择整合包目录后，这里会列出 <code>config/ftbquests/quests/lang</code> 下的 SNBT 语言文件。');
      empty.hidden = false;
      body.hidden = true;
      return;
    }
    var info = ws.ftb || {};
    var files = sourceLangFiles(info.files);
    if (!info.found || !files.length) {
      var desc = info.root
        ? '已找到 <code>' + escapeHtml(shortPath(info.root)) + '</code>，但没有解析到语言文件。'
        : '当前目录下没有 <code>config/ftbquests</code> 文件夹。';
      fillEmpty('ftb-empty', 'book', '未找到 FTB Quests 语言文件', desc);
      empty.hidden = false;
      body.hidden = true;
      return;
    }
    empty.hidden = true;
    body.hidden = false;

    var totalEntries = 0, missing = 0;
    files.forEach(function (f) {
      totalEntries += f.entryCount || 0;
      if (!f.hasZhCn) missing += 1;
    });
    $('#ftb-stats').innerHTML =
      statCard('book', '', fmtNumber(files.length), '语言文件') +
      statCard('layers', '', fmtNumber(totalEntries), '任务文本条目') +
      statCard('alert', missing ? 'warn' : 'ok', fmtNumber(missing), '缺少 zh_cn') +
      statCard('globe', '', fmtNumber((info.languages || []).length), '语言种类');

    renderLangFileList('ftb', files, '#ftb-files', '#ftb-list-empty', state.ftbFilter.q);
  }

  function renderLangFileList(kind, files, listSel, emptySel, query) {
    var q = String(query || '').trim().toLowerCase();
    var list = files.filter(function (f) {
      if (!q) return true;
      var hay = ((f.namespace || '') + ' ' + (f.relativePath || '') + ' ' + (f.lang || '')).toLowerCase();
      return hay.indexOf(q) >= 0;
    });
    var node = $(listSel);
    var emptyHint = $(emptySel);
    if (!node) return;
    if (!list.length) {
      node.innerHTML = '';
      if (emptyHint) emptyHint.hidden = false;
      return;
    }
    if (emptyHint) emptyHint.hidden = true;

    node.innerHTML = list.map(function (f) {
      var zh = f.hasZhCn ? '<span class="badge badge-ok">' + icon('check') + 'zh_cn</span>' : '<span class="badge badge-warn">无中文</span>';
      var title = kind === 'kubejs'
        ? ((f.namespace || 'kubejs') + ' · ' + (f.lang || 'en_us') + '.json')
        : ((f.relativePath || '').split('/').pop() || (f.lang || 'en_us') + '.snbt');
      return '<div class="file-row" data-kind="' + kind + '" data-path="' + escapeHtml(f.relativePath) + '" role="button" tabindex="0">' +
        '<div class="file-icon">' + icon(kind === 'kubejs' ? 'code' : 'book') + '</div>' +
        '<div class="file-main">' +
          '<div class="file-name">' + escapeHtml(title) + '</div>' +
          '<div class="file-path">' + escapeHtml(f.relativePath || '') + '</div>' +
        '</div>' +
        '<div class="file-badges">' + zh +
          '<span class="badge badge-mono">' + escapeHtml(fmtNumber(f.entryCount || 0)) + ' 条</span>' +
        '</div>' +
        '<div class="file-action">' +
          '<button type="button" class="btn btn-sm btn-ghost file-dl" title="导出 zh_cn.json">' + icon('download') + '</button>' +
          '<button type="button" class="btn btn-sm file-open">打开</button>' +
        '</div>' +
        '</div>';
    }).join('');
  }

  /* ============================================================
     内置目录浏览器
     ============================================================ */
  async function openBrowseModal(startPath) {
    var initial = startPath || (state.workspace && state.workspace.path) ||
      (state.settings && state.settings.lastWorkspace) || '';
    state.browse.selected = '';
    openModal('overlay-browse');
    await browseTo(initial);
  }

  async function browseTo(path) {
    var listNode = $('#browse-list');
    if (listNode) listNode.innerHTML = '<div class="browse-item"><span class="browse-name">正在读取目录…</span></div>';
    var url = '/api/workspace/browse' + (path ? '?path=' + encodeURIComponent(path) : '');
    var data;
    try {
      data = await apiJson(url);
    } catch (err) {
      if (listNode) listNode.innerHTML = '<div class="browse-item"><span class="browse-name">读取失败：' + escapeHtml(err.message) + '</span></div>';
      toast('读取目录失败：' + err.message, 'error');
      return;
    }
    if (!data.path && (data.drives || []).length) {
      // 首次打开：自动进入第一个驱动器
      await browseTo(data.drives[0].path);
      return;
    }
    state.browse.path = data.path || '';
    state.browse.parent = data.parent || null;
    state.browse.isRoot = !!data.isRoot;
    state.browse.drives = data.drives || [];
    state.browse.dirs = data.dirs || [];
    if (data.error) toast('读取目录时出错：' + data.error, 'warn');
    state.browse.suggestions = data.suggestions || [];
    state.browse.selected = '';

    var pathNode = $('#browse-path');
    if (pathNode) { pathNode.textContent = data.path || '—'; pathNode.title = data.path || ''; }

    var driveSelect = $('#browse-drive');
    if (driveSelect) {
      var drives = state.browse.drives;
      if (!drives.length && data.path) {
        var rootMatch = /^([A-Za-z]:[\\/]?)/.exec(data.path);
        if (rootMatch) drives = [{ name: rootMatch[1], path: rootMatch[1] }];
      }
      driveSelect.innerHTML = drives.map(function (d) {
        var label = d.name || d.path;
        var isCurrent = String(data.path || '').toLowerCase().indexOf(String(d.path || '').toLowerCase()) === 0;
        return '<option value="' + escapeHtml(d.path) + '"' + (isCurrent ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
      }).join('');
      driveSelect.disabled = !drives.length;
    }

    var upBtn = $('#browse-up');
    if (upBtn) upBtn.disabled = !state.browse.parent;

    renderBrowseList();
    updateBrowseFooter();
  }

  function renderBrowseList() {
    var listNode = $('#browse-list');
    if (!listNode) return;
    var dirs = state.browse.dirs;
    if (!dirs.length) {
      listNode.innerHTML = '<div class="browse-item"><span class="browse-name">该目录下没有子文件夹</span></div>';
      return;
    }
    listNode.innerHTML = dirs.map(function (d) {
      var tags = '';
      if (d.hasMods) tags += '<span class="badge badge-accent">mods</span>';
      if (d.hasKubejs) tags += '<span class="badge badge-accent">kubejs</span>';
      if (d.hasConfig) tags += '<span class="badge">config</span>';
      if (d.hasMinecraft) tags += '<span class="badge badge-ok">.minecraft</span>';
      return '<div class="browse-item' + (state.browse.selected === d.path ? ' selected' : '') + '" data-path="' + escapeHtml(d.path) + '">' +
        '<span class="browse-icon">' + icon('folder') + '</span>' +
        '<span class="browse-name">' + escapeHtml(d.name || d.path) + '</span>' +
        '<span class="browse-tags">' + tags + '</span>' +
        '</div>';
    }).join('');

    var suggest = state.browse.suggestions;
    var host = $('#browse-suggest');
    if (!host) return;
    if (suggest.length) {
      host.hidden = false;
      host.innerHTML = '<span class="browse-suggest-label">推荐：</span>' + suggest.map(function (s) {
        return '<button type="button" class="suggest-chip" data-suggest="' + escapeHtml(s.path) + '" title="' + escapeHtml(s.path + ' — ' + (s.reason || '')) + '">' + escapeHtml(shortPath(s.label || s.path)) + '</button>';
      }).join('');
    } else {
      host.hidden = true;
      host.innerHTML = '';
    }
  }

  function updateBrowseFooter() {
    var info = $('#browse-selected');
    if (!info) return;
    var target = state.browse.selected || state.browse.path;
    info.textContent = target ? ('将使用：' + target) : '未选择目录';
    info.title = target || '';
  }

  /* ============================================================
     资源包 / 覆盖包 弹窗
     ============================================================ */
  function openPackModal(mode, options) {
    options = options || {};
    state.pack.mode = mode;
    state.pack.kind = options.kind || (mode === 'kubejs-overlay' ? 'kubejs' : (mode === 'ftb-overlay' ? 'ftb' : null));
    state.pack.desc = options.desc || null;
    state.pack.files = options.files || null;
    state.pack.scope = options.scope === 'current' ? 'current' : 'all';

    var isPack = mode === 'resourcepack';
    var titleNode = $('#pack-title');
    if (titleNode) titleNode.textContent = isPack ? '生成汉化资源包' : '生成覆盖包';

    var packFields = $('#pack-fields-resourcepack');
    if (packFields) packFields.hidden = !isPack;
    var overlayFields = $('#pack-fields-overlay');
    if (overlayFields) overlayFields.hidden = isPack;
    var packNotice = $('#pack-notice-resourcepack');
    if (packNotice) packNotice.hidden = !isPack;
    var overlayNotice = $('#pack-notice-overlay');
    if (overlayNotice) overlayNotice.hidden = isPack;

    var nameInput = $('#pack-name');
    if (nameInput) nameInput.value = options.defaultName || '汉化资源包';
    var verInput = $('#pack-version');
    if (verInput) verInput.value = options.defaultVersion || '1.0.0';
    var mcSelect = $('#pack-mc');
    if (mcSelect) mcSelect.value = '';
    var formatInput = $('#pack-format');
    if (formatInput) formatInput.value = '';

    var previewTitle = $('#pack-preview-title');
    if (previewTitle) previewTitle.textContent = isPack ? '资源包将包含' : '覆盖包将包含';

    var summary = $('#pack-summary');
    if (summary) summary.textContent = isPack ? (options.summary || '语言文件与 pack.mcmeta') : '';

    if (!isPack) {
      var langSelect = $('#pack-overlay-lang');
      if (langSelect) langSelect.value = options.targetLanguage || (state.doc && state.doc.targetLanguage) || 'zh_cn';
      // 只有从文件页发起（或只有一个候选文件）时「当前文件」才有意义
      var canCurrent = !!(state.pack.files && state.pack.files.length <= 1);
      var scopeSelect = $('#pack-scope');
      if (scopeSelect) {
        var currentOption = scopeSelect.querySelector('option[value="current"]');
        if (currentOption) {
          currentOption.disabled = !canCurrent;
          currentOption.textContent = canCurrent ? '当前文件' : '当前文件（需先打开文件）';
        }
        if (!canCurrent) state.pack.scope = 'all';
        scopeSelect.value = state.pack.scope;
      }
      var confirmLabel = $('#pack-confirm span');
      if (confirmLabel) confirmLabel.textContent = '生成并下载';
      // 覆盖包预览文案完全由「包含范围」推导
      updateOverlaySummary();
    }
    openModal('overlay-pack');
  }

  /** 根据「包含范围」更新弹窗预览文案。 */
  function updateOverlaySummary() {
    if (state.pack.mode === 'resourcepack') return;
    var summary = $('#pack-summary');
    if (!summary) return;
    var scope = val('#pack-scope') || 'all';
    var current = state.pack.files || [];
    if (scope === 'all') {
      var total = listFilesFor(state.pack.kind || 'kubejs').length;
      summary.textContent = total
        ? ('将打包全部 ' + fmtNumber(total) + ' 个语言文件（解压到整合包根目录覆盖即可）')
        : '将打包全部语言文件（解压到整合包根目录覆盖即可）';
    } else {
      var count = current.length || 1;
      summary.textContent = '将打包当前 ' + fmtNumber(count) + ' 个语言文件（解压到整合包根目录覆盖即可）';
    }
  }

  /** 覆盖包：当前文件 / 全部文件。全部文件时 files 传空数组，由后端导出全部。 */
  function overlayScopeFiles() {
    var scope = val('#pack-scope') || 'all';
    state.pack.scope = scope;
    if (scope === 'all') return [];
    var files = state.pack.files;
    if (!files || !files.length) {
      if (state.doc && state.currentDesc) {
        files = [{ relativePath: state.doc.relativePath, desc: state.currentDesc }];
      } else {
        files = [];
      }
    }
    return files;
  }

  async function submitPackModal() {
    var mode = state.pack.mode;
    var btn = $('#pack-confirm');
    if (btn) btn.disabled = true;
    try {
      if (mode === 'resourcepack') {
        var packName = val('#pack-name').trim();
        var packVersion = val('#pack-version').trim();
        if (!packName) { toast('请填写资源包名称', 'warn'); return; }
        if (!packVersion) { toast('请填写资源包版本号', 'warn'); return; }
        var mcVersion = val('#pack-mc');
        if (!mcVersion) { toast('请手动选择 Minecraft 版本（用于 pack_format）', 'warn'); return; }
        var doc = state.doc;
        var desc = state.pack.desc || state.currentDesc;
        if (!doc || !desc) { toast('没有可导出的文档', 'error'); return; }
        var body = {
          kind: 'mod',
          file: desc.file,
          namespace: desc.namespace,
          lang: desc.lang,
          entries: buildEntries(doc),
          targetLanguage: doc.targetLanguage || 'zh_cn',
          packName: packName,
          packVersion: packVersion,
          mcVersion: mcVersion
        };
        var formatText = val('#pack-format').trim();
        if (formatText) {
          var fmt = parseInt(formatText, 10);
          if (!fmt || fmt < 1) { toast('pack_format 需要是正整数', 'warn'); return; }
          body.packFormat = fmt;
        }
        var res = await api('/api/export/resourcepack', { method: 'POST', body: body });
        var name = await downloadResponse(res, packName + '-' + packVersion + '.zip');
        toast('已生成资源包：' + name, 'success');
      } else {
        var kind = state.pack.kind || (mode === 'kubejs-overlay' ? 'kubejs' : 'ftb');
        var targetLanguage = val('#pack-overlay-lang') || (state.doc && state.doc.targetLanguage) || 'zh_cn';
        var scopeFiles = overlayScopeFiles();
        var payloadFiles = [];
        for (var i = 0; i < scopeFiles.length; i++) {
          var f = scopeFiles[i];
          var fdesc = f.desc || { kind: kind, path: f.relativePath };
          var fdoc;
          try { fdoc = await ensureDoc(fdesc); } catch (err) { toast('跳过 ' + (f.relativePath || '') + '：' + err.message, 'warn'); continue; }
          payloadFiles.push({ path: fdoc.relativePath || f.relativePath, entries: buildEntries(fdoc) });
        }
        if (scopeFiles.length && !payloadFiles.length) { toast('没有可打包的语言文件', 'error'); return; }
        var url = kind === 'kubejs' ? '/api/export/kubejs-overlay' : '/api/export/ftb-overlay';
        var out = await api(url, {
          method: 'POST',
          body: {
            files: payloadFiles,
            targetLanguage: targetLanguage
          }
        });
        var fallbackName = ((state.workspace && state.workspace.name) || 'overlay') +
          (kind === 'kubejs' ? '-kubejs-' : '-ftbquests-') + targetLanguage + '.zip';
        var fname = await downloadResponse(out, fallbackName);
        toast('已生成覆盖包：' + fname +
          (payloadFiles.length ? ('（' + payloadFiles.length + ' 个文件）') : '（全部语言文件）'), 'success');
      }
      closeModal('overlay-pack');
    } catch (err) {
      toast('导出失败：' + err.message, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }


  /* ============================================================
     文档 / 键值表
     ============================================================ */
  function descKey(desc) {
    if (!desc) return '';
    if (desc.kind === 'mod') {
      return 'mod|' + (desc.file || '') + '|' + (desc.namespace || '') + '|' + (desc.lang || '');
    }
    return desc.kind + '|' + (desc.path || '');
  }

  function descQuery(desc) {
    var params = new URLSearchParams();
    params.set('kind', desc.kind);
    if (desc.kind === 'mod') {
      params.set('file', desc.file || '');
      if (desc.namespace) params.set('ns', desc.namespace);
      if (desc.lang) params.set('lang', desc.lang);
    } else {
      params.set('path', desc.path || '');
    }
    return params.toString();
  }

  function descBody(desc) {
    var body = { kind: desc.kind };
    if (desc.kind === 'mod') {
      body.file = desc.file;
      if (desc.namespace) body.namespace = desc.namespace;
      if (desc.lang) body.lang = desc.lang;
    } else {
      body.path = desc.path;
    }
    return body;
  }

  async function ensureDoc(desc) {
    var key = descKey(desc);
    if (docCache[key]) return docCache[key];
    var doc = await apiJson('/api/document?' + descQuery(desc));
    docCache[key] = doc;
    return doc;
  }

  function targetOf(key) {
    var doc = state.doc;
    if (!doc) return '';
    var targets = state.targets[doc.id];
    if (targets && has(targets, key)) return targets[key];
    var existing = doc.existing || {};
    if (has(existing, key)) return existing[key];
    return '';
  }

  function targetsFor(doc) {
    if (!state.targets[doc.id]) state.targets[doc.id] = {};
    return state.targets[doc.id];
  }

  function valueFor(doc, key, source) {
    if (!doc) return source || '';
    var targets = state.targets[doc.id];
    if (targets && has(targets, key)) return targets[key];
    var existing = doc.existing || {};
    if (has(existing, key)) return existing[key];
    return source || '';
  }

  function buildEntries(doc) {
    var list = [];
    (doc.entries || []).forEach(function (e) {
      list.push({ key: e.key, value: valueFor(doc, e.key, e.value) });
    });
    return list;
  }

  async function openDoc(desc) {
    var doc;
    try {
      doc = await ensureDoc(desc);
    } catch (err) {
      toast('打开语言文件失败：' + err.message, 'error');
      return;
    }
    state.doc = doc;
    state.currentDesc = desc;
    state.kv = { q: '', onlyUntranslated: false, selected: {}, limit: ROW_LIMIT_STEP };
    targetsFor(doc);

    var search = $('#kv-search');
    if (search) search.value = '';
    var only = $('#chk-only-untranslated');
    if (only) only.checked = false;
    var all = $('#chk-select-all');
    if (all) all.checked = false;

    showPage('file', desc.kind === 'ftb' ? 'ftb' : (desc.kind === 'kubejs' ? 'kubejs' : 'mods'));
    renderFilePage();
  }

  function renderFilePage() {
    var doc = state.doc;
    if (!doc) return;

    var title = $('#file-title');
    if (title) title.textContent = doc.displayName || doc.relativePath || '语言文件';
    var sub = $('#file-sub');
    if (sub) {
      sub.innerHTML = escapeHtml(doc.relativePath || '') +
        ' · 源语言 <code>' + escapeHtml(doc.sourceLanguage || 'en_us') + '</code>' +
        ' · <code>' + escapeHtml(doc.format || 'json') + '</code>' +
        ' · ' + escapeHtml(fmtNumber((doc.entries || []).length)) + ' 条';
    }

    var exportPack = $('#btn-file-export-pack');
    if (exportPack) {
      var label = exportPack.querySelector('span');
      if (label) label.textContent = doc.kind === 'mod' ? '生成资源包' : '生成覆盖包';
      exportPack.hidden = false;
    }

    var langSelect = $('#sel-target-lang');
    if (langSelect) langSelect.value = doc.targetLanguage || (state.settings && state.settings.targetLanguage) || 'zh_cn';

    renderKvTable();
    updateKvStats();
    updateJobResultButtons(state.job.data);
  }

  function filteredEntries() {
    var doc = state.doc;
    if (!doc) return [];
    var q = state.kv.q.trim().toLowerCase();
    var out = [];
    var entries = doc.entries || [];
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i];
      var target = targetOf(e.key);
      if (state.kv.onlyUntranslated && target) continue;
      if (q) {
        var hay = (e.key + '\n' + (e.value || '') + '\n' + target).toLowerCase();
        if (hay.indexOf(q) < 0) continue;
      }
      out.push(e);
    }
    return out;
  }

  function statusBadge(target, key) {
    var doc = state.doc;
    var existing = (doc && doc.existing) || {};
    if (target) {
      if (has(existing, key) && existing[key] === target) return '<span class="badge badge-ok">' + icon('check') + '已有</span>';
      return '<span class="badge badge-ok">' + icon('check') + '已翻译</span>';
    }
    return '<span class="badge badge-warn">未翻译</span>';
  }

  function renderKvTable() {
    var doc = state.doc;
    var tbody = $('#kv-tbody');
    var emptyHint = $('#kv-empty');
    if (!doc || !tbody) return;

    var rows = filteredEntries();
    var limited = rows.slice(0, state.kv.limit);
    if (!limited.length) {
      tbody.innerHTML = '';
      if (emptyHint) emptyHint.hidden = false;
    } else {
      if (emptyHint) emptyHint.hidden = true;
      tbody.innerHTML = limited.map(function (e) {
        var target = targetOf(e.key);
        var isSelected = !!state.kv.selected[e.key];
        return '<tr data-key="' + escapeHtml(e.key) + '">' +
          '<td class="col-check"><label class="checkbox"><input type="checkbox" class="kv-check"' + (isSelected ? ' checked' : '') + ' /></label></td>' +
          '<td><div class="kv-key">' + escapeHtml(e.key) + '</div></td>' +
          '<td><div class="kv-source">' + escapeHtml(e.value || '') + '</div></td>' +
          '<td><textarea class="kv-input" rows="1" spellcheck="false" data-key="' + escapeHtml(e.key) + '">' + escapeHtml(target) + '</textarea></td>' +
          '<td class="col-status"><span class="kv-status">' + statusBadge(target, e.key) + '</span></td>' +
          '</tr>';
      }).join('');
    }

    var scroll = tbody.closest('.table-scroll');
    var more = $('#kv-more');
    if (!more && scroll) {
      more = document.createElement('div');
      more.id = 'kv-more';
      more.className = 'table-more';
      scroll.appendChild(more);
    }
    if (more) {
      if (rows.length > limited.length) {
        more.hidden = false;
        more.innerHTML = '<button type="button" class="btn btn-sm" id="btn-kv-more">继续加载 ' +
          Math.min(ROW_LIMIT_STEP, rows.length - limited.length) + ' 条（共 ' + rows.length + ' 条）</button>';
      } else {
        more.hidden = true;
        more.innerHTML = '';
      }
    }

    requestAnimationFrame(function () {
      $$('#kv-tbody .kv-input').forEach(autosize);
    });

    var all = $('#chk-select-all');
    if (all) {
      var selectedCount = Object.keys(state.kv.selected).filter(function (k) { return state.kv.selected[k]; }).length;
      all.checked = rows.length > 0 && rows.every(function (e) { return !!state.kv.selected[e.key]; });
      all.indeterminate = selectedCount > 0 && !all.checked;
    }
  }

  function autosize(el) {
    if (!el) return;
    el.style.height = 'auto';
    var h = el.scrollHeight + 2;
    el.style.height = Math.min(h, 320) + 'px';
  }

  function updateKvStats() {
    var doc = state.doc;
    if (!doc) return;
    var entries = doc.entries || [];
    var done = 0;
    for (var i = 0; i < entries.length; i++) if (targetOf(entries[i].key)) done += 1;
    var node = $('#kv-stats');
    if (node) {
      node.textContent = '共 ' + fmtNumber(entries.length) + ' 条 · 已翻译 ' +
        fmtNumber(done) + ' · 未翻译 ' + fmtNumber(entries.length - done);
    }
  }

  /** 还原译文：把当前文档在内存里的译文全部清空，回到 doc.existing 或空。 */
  function restoreTargets(options) {
    var doc = state.doc;
    if (!doc) { toast('请先打开一个语言文件', 'warn'); return false; }
    var existing = doc.existing || {};
    var targets = state.targets[doc.id];
    var touched = 0;
    (doc.entries || []).forEach(function (e) {
      var had = !!(targets && has(targets, e.key)) ? targets[e.key] : (has(existing, e.key) ? existing[e.key] : '');
      var back = has(existing, e.key) ? existing[e.key] : '';
      if (targets && has(targets, e.key)) {
        if (targets[e.key] !== back) touched += 1;
        if (back) targets[e.key] = back; else delete targets[e.key];
      } else if (had !== back) {
        touched += 1;
      }
    });
    if (targets) {
      Object.keys(targets).forEach(function (k) {
        if (!has(existing, k) && targets[k]) { delete targets[k]; touched += 1; }
      });
    }
    if (state.job.stagedDocId === doc.id) state.job.staged = {};
    renderKvTable();
    updateKvStats();
    if (!(options && options.silent)) {
      toast(touched ? ('已还原 ' + fmtNumber(touched) + ' 条译文') : '没有需要还原的译文', touched ? 'success' : 'info');
    }
    return true;
  }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(function () { return fallbackCopy(text); });
    }
    return Promise.resolve(fallbackCopy(text));
  }

  function fallbackCopy(text) {
    var area = document.createElement('textarea');
    area.value = text;
    area.style.position = 'fixed';
    area.style.left = '-9999px';
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    document.body.removeChild(area);
  }

  /* ============================================================
     导出（语言 JSON）
     ============================================================ */
  async function exportLangDoc(doc, desc) {
    if (!doc) { toast('没有可导出的文档', 'warn'); return; }
    var target = (doc.targetLanguage || 'zh_cn');
    var fileName = (doc.displayName || 'lang') + '.' + target + '.json';
    try {
      var body = descBody(desc || state.currentDesc || { kind: doc.kind, path: doc.relativePath });
      body.entries = buildEntries(doc);
      body.targetLanguage = target;
      body.fileName = fileName;
      var res = await api('/api/export/lang', { method: 'POST', body: body });
      var name = await downloadResponse(res, fileName);
      toast('已导出：' + name, 'success');
    } catch (err) {
      toast('导出失败：' + err.message, 'error');
    }
  }

  /* ============================================================
     翻译任务
     ============================================================ */
  function parseGlossary(text) {
    var out = {};
    var any = false;
    String(text || '').split(/\r?\n/).forEach(function (line) {
      var raw = line.trim();
      if (!raw || raw.charAt(0) === '#') return;
      var colon = raw.indexOf(':');
      var equal = raw.indexOf('=');
      var idx = colon;
      if (colon < 0 || (equal >= 0 && equal < colon)) idx = equal;
      if (idx <= 0) return;
      var key = raw.slice(0, idx).trim();
      var value = raw.slice(idx + 1).trim();
      if (key && value) { out[key] = value; any = true; }
    });
    return any ? out : undefined;
  }

  function setJobPanelVisible(visible) {
    var panel = $('#job-panel');
    if (panel) panel.hidden = !visible;
  }

  async function startTranslate() {
    var doc = state.doc;
    var desc = state.currentDesc;
    if (!doc || !desc) { toast('请先打开一个语言文件', 'warn'); return; }
    if (state.job.id && state.job.timer) { toast('已有翻译任务正在运行', 'warn'); return; }

    var btn = $('#btn-start-translate');
    if (btn) btn.disabled = true;
    try {
      var body = descBody(desc);
      body.mode = (state.translateMode === 'mt' || state.translateMode === 'machine') ? 'mt' : 'llm';
      if (body.mode === 'mt') {
        body.provider = (state.settings && state.settings.mtProvider) || 'auto';
      }
      body.entries = (doc.entries || []).map(function (e) { return { key: e.key, value: e.value }; });
      body.glossary = parseGlossary(val('#glossary-input'));
      body.askUser = !!(state.settings && state.settings.askUserEnabled);
      body.skipExisting = checked('#chk-skip-existing');
      body.targetLanguage = val('#sel-target-lang') || (doc.targetLanguage || 'zh_cn');

      var res = await apiJson('/api/translate', { method: 'POST', body: body });
      if (!res || !res.jobId) throw new ApiError('服务未返回任务 ID', 0);
      state.job.id = res.jobId;
      state.job.docId = doc.id;
      state.job.data = null;
      state.job.modalShown = {};
      state.job.staged = {};
      state.job.stagedDocId = null;
      setJobPanelVisible(true);
      renderJob({ id: res.jobId, status: 'queued', total: (doc.entries || []).length, done: 0, failed: 0, skipped: 0, items: [], questions: [] });
      startJobPolling();
      toast('翻译任务已启动', 'success');
    } catch (err) {
      toast('启动翻译失败：' + err.message, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function startJobPolling() {
    stopJobPolling();
    state.job.timer = setInterval(pollJob, JOB_POLL_MS);
    pollJob();
  }

  function stopJobPolling() {
    if (state.job.timer) {
      clearInterval(state.job.timer);
      state.job.timer = null;
    }
  }

  async function pollJob() {
    var id = state.job.id;
    if (!id) return;
    var job;
    try {
      job = await apiJson('/api/translate/' + encodeURIComponent(id));
    } catch (err) {
      stopJobPolling();
      renderJobError(err.message);
      return;
    }
    state.job.data = job;
    renderJob(job);

    // 翻译结果先暂存到面板，用户点「应用结果」后才写入表格
    if (job.status === 'running' || job.status === 'waiting_user' || job.status === 'done' || job.status === 'cancelled') {
      stageJobResults(job);
    }

    if (job.status === 'waiting_user') {
      maybeShowQuestions(job);
      return;
    }
    if (job.status === 'done' || job.status === 'cancelled' || job.status === 'error') {
      stopJobPolling();
      var stagedCount = Object.keys(state.job.staged || {}).length;
      if (job.status === 'done') {
        toast('翻译完成：成功 ' + (job.done || 0) + ' 条' + (job.skipped ? ('，跳过 ' + job.skipped + ' 条') : '') +
          (job.failed ? ('，失败 ' + job.failed + ' 条') : '') +
          (stagedCount ? ('；点「应用结果」写入表格') : ''), job.failed ? 'warn' : 'success');
      } else if (job.status === 'cancelled') toast('翻译任务已取消' + (stagedCount ? ('，已保留 ' + stagedCount + ' 条结果') : ''), 'info');
      else toast('翻译任务失败：' + (job.error || '未知错误'), 'error');
    }
  }

  function jobResultItems(job) {
    return (job && job.items ? job.items : []).filter(function (item) {
      return item && item.key && (item.status === 'done' || item.status === 'skipped') && item.target;
    });
  }

  /** 翻译结果先暂存，用户点「应用结果」后才写入文档。 */
  function stageJobResults(job) {
    var docId = job.documentId || state.job.docId;
    if (!docId) return;
    if (state.job.stagedDocId !== docId) {
      state.job.stagedDocId = docId;
      state.job.staged = {};
    }
    jobResultItems(job).forEach(function (item) { state.job.staged[item.key] = item.target; });
    updateJobResultButtons(job);
  }

  function applyJobResults(job) {
    var docId = (job && (job.documentId || state.job.docId)) || state.job.stagedDocId;
    var staged = state.job.staged || {};
    var count = 0;
    if (docId) {
      var targets = state.targets[docId] || (state.targets[docId] = {});
      Object.keys(staged).forEach(function (k) {
        if (staged[k]) { targets[k] = staged[k]; count += 1; }
      });
    }
    state.job.staged = {};
    state.job.stagedDocId = null;
    if (state.doc && state.doc.id === docId) {
      renderKvTable();
      updateKvStats();
    }
    updateJobResultButtons(job);
    toast(count ? ('已应用 ' + fmtNumber(count) + ' 条译文') : '没有可应用的译文', count ? 'success' : 'info');
  }

  function discardJobResults(job) {
    var count = Object.keys(state.job.staged || {}).length;
    state.job.staged = {};
    state.job.stagedDocId = null;
    updateJobResultButtons(job);
    toast(count ? ('已丢弃 ' + fmtNumber(count) + ' 条翻译结果') : '没有可丢弃的翻译结果', 'info');
  }

  function updateJobResultButtons(job) {
    var stagedCount = Object.keys(state.job.staged || {}).length;
    var applyBtn = $('#btn-job-apply');
    if (applyBtn) applyBtn.disabled = stagedCount === 0;
    var discardBtn = $('#btn-job-discard');
    if (discardBtn) discardBtn.disabled = stagedCount === 0;
    var restoreAll = $('#btn-job-restore-all');
    if (restoreAll) restoreAll.disabled = !state.doc;
  }

  var JOB_STATUS_TEXT = {
    queued: '排队中',
    running: '翻译中',
    waiting_user: '等待确认术语',
    done: '已完成',
    cancelled: '已取消',
    error: '出错'
  };

  function renderJob(job) {
    setJobPanelVisible(true);
    var status = job.status || 'queued';
    var dot = $('#job-dot');
    if (dot) {
      dot.className = 'job-dot';
      if (status === 'running') dot.classList.add('running');
      else if (status === 'done') dot.classList.add('done');
      else if (status === 'error') dot.classList.add('error');
      else if (status === 'waiting_user') dot.classList.add('waiting');
    }
    var statusNode = $('#job-status');
    if (statusNode) statusNode.textContent = JOB_STATUS_TEXT[status] || status;

    var total = job.total || 0;
    var processed = (job.done || 0) + (job.failed || 0) + (job.skipped || 0);
    if (processed > total) processed = total;
    var percent = total > 0 ? Math.round((processed / total) * 100) : 0;
    var fill = $('#job-progress-fill');
    if (fill) {
      fill.style.width = percent + '%';
      fill.className = 'progress-fill' + (status === 'done' ? ' done' : (status === 'error' ? ' error' : ''));
    }
    var count = $('#job-count');
    if (count) count.textContent = processed + ' / ' + total;
    var detail = $('#job-detail');
    if (detail) {
      var parts = [];
      if (job.mode) parts.push(job.mode === 'mt' ? '机翻' : 'LLM');
      if (job.provider) parts.push(job.provider);
      if (job.failed) parts.push('失败 ' + job.failed);
      if (job.skipped) parts.push('跳过 ' + job.skipped);
      detail.textContent = parts.join(' · ');
    }

    var cancelBtn = $('#btn-job-cancel');
    if (cancelBtn) cancelBtn.disabled = !(status === 'running' || status === 'queued' || status === 'waiting_user');
    updateJobResultButtons(job);

    var items = job.items || [];
    var box = $('#job-items');
    if (box) {
      if (!items.length) {
        box.innerHTML = '<div class="job-item"><span class="ji-text">正在准备…</span></div>';
      } else {
        var shown = items.slice(0, 240);
        box.innerHTML = shown.map(function (item) {
          var st = item.status || 'pending';
          var text = item.target || item.source || '';
          if (st === 'error' && item.error) text = item.error;
          return '<div class="job-item ' + escapeHtml(st) + '">' +
            '<span class="ji-dot"></span>' +
            '<span class="ji-key" title="' + escapeHtml(item.key) + '">' + escapeHtml(item.key) + '</span>' +
            '<span class="ji-text" title="' + escapeHtml(text) + '">' + escapeHtml(text) + '</span>' +
            '</div>';
        }).join('') + (items.length > shown.length ? '<div class="job-item"><span class="ji-text">… 其余 ' + (items.length - shown.length) + ' 条已省略</span></div>' : '');
      }
    }
  }

  function renderJobError(message) {
    var statusNode = $('#job-status');
    if (statusNode) statusNode.textContent = '出错';
    var dot = $('#job-dot');
    if (dot) dot.className = 'job-dot error';
    var detail = $('#job-detail');
    if (detail) detail.textContent = message || '';
    var fill = $('#job-progress-fill');
    if (fill) fill.className = 'progress-fill error';
  }

  async function cancelJob() {
    var id = state.job.id;
    if (!id) return;
    try {
      await api('/api/translate/' + encodeURIComponent(id) + '/cancel', { method: 'POST' });
      toast('已请求取消任务', 'info');
    } catch (err) {
      toast('取消失败：' + err.message, 'error');
    }
  }

  /* ---------------- LLM 提问 ---------------- */
  function pendingQuestions(job) {
    return (job.questions || []).filter(function (q) { return q.status === 'pending'; });
  }

  function maybeShowQuestions(job) {
    var pending = pendingQuestions(job);
    if (!pending.length) return;
    var sig = pending.map(function (q) { return q.id; }).join(',');
    if (state.job.modalShown[sig]) return;
    state.job.modalShown[sig] = true;
    state.questionsJobId = job.id;
    renderQuestions(job, pending);
    openModal('overlay-questions');
  }

  function renderQuestions(job, pending) {
    var box = $('#questions-list');
    if (!box) return;
    box.innerHTML = pending.map(function (q, index) {
      var terms = (q.terms || []).map(function (t) {
        return '<span class="badge badge-accent">' + escapeHtml(t) + '</span>';
      }).join('');
      var options = (q.options || []).map(function (o) {
        return '<button type="button" class="q-option" data-value="' + escapeHtml(o) + '">' + escapeHtml(o) + '</button>';
      }).join('');
      return '<div class="question-card" data-qid="' + escapeHtml(q.id) + '">' +
        '<div class="q-head">' +
          '<span class="q-index">' + (index + 1) + '</span>' +
          '<div class="q-text">' + escapeHtml(q.question || '') + '</div>' +
        '</div>' +
        (terms ? '<div class="q-terms">' + terms + '</div>' : '') +
        (options ? '<div class="q-options">' + options + '</div>' : '') +
        '<div class="q-input"><input type="text" class="input q-answer" placeholder="输入你希望采用的中文译名…" /></div>' +
        '</div>';
    }).join('');

    var info = $('#questions-info');
    if (info) info.textContent = '共 ' + pending.length + ' 个问题';
  }

  async function submitAnswers() {
    var jobId = state.questionsJobId;
    if (!jobId) { closeModal('overlay-questions'); return; }
    var cards = $$('#questions-list .question-card');
    var btn = $('#questions-submit');
    if (btn) btn.disabled = true;
    try {
      for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var qid = card.getAttribute('data-qid');
        var input = card.querySelector('.q-answer');
        var answer = input ? input.value.trim() : '';
        if (!answer) answer = '跳过';
        await api('/api/translate/' + encodeURIComponent(jobId) + '/answer', {
          method: 'POST',
          body: { questionId: qid, answer: answer }
        });
      }
      toast('已提交答案，翻译继续', 'success');
      closeModal('overlay-questions');
      if (state.job.id === jobId && !state.job.timer) startJobPolling();
    } catch (err) {
      toast('提交答案失败：' + err.message, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  /* ============================================================
     设置页
     ============================================================ */
  function defaultSettings() {
    return {
      llmBaseUrl: 'https://api.deepseek.com/v1',
      llmApiKey: '',
      llmApiKeySet: false,
      llmModel: 'deepseek-flash',
      suggestedModels: ['deepseek-flash', 'deepseek-v4-pro'],
      llmSystemPrompt: '',
      llmSystemPromptDefault: '',
      llmTemperature: 1,
      llmMaxTokens: 4096,
      mtProvider: 'auto',
      mtEndpoint: '',
      mtApiKey: '',
      mtApiKeySet: false,
      targetLanguage: 'zh_cn',
      askUserEnabled: true,
      requestTimeoutSeconds: 120,
      batchSize: 24,
      lastWorkspace: ''
    };
  }

  async function loadSettings() {
    try {
      var data = await apiJson('/api/settings');
      state.settings = Object.assign(defaultSettings(), data || {});
    } catch (err) {
      state.settings = defaultSettings();
      if (err.status && err.status !== 0) toast('读取设置失败：' + err.message, 'warn');
    }
    renderSettings();
    syncTargetLang();
  }

  function renderSettings() {
    var s = state.settings || defaultSettings();
    var base = $('#set-llm-baseurl'); if (base) base.value = s.llmBaseUrl || '';
    var key = $('#set-llm-apikey');
    if (key) {
      key.value = '';
      key.placeholder = s.llmApiKeySet ? '已保存（留空则不修改）' : 'sk-...';
    }
    renderModelOptions(s);
    var model = $('#set-llm-model');
    if (model) model.value = s.llmModel || 'deepseek-flash';
    var prompt = $('#set-llm-prompt');
    if (prompt) {
      prompt.value = s.llmSystemPrompt || '';
      prompt.placeholder = '留空则使用内置默认提示词';
    }
    var promptHint = $('#prompt-hint');
    if (promptHint) {
      promptHint.textContent = (s.llmSystemPrompt && s.llmSystemPrompt.trim())
        ? '正在使用自定义提示词（' + fmtNumber(s.llmSystemPrompt.length) + ' 字符）'
        : '留空则使用内置默认提示词（点击「填入默认提示词」可查看并在此基础上修改）';
    }
    var temp = $('#set-llm-temp');
    if (temp) { temp.value = String(typeof s.llmTemperature === 'number' ? s.llmTemperature : 1); updateTempLabel(); }
    var maxTokens = $('#set-llm-maxtokens'); if (maxTokens) maxTokens.value = s.llmMaxTokens || 4096;
    var provider = $('#set-machine-provider'); if (provider) provider.value = s.mtProvider || 'auto';
    var endpoint = $('#set-machine-endpoint'); if (endpoint) endpoint.value = s.mtEndpoint || '';
    var mtKey = $('#set-machine-key');
    if (mtKey) {
      mtKey.value = '';
      mtKey.placeholder = s.mtApiKeySet ? '已保存（留空则不修改）' : '自定义接口的访问密钥';
    }
    var targetLang = $('#set-target-lang'); if (targetLang) targetLang.value = s.targetLanguage || 'zh_cn';
    var allow = $('#set-allow-questions'); if (allow) allow.checked = s.askUserEnabled !== false;
    var timeout = $('#set-timeout'); if (timeout) timeout.value = s.requestTimeoutSeconds || 120;
    var batch = $('#set-batch'); if (batch) batch.value = s.batchSize || 24;
    var themeChoices = $$('.theme-choice');
    themeChoices.forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-theme-value') === state.theme); });
  }

  /** 模型候选来自 SettingsDto.suggestedModels，允许手填任意模型名。 */
  function renderModelOptions(s) {
    var host = $('#llm-model-options');
    if (!host) return;
    var models = (s && s.suggestedModels && s.suggestedModels.length)
      ? s.suggestedModels.slice()
      : ['deepseek-flash', 'deepseek-v4-pro'];
    if (s && s.llmModel && models.indexOf(s.llmModel) < 0) models.push(s.llmModel);
    host.innerHTML = models.map(function (m) {
      return '<option value="' + escapeHtml(m) + '"></option>';
    }).join('');
  }

  function updateTempLabel() {
    var temp = $('#set-llm-temp');
    var label = $('#val-temp');
    if (!temp || !label) return;
    var v = parseFloat(temp.value);
    label.textContent = (isNaN(v) ? 0 : v).toFixed(1);
    var pct = ((v || 0) / 2) * 100;
    temp.style.setProperty('--pct', pct + '%');
  }

  function syncTargetLang() {
    var s = state.settings || defaultSettings();
    var bar = $('#sel-target-lang');
    if (bar) bar.value = s.targetLanguage || 'zh_cn';
  }

  function readSettingsForm() {
    return {
      llmBaseUrl: val('#set-llm-baseurl').trim(),
      llmApiKey: val('#set-llm-apikey').trim(),
      llmModel: val('#set-llm-model').trim() || 'deepseek-flash',
      llmSystemPrompt: val('#set-llm-prompt'),
      llmTemperature: parseFloat(val('#set-llm-temp')) || 0,
      llmMaxTokens: num('#set-llm-maxtokens', 4096),
      mtProvider: val('#set-machine-provider') || 'auto',
      mtEndpoint: val('#set-machine-endpoint').trim(),
      mtApiKey: val('#set-machine-key').trim(),
      targetLanguage: val('#set-target-lang') || 'zh_cn',
      askUserEnabled: checked('#set-allow-questions'),
      requestTimeoutSeconds: num('#set-timeout', 120),
      batchSize: num('#set-batch', 24),
      lastWorkspace: (state.workspace && state.workspace.path) || ((state.settings && state.settings.lastWorkspace) || '')
    };
  }

  async function saveSettings() {
    var btn = $('#btn-settings-save');
    if (btn) btn.disabled = true;
    try {
      var saved = await apiJson('/api/settings', { method: 'POST', body: readSettingsForm() });
      state.settings = Object.assign(defaultSettings(), saved || {}, { llmApiKey: '', mtApiKey: '' });
      renderSettings();
      syncTargetLang();
      toast('设置已保存', 'success');
    } catch (err) {
      toast('保存设置失败：' + err.message, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function fillDefaultPrompt() {
    var s = state.settings || defaultSettings();
    var text = s.llmSystemPromptDefault || '';
    var prompt = $('#set-llm-prompt');
    if (!text) { toast('服务未返回默认提示词', 'warn'); return; }
    if (prompt) {
      prompt.value = text;
      prompt.focus();
    }
    var result = $('#prompt-result');
    if (result) {
      result.className = 'test-result ok';
      result.innerHTML = icon('check-circle') + '<span>已填入默认提示词，保存后生效</span>';
    }
  }

  async function resetPrompt() {
    var btn = $('#btn-reset-prompt');
    if (btn) btn.disabled = true;
    try {
      var saved = await apiJson('/api/settings/reset-prompt', { method: 'POST' });
      if (saved && typeof saved === 'object') {
        state.settings = Object.assign(defaultSettings(), saved, { llmApiKey: '', mtApiKey: '' });
        renderSettings();
        syncTargetLang();
        var result = $('#prompt-result');
        if (result) {
          result.className = 'test-result ok';
          result.innerHTML = icon('check-circle') + '<span>已恢复内置默认提示词</span>';
        }
        toast('已恢复默认提示词', 'success');
      } else {
        var prompt = $('#set-llm-prompt');
        if (prompt) prompt.value = '';
        toast('已恢复默认提示词', 'success');
      }
    } catch (err) {
      toast('恢复默认提示词失败：' + err.message, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function setTestResult(sel, ok, message) {
    var node = $(sel);
    if (!node) return;
    node.className = 'test-result ' + (ok ? 'ok' : 'err');
    node.innerHTML = icon(ok ? 'check-circle' : 'alert') + '<span>' + escapeHtml(message) + '</span>';
  }

  async function testLlm() {
    var btn = $('#btn-test-llm');
    if (btn) btn.disabled = true;
    setTestResult('#test-llm-result', true, '正在测试…');
    try {
      var res = await apiJson('/api/settings/test-llm', { method: 'POST', body: readSettingsForm() });
      if (res && res.ok) setTestResult('#test-llm-result', true, res.message || ('连接成功：' + (res.model || '')));
      else setTestResult('#test-llm-result', false, (res && res.message) || '连接失败');
    } catch (err) {
      setTestResult('#test-llm-result', false, err.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function testMt() {
    var btn = $('#btn-test-machine');
    if (btn) btn.disabled = true;
    setTestResult('#test-machine-result', true, '正在测试…');
    try {
      var res = await apiJson('/api/settings/test-mt', { method: 'POST', body: { provider: val('#set-machine-provider') || 'auto' } });
      if (res && res.ok) setTestResult('#test-machine-result', true, res.message || ('可用：' + (res.provider || '')));
      else setTestResult('#test-machine-result', false, (res && res.message) || '测试失败');
    } catch (err) {
      setTestResult('#test-machine-result', false, err.message);
    } finally {
      if (btn) btn.disabled = false;
    }
  }


  /* ============================================================
     事件绑定
     ============================================================ */
  function bindEvents() {
    /* 导航 */
    $$('.nav-item').forEach(function (item) {
      item.addEventListener('click', function () {
        var page = item.getAttribute('data-page');
        if (page === 'settings') { showPage('settings'); return; }
        showPage(page);
        if (page === 'mods') { backToModList(); }
      });
    });

    /* 顶部栏 */
    var themeBtn = $('#btn-theme');
    if (themeBtn) themeBtn.addEventListener('click', toggleTheme);
    var settingsBtn = $('#btn-goto-settings');
    if (settingsBtn) settingsBtn.addEventListener('click', function () { showPage('settings'); });
    var pickBtn = $('#btn-pick-folder');
    if (pickBtn) pickBtn.addEventListener('click', function () {
      if (state.workspace && state.workspace.path) revealWorkspace();
      else pickWorkspaceFolder();
    });
    var chip = $('#workspace-chip');
    if (chip) chip.addEventListener('click', function () { pickWorkspaceFolder(); });
    var sidePick = $('#btn-sidebar-pick');
    if (sidePick) sidePick.addEventListener('click', function () { pickWorkspaceFolder(); });
    var revealBtn = $('#btn-sidebar-reveal');
    if (revealBtn) revealBtn.addEventListener('click', revealWorkspace);
    var browseBtn = $('#btn-sidebar-browse');
    if (browseBtn) browseBtn.addEventListener('click', function () { openBrowseModal(); });

    /* 空状态按钮 */
    var emptyPick = $('#btn-empty-pick');
    if (emptyPick) emptyPick.addEventListener('click', function () { pickWorkspaceFolder(); });
    var kubejsPick = $('#btn-kubejs-pick');
    if (kubejsPick) kubejsPick.addEventListener('click', function () { pickWorkspaceFolder(); });
    var ftbPick = $('#btn-ftb-pick');
    if (ftbPick) ftbPick.addEventListener('click', function () { pickWorkspaceFolder(); });

    /* 刷新 */
    var modsRefresh = $('#btn-mods-refresh');
    if (modsRefresh) modsRefresh.addEventListener('click', refreshWorkspace);
    var kubejsRefresh = $('#btn-kubejs-refresh');
    if (kubejsRefresh) kubejsRefresh.addEventListener('click', refreshWorkspace);
    var ftbRefresh = $('#btn-ftb-refresh');
    if (ftbRefresh) ftbRefresh.addEventListener('click', refreshWorkspace);

    /* 模组页搜索 */
    var modsSearch = $('#mods-search');
    if (modsSearch) modsSearch.addEventListener('input', debounce(function () {
      state.modsFilter.q = modsSearch.value;
      renderModGrid((state.workspace && state.workspace.mods && state.workspace.mods.mods) || []);
    }, 120));
    var modsOnly = $('#mods-only-missing');
    if (modsOnly) modsOnly.addEventListener('change', function () {
      state.modsFilter.onlyMissing = modsOnly.checked;
      renderModGrid((state.workspace && state.workspace.mods && state.workspace.mods.mods) || []);
    });

    /* 模组卡片 / 语言文件 */
    var grid = $('#mod-grid');
    if (grid) {
      grid.addEventListener('click', function (ev) {
        var card = ev.target.closest('[data-mod]');
        if (card) openModDetail(card.getAttribute('data-mod'));
      });
      grid.addEventListener('keydown', function (ev) {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        var card = ev.target.closest('[data-mod]');
        if (card) { ev.preventDefault(); openModDetail(card.getAttribute('data-mod')); }
      });
    }
    var modFiles = $('#mod-files');
    if (modFiles) {
      modFiles.addEventListener('click', function (ev) {
        var row = ev.target.closest('[data-file]');
        if (!row) return;
        openDoc({
          kind: 'mod',
          file: row.getAttribute('data-file'),
          namespace: row.getAttribute('data-ns'),
          lang: row.getAttribute('data-lang')
        });
      });
    }
    var modBack = $('#btn-mod-back');
    if (modBack) modBack.addEventListener('click', backToModList);

    /* KubeJS / FTB 列表 */
    bindLangList('#kubejs-files', 'kubejs');
    bindLangList('#ftb-files', 'ftb');

    var kubejsSearch = $('#kubejs-search');
    if (kubejsSearch) kubejsSearch.addEventListener('input', debounce(function () {
      state.kubejsFilter.q = kubejsSearch.value;
      renderKubejsPage();
    }, 120));
    var ftbSearch = $('#ftb-search');
    if (ftbSearch) ftbSearch.addEventListener('input', debounce(function () {
      state.ftbFilter.q = ftbSearch.value;
      renderFtbPage();
    }, 120));

    var kubejsExportLang = $('#btn-kubejs-export-lang');
    if (kubejsExportLang) kubejsExportLang.addEventListener('click', function () { exportListLang('kubejs'); });
    var ftbExportLang = $('#btn-ftb-export-lang');
    if (ftbExportLang) ftbExportLang.addEventListener('click', function () { exportListLang('ftb'); });
    var kubejsOverlay = $('#btn-kubejs-overlay');
    if (kubejsOverlay) kubejsOverlay.addEventListener('click', function () { openListOverlay('kubejs'); });
    var ftbOverlay = $('#btn-ftb-overlay');
    if (ftbOverlay) ftbOverlay.addEventListener('click', function () { openListOverlay('ftb'); });

    /* 目录浏览器 */
    var browseList = $('#browse-list');
    if (browseList) {
      browseList.addEventListener('click', function (ev) {
        var item = ev.target.closest('[data-path]');
        if (!item) return;
        state.browse.selected = item.getAttribute('data-path');
        $$('#browse-list .browse-item').forEach(function (n) { n.classList.toggle('selected', n === item); });
        updateBrowseFooter();
      });
      browseList.addEventListener('dblclick', function (ev) {
        var item = ev.target.closest('[data-path]');
        if (!item) return;
        browseTo(item.getAttribute('data-path'));
      });
    }
    var browseUp = $('#browse-up');
    if (browseUp) browseUp.addEventListener('click', function () {
      if (state.browse.parent) browseTo(state.browse.parent);
    });
    var browseRefresh = $('#browse-refresh');
    if (browseRefresh) browseRefresh.addEventListener('click', function () { browseTo(state.browse.path); });
    var browseDrive = $('#browse-drive');
    if (browseDrive) browseDrive.addEventListener('change', function () { browseTo(browseDrive.value); });
    var browseConfirm = $('#browse-confirm');
    if (browseConfirm) browseConfirm.addEventListener('click', async function () {
      if (isScanning()) {
        toast('正在解析中，请先取消当前解析再重新选择', 'warn');
        return;
      }
      var target = state.browse.selected || state.browse.path;
      if (!target) { toast('请选择一个目录', 'warn'); return; }
      closeModal('overlay-browse');
      await openWorkspacePath(target);
    });
    var browseSuggest = $('#browse-suggest');
    if (browseSuggest) browseSuggest.addEventListener('click', function (ev) {
      var chip = ev.target.closest('[data-suggest]');
      if (chip) browseTo(chip.getAttribute('data-suggest'));
    });

    /* 导出文件选择弹窗 */
    var pickList = $('#pick-file-list');
    if (pickList) {
      pickList.addEventListener('click', function (ev) {
        var row = ev.target.closest('[data-pick]');
        if (!row) return;
        state.pickFile.selected = row.getAttribute('data-pick');
        $$('#pick-file-list .file-row').forEach(function (n) { n.classList.toggle('selected', n === row); });
      });
      pickList.addEventListener('dblclick', function (ev) {
        var row = ev.target.closest('[data-pick]');
        if (!row) return;
        state.pickFile.selected = row.getAttribute('data-pick');
        confirmPickFile();
      });
    }
    var pickConfirm = $('#pick-file-confirm');
    if (pickConfirm) pickConfirm.addEventListener('click', confirmPickFile);

    /* 解析进度弹窗 */
    var scanCancel = $('#btn-scan-cancel');
    if (scanCancel) scanCancel.addEventListener('click', cancelScan);
    var scanRetry = $('#btn-scan-retry');
    if (scanRetry) scanRetry.addEventListener('click', retryScan);
    var scanClose = $('#btn-scan-close');
    if (scanClose) scanClose.addEventListener('click', function () {
      closeModal('overlay-scan');
      setScanLock(false);
      state.scan.id = null;
      state.scan.data = null;
      if (!(state.workspace && state.workspace.path)) { applyWorkspacePath('', ''); }
      else renderWorkspaceChrome();
    });
    var scanOverlay = $('#overlay-scan');
    if (scanOverlay) {
      scanOverlay.addEventListener('click', function (ev) {
        if (ev.target !== scanOverlay) return;
        if (isScanning()) {
          // 遮罩层挡住页面按钮，用户仍试图操作时给出明确提示
          toast('正在解析中，请先取消当前解析再重新选择', 'warn');
          return;
        }
        closeModal('overlay-scan');
      });
    }

    /* 目录不合法弹窗：候选目录点选 */
    var invalidHost = $('#ws-invalid-candidates');
    if (invalidHost) {
      invalidHost.addEventListener('click', function (ev) {
        var chip = ev.target.closest('[data-candidate]');
        if (!chip) return;
        closeModal('overlay-ws-invalid');
        openWorkspacePath(chip.getAttribute('data-candidate'));
      });
    }
    var invalidBrowse = $('#btn-ws-invalid-browse');
    if (invalidBrowse) invalidBrowse.addEventListener('click', function () {
      closeModal('overlay-ws-invalid');
      openBrowseModal();
    });

    /* 覆盖包弹窗：范围切换 */
    var packScope = $('#pack-scope');
    if (packScope) packScope.addEventListener('change', function () {
      state.pack.scope = packScope.value;
      updateOverlaySummary();
    });

    /* 资源包弹窗 */
    var packConfirm = $('#pack-confirm');
    if (packConfirm) packConfirm.addEventListener('click', submitPackModal);

    /* 通用关闭 */
    document.addEventListener('click', function (ev) {
      var closer = ev.target.closest('[data-close]');
      if (closer) { closeModal(closer.getAttribute('data-close')); return; }
      if (ev.target.classList && ev.target.classList.contains('overlay')) {
        if (ev.target.id === 'overlay-scan' && isScanning()) {
          toast('正在解析中，请先取消当前解析再重新选择', 'warn');
          return;
        }
        closeModal(ev.target.id);
      }
    });
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') closeTopModal();
    });

    /* 文件页 */
    var fileBack = $('#btn-file-back');
    if (fileBack) fileBack.addEventListener('click', function () {
      var kind = state.currentDesc ? state.currentDesc.kind : 'mods';
      showPage(kind === 'ftb' ? 'ftb' : kind);
    });
    var fileExportLang = $('#btn-file-export-lang');
    if (fileExportLang) fileExportLang.addEventListener('click', function () { exportLangDoc(state.doc, state.currentDesc); });
    var fileExportPack = $('#btn-file-export-pack');
    if (fileExportPack) fileExportPack.addEventListener('click', function () { openFilePack(); });

    /* 翻译工具条 */
    $$('#seg-mode .seg-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        state.translateMode = btn.getAttribute('data-value');
        $$('#seg-mode .seg-btn').forEach(function (b) { b.classList.toggle('active', b === btn); });
      });
    });
    var glossaryToggle = $('#btn-glossary-toggle');
    if (glossaryToggle) glossaryToggle.addEventListener('click', function () {
      var panel = $('#glossary-panel');
      if (panel) panel.hidden = !panel.hidden;
    });
    var glossaryClose = $('#btn-glossary-close');
    if (glossaryClose) glossaryClose.addEventListener('click', function () {
      var panel = $('#glossary-panel');
      if (panel) panel.hidden = true;
    });
    var startBtn = $('#btn-start-translate');
    if (startBtn) startBtn.addEventListener('click', startTranslate);
    var jobCancel = $('#btn-job-cancel');
    if (jobCancel) jobCancel.addEventListener('click', cancelJob);
    var jobClose = $('#btn-job-close');
    if (jobClose) jobClose.addEventListener('click', function () { setJobPanelVisible(false); });

    /* 提问弹窗 */
    var qList = $('#questions-list');
    if (qList) {
      qList.addEventListener('click', function (ev) {
        var opt = ev.target.closest('.q-option');
        if (!opt) return;
        var card = opt.closest('.question-card');
        if (!card) return;
        $$('.q-option', card).forEach(function (o) { o.classList.toggle('selected', o === opt); });
        var input = card.querySelector('.q-answer');
        if (input) input.value = opt.getAttribute('data-value') || '';
      });
    }
    var qSubmit = $('#questions-submit');
    if (qSubmit) qSubmit.addEventListener('click', submitAnswers);
    var qSkip = $('#questions-skip');
    if (qSkip) qSkip.addEventListener('click', function () { closeModal('overlay-questions'); });

    /* 键值表 */
    var kvSearch = $('#kv-search');
    if (kvSearch) kvSearch.addEventListener('input', debounce(function () {
      state.kv.q = kvSearch.value;
      state.kv.limit = ROW_LIMIT_STEP;
      renderKvTable();
    }, 140));
    var onlyUntranslated = $('#chk-only-untranslated');
    if (onlyUntranslated) onlyUntranslated.addEventListener('change', function () {
      state.kv.onlyUntranslated = onlyUntranslated.checked;
      state.kv.limit = ROW_LIMIT_STEP;
      renderKvTable();
    });
    var selectAll = $('#chk-select-all');
    if (selectAll) selectAll.addEventListener('change', function () {
      var rows = filteredEntries();
      rows.forEach(function (e) { state.kv.selected[e.key] = selectAll.checked; });
      renderKvTable();
    });
    var tbody = $('#kv-tbody');
    if (tbody) {
      tbody.addEventListener('input', function (ev) {
        var input = ev.target;
        if (!input.classList || !input.classList.contains('kv-input')) return;
        var key = input.getAttribute('data-key');
        var doc = state.doc;
        if (!doc || key === null) return;
        var targets = targetsFor(doc);
        var value = input.value;
        if (value === '' && has(doc.existing || {}, key)) targets[key] = '';
        else targets[key] = value;
        input.classList.toggle('dirty', value !== (has(doc.existing || {}, key) ? doc.existing[key] : ''));
        var row = input.closest('tr');
        if (row) {
          var status = row.querySelector('.kv-status');
          if (status) status.innerHTML = statusBadge(value, key);
          row.classList.toggle('is-untranslated', !value);
        }
        autosize(input);
        updateKvStats();
      });
      tbody.addEventListener('change', function (ev) {
        var box = ev.target;
        if (!box.classList || !box.classList.contains('kv-check')) return;
        var row = box.closest('tr');
        if (!row) return;
        var key = row.getAttribute('data-key');
        state.kv.selected[key] = box.checked;
      });

    }
    var tableScroll = $('.table-scroll');
    if (tableScroll) {
      tableScroll.addEventListener('click', function (ev) {
        var btn = ev.target.closest('#btn-kv-more');
        if (!btn) return;
        state.kv.limit += ROW_LIMIT_STEP;
        renderKvTable();
      });
    }
    var copySelected = $('#btn-copy-selected');
    if (copySelected) copySelected.addEventListener('click', function () {
      var doc = state.doc;
      if (!doc) return;
      var keys = Object.keys(state.kv.selected).filter(function (k) { return state.kv.selected[k]; });
      if (!keys.length) { toast('请先勾选要复制的条目', 'warn'); return; }
      var lines = keys.map(function (k) { return k + '\t' + targetOf(k); });
      copyText(lines.join('\n')).then(function () { toast('已复制 ' + keys.length + ' 条', 'success'); });
    });
    var copyAll = $('#btn-copy-all');
    if (copyAll) copyAll.addEventListener('click', function () {
      var doc = state.doc;
      if (!doc) return;
      var obj = {};
      (doc.entries || []).forEach(function (e) {
        var v = targetOf(e.key);
        if (v) obj[e.key] = v;
      });
      var count = Object.keys(obj).length;
      if (!count) { toast('还没有任何译文', 'warn'); return; }
      copyText(JSON.stringify(obj, null, 2)).then(function () { toast('已复制 ' + count + ' 条译文（JSON）', 'success'); });
    });

    /* 一键还原 */
    var restoreBtn = $('#btn-restore-targets');
    if (restoreBtn) restoreBtn.addEventListener('click', function () {
      var doc = state.doc;
      if (!doc) { toast('请先打开一个语言文件', 'warn'); return; }
      confirmDialog('还原译文', '将把当前文件在本会话中的<b>全部译文</b>清空，回到文件中已有的 zh_cn（若有）。此操作不可撤销，是否继续？', '还原')
        .then(function (ok) { if (ok) restoreTargets(); });
    });
    var jobApply = $('#btn-job-apply');
    if (jobApply) jobApply.addEventListener('click', function () { applyJobResults(state.job.data); });
    var jobDiscard = $('#btn-job-discard');
    if (jobDiscard) jobDiscard.addEventListener('click', function () { discardJobResults(state.job.data); });
    var jobRestoreAll = $('#btn-job-restore-all');
    if (jobRestoreAll) jobRestoreAll.addEventListener('click', function () {
      var doc = state.doc;
      if (!doc) { toast('请先打开一个语言文件', 'warn'); return; }
      confirmDialog('全部还原', '将清空当前文件的<b>所有译文</b>（含本次翻译结果），是否继续？', '全部还原')
        .then(function (ok) { if (ok) restoreTargets(); });
    });

    /* 设置页 */
    var saveBtn = $('#btn-settings-save');
    if (saveBtn) saveBtn.addEventListener('click', saveSettings);
    var reloadBtn = $('#btn-settings-reload');
    if (reloadBtn) reloadBtn.addEventListener('click', loadSettings);
    var testLlmBtn = $('#btn-test-llm');
    if (testLlmBtn) testLlmBtn.addEventListener('click', testLlm);
    var testMtBtn = $('#btn-test-machine');
    if (testMtBtn) testMtBtn.addEventListener('click', testMt);
    var tempRange = $('#set-llm-temp');
    if (tempRange) tempRange.addEventListener('input', updateTempLabel);
    var fillPrompt = $('#btn-fill-default-prompt');
    if (fillPrompt) fillPrompt.addEventListener('click', fillDefaultPrompt);
    var resetPromptBtn = $('#btn-reset-prompt');
    if (resetPromptBtn) resetPromptBtn.addEventListener('click', resetPrompt);
    var toggleKey = $('#btn-toggle-apikey');
    if (toggleKey) toggleKey.addEventListener('click', function () {
      var input = $('#set-llm-apikey');
      if (!input) return;
      var isPassword = input.type === 'password';
      input.type = isPassword ? 'text' : 'password';
      toggleKey.textContent = isPassword ? '隐藏' : '显示';
    });
    $$('.theme-choice').forEach(function (btn) {
      btn.addEventListener('click', function () { applyTheme(btn.getAttribute('data-theme-value')); });
    });
  }

  function bindLangList(sel, kind) {
    var node = $(sel);
    if (!node) return;
    node.addEventListener('click', function (ev) {
      var row = ev.target.closest('[data-path]');
      if (!row) return;
      var path = row.getAttribute('data-path');
      var dl = ev.target.closest('.file-dl');
      if (dl) {
        ev.stopPropagation();
        exportSingleFile(kind, path);
        return;
      }
      openDoc({ kind: kind, path: path });
    });
  }

  async function exportSingleFile(kind, path) {
    try {
      var doc = await ensureDoc({ kind: kind, path: path });
      await exportLangDoc(doc, { kind: kind, path: path });
    } catch (err) {
      toast('导出失败：' + err.message, 'error');
    }
  }

  function listFilesFor(kind) {
    var ws = state.workspace;
    if (!ws) return [];
    if (kind === 'kubejs') return sourceLangFiles((ws.kubejs && ws.kubejs.files) || []);
    return sourceLangFiles((ws.ftb && ws.ftb.files) || []);
  }

  async function exportListLang(kind) {
    var files = listFilesFor(kind);
    if (!files.length) { toast('没有可导出的语言文件', 'warn'); return; }
    if (files.length === 1) { await exportSingleFile(kind, files[0].relativePath); return; }
    openPickFileModal(kind, files);
  }

  function openPickFileModal(kind, files) {
    state.pickFile = { kind: kind, files: files, selected: files[0] ? files[0].relativePath : '' };
    var title = $('#pick-file-title');
    if (title) title.textContent = '选择要导出的语言文件';
    var sub = $('#pick-file-sub');
    if (sub) sub.textContent = (kind === 'kubejs' ? 'KubeJS' : 'FTB Quests') + ' 共有 ' + files.length + ' 个语言文件，请选择其中一个导出 zh_cn.json。';
    var list = $('#pick-file-list');
    if (list) {
      list.innerHTML = files.map(function (file) {
        var selected = state.pickFile.selected === file.relativePath;
        return '<div class="file-row' + (selected ? ' selected' : '') + '" data-pick="' + escapeHtml(file.relativePath) + '">' +
          '<div class="file-icon">' + icon(kind === 'kubejs' ? 'code' : 'book') + '</div>' +
          '<div class="file-main">' +
            '<div class="file-name">' + escapeHtml(kind === 'kubejs' ? ((file.namespace || 'kubejs') + ' · ' + (file.lang || 'en_us') + '.json') : ((file.relativePath || '').split('/').pop() || (file.lang || 'en_us') + '.snbt')) + '</div>' +
            '<div class="file-path">' + escapeHtml(file.relativePath || '') + '</div>' +
          '</div>' +
          '<div class="file-badges"><span class="badge badge-mono">' + escapeHtml(fmtNumber(file.entryCount || 0)) + ' 条</span></div>' +
          '</div>';
      }).join('');
    }
    openModal('overlay-pick-file');
  }

  async function confirmPickFile() {
    var pick = state.pickFile;
    if (!pick || !pick.selected) { toast('请选择要导出的语言文件', 'warn'); return; }
    closeModal('overlay-pick-file');
    await exportSingleFile(pick.kind, pick.selected);
  }

  function openListOverlay(kind) {
    var files = listFilesFor(kind);
    if (!files.length) { toast('没有可打包的语言文件', 'warn'); return; }
    openPackModal(kind === 'kubejs' ? 'kubejs-overlay' : 'ftb-overlay', {
      kind: kind,
      files: files.map(function (f) { return { relativePath: f.relativePath }; }),
      scope: 'all',
      targetLanguage: (state.settings && state.settings.targetLanguage) || 'zh_cn',
      summary: '将打包 ' + files.length + ' 个语言文件（解压到整合包根目录覆盖即可）'
    });
  }

  function openFilePack() {
    var doc = state.doc;
    var desc = state.currentDesc;
    if (!doc || !desc) { toast('没有可导出的文档', 'warn'); return; }
    if (doc.kind === 'mod') {
      openPackModal('resourcepack', {
        desc: desc,
        defaultName: (doc.displayName || '汉化') + ' 汉化资源包',
        summary: '将打包 ' + fmtNumber((doc.entries || []).length) + ' 条译文与 pack.mcmeta'
      });
    } else {
      openPackModal(doc.kind === 'kubejs' ? 'kubejs-overlay' : 'ftb-overlay', {
        kind: doc.kind,
        files: [{ relativePath: doc.relativePath, desc: desc }],
        scope: 'current',
        targetLanguage: doc.targetLanguage || 'zh_cn',
        summary: '将打包 ' + fmtNumber((doc.entries || []).length) + ' 条译文（解压到整合包根目录覆盖即可）'
      });
    }
  }

  /* ============================================================
     启动
     ============================================================ */
  async function init() {
    applyTheme(readStoredTheme());
    bindEvents();
    await loadSettings();

    var ws = null;
    try {
      ws = await apiJson('/api/workspace');
    } catch (err) {
      ws = null;
    }

    // 首次加载时若后端仍在扫描，直接接上现有 scanId 继续显示进度
    if (ws && ws.scanning && ws.scanId) {
      state.scan.id = ws.scanId;
      state.scan.path = ws.path || '';
      state.scan.name = ws.name || '';
      state.scan.cancelling = false;
      state.scan.cancelDeadline = 0;
      applyWorkspacePath(state.scan.path, state.scan.name);
      setScanLock(true);
      renderScanProgress({ scanId: ws.scanId, path: state.scan.path, name: state.scan.name, status: 'running', phaseLabel: '正在解析…', percent: 0, elapsedMs: 0 });
      openModal('overlay-scan');
      startScanPolling();
    } else if (!ws || !ws.path) {
      var last = state.settings && state.settings.lastWorkspace;
      if (last) {
        try { ws = await startScan(last); } catch (err2) { ws = null; }
        // startScan 只返回启动信息，完整数据在扫描完成后拉取
        if (!isScanning()) {
          try { ws = await apiJson('/api/workspace'); } catch (err3) { ws = null; }
        } else {
          ws = null;
        }
      }
    }
    setWorkspace(ws && ws.path ? ws : null);

    /* 健康检查（仅本地，用于确认后端在线） */
    try {
      var health = await apiJson('/api/health');
      if (health && health.version) {
        var versionNode = $('.sidebar-version');
        if (versionNode) versionNode.textContent = 'v' + health.version + ' · 离线运行';
      }
    } catch (err3) { /* ignore */ }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // 仅供自动化测试（无头浏览器）驱动的少量入口；不影响正常使用。
  window.__dstTest = {
    startScan: startScan,
    openWorkspacePath: openWorkspacePath,
    isScanning: isScanning,
    state: state,
    openBrowseModal: openBrowseModal,
    showInvalidWorkspace: showInvalidWorkspace
  };
})();

'use strict';

/* ------------------------------------------------------------------ 常量 */
const POINTS = 120;      // 60 秒 @ 2 Hz
const STEP_MS = 500;

const COLOR = {
  cpu: '#3ba6f2',
  mem: '#8b6be0',
  disk: '#2fbf5a',
  read: '#2fbf5a',
  write: '#e2604f',
  rx: '#3ba6f2',
  tx: '#e2604f',
  gpu: '#3ba6f2',
};

const KB = 1024;
const MB = KB * 1024;
const GB = MB * 1024;

/* ------------------------------------------------------------------ 状态 */
const state = {
  static: {},
  current: {},
  history: {},
  diskHistory: {},
  selected: 'cpu',
  page: 'performance',
  lastFetch: performance.now(),
  tileSig: '',
  detailSig: '',
  tileNodes: new Map(),
  statNodes: [],
  rect: null,
};

/* ------------------------------------------------------------------ DOM */
const el = {
  tiles: document.getElementById('tiles'),
  detail: document.getElementById('detail'),
  title: document.getElementById('page-title'),
  toast: document.getElementById('toast'),
};

/* ------------------------------------------------------------------ 工具 */
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const pad2 = (n) => String(n).padStart(2, '0');

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function fmtBytes(n) {
  if (!n || n < 0) return '0 B';
  if (n >= GB) return (n / GB).toFixed(1) + ' GB';
  if (n >= MB) return (n / MB).toFixed(1) + ' MB';
  if (n >= KB) return (n / KB).toFixed(0) + ' KB';
  return Math.round(n) + ' B';
}

function fmtGB(n) {
  return (n / GB).toFixed(1);
}

/** 网络速率：任务管理器用比特，单位只写到 Mb 一级。 */
function fmtRate(bytesPerSec) {
  const bits = (bytesPerSec || 0) * 8;
  if (bits >= 1e9) return (bits / 1e9).toFixed(1) + ' Gb';
  if (bits >= 1e6) return (bits / 1e6).toFixed(1) + ' Mb';
  if (bits >= 1e3) return (bits / 1e3).toFixed(1) + ' Kb';
  return Math.round(bits) + ' b';
}

function fmtDiskRate(bytesPerSec) {
  const v = bytesPerSec || 0;
  if (v >= MB) return (v / MB).toFixed(1) + ' MB/秒';
  if (v >= KB) return (v / KB).toFixed(1) + ' KB/秒';
  return Math.round(v) + ' 字节/秒';
}

function fmtUptime(sec) {
  const s = Math.floor(sec || 0);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${d}:${pad2(h)}:${pad2(m)}:${pad2(s % 60)}`;
}

function fmtCache(bytes) {
  if (!bytes) return '—';
  return bytes >= MB ? (bytes / MB).toFixed(1) + ' MB' : Math.round(bytes / KB) + ' KB';
}

function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

/* ------------------------------------------------------------------ 绘图 */
function niceMax(v) {
  if (!v || v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / exp;
  const step = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return step * exp;
}

function drawPlot(canvas, series, opts) {
  const rect = canvas.getBoundingClientRect();
  const w = rect.width;
  const h = rect.height;
  if (w < 2 || h < 2) return;

  const dpr = window.devicePixelRatio || 1;
  const pw = Math.round(w * dpr);
  const ph = Math.round(h * dpr);
  if (canvas.width !== pw || canvas.height !== ph) {
    canvas.width = pw;
    canvas.height = ph;
  }

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const prog = state.progress || 0;
  const step = w / (POINTS - 1);

  if (opts.grid) {
    ctx.strokeStyle = '#e8edf2';
    ctx.lineWidth = 1;
    for (let k = 1; k < 5; k++) {
      const x = Math.round(w - (w / 5) * k) + 0.5;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, h);
      ctx.stroke();
    }
    for (let k = 1; k < 4; k++) {
      const y = Math.round((h / 4) * k) + 0.5;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(w, y);
      ctx.stroke();
    }
  }

  let max = opts.max;
  if (!max) {
    let m = 0;
    for (const s of series) for (const v of s.data) if (v > m) m = v;
    max = niceMax(m * 1.15);
  }
  if (max <= 0) max = 1;

  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, w, h);
  ctx.clip();

  for (const s of series) {
    const d = s.data;
    const n = d.length;
    if (n < 2) continue;

    const X = (i) => w - (n - 1 - i) * step - prog * step;
    const Y = (v) => h - clamp(v / max, 0, 1) * h;

    if (s.fill) {
      ctx.beginPath();
      ctx.moveTo(X(0), Y(d[0]));
      for (let i = 1; i < n; i++) ctx.lineTo(X(i), Y(d[i]));
      ctx.lineTo(X(n - 1), h);
      ctx.lineTo(X(0), h);
      ctx.closePath();
      const g = ctx.createLinearGradient(0, 0, 0, h);
      g.addColorStop(0, hexA(s.color, 0.34));
      g.addColorStop(1, hexA(s.color, 0.07));
      ctx.fillStyle = g;
      ctx.fill();
    }

    if (s.dashed) ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(X(0), Y(d[0]));
    for (let i = 1; i < n; i++) ctx.lineTo(X(i), Y(d[i]));
    ctx.strokeStyle = s.color;
    ctx.lineWidth = s.width || 1.5;
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.stroke();
    ctx.setLineDash([]);
  }

  ctx.restore();
}

/* ------------------------------------------------------------------ 卡片 */
function buildTiles() {
  const cur = state.current;
  const st = state.static;
  if (!cur.cpu) return [];

  const list = [];

  list.push({
    id: 'cpu',
    name: 'CPU',
    lines: [`${cur.cpu.usage}%  ${cur.cpu.speed.toFixed(2)} GHz`],
    series: [{ data: state.history.cpu || [], color: COLOR.cpu, fill: true }],
    max: 100,
  });

  list.push({
    id: 'mem',
    name: '内存',
    lines: [`${fmtGB(cur.mem.used)}/${fmtGB(cur.mem.total)} GB (${Math.round(cur.mem.percent)}%)`],
    series: [{ data: state.history.mem || [], color: COLOR.mem, fill: true }],
    max: 100,
  });

  for (const d of Object.values(cur.disks || {})) {
    const meta = (st.disks || {})[d.index] || {};
    const kind = [meta.type, meta.bus ? `(${meta.bus})` : ''].filter(Boolean).join(' ');
    const hist = (state.diskHistory || {})[d.letter || ''] || {};
    list.push({
      id: 'disk:' + (d.letter || d.index),
      name: d.label,
      lines: [kind || '磁盘', `${Math.round(d.active)}%`],
      series: [{ data: hist.active || [], color: COLOR.disk, fill: true }],
      max: 100,
    });
  }

  list.push({
    id: 'net',
    name: 'Wi-Fi',
    lines: [
      'WLAN',
      `发送: ${fmtRate(cur.net.tx)} 接收: ${fmtRate(cur.net.rx)}`,
    ],
    series: [
      { data: state.history.net_rx || [], color: COLOR.rx },
      { data: state.history.net_tx || [], color: COLOR.tx },
    ],
    max: null,
  });

  list.push({
    id: 'gpu',
    name: 'GPU 0',
    lines: [st.gpu_name || 'GPU', `${cur.gpu.util}% (${cur.gpu.temp} °C)`],
    series: [{ data: state.history.gpu || [], color: COLOR.gpu, fill: true }],
    max: 100,
  });

  return list;
}

function renderTiles() {
  const defs = buildTiles();
  const sig = defs.map((d) => d.id).join('|');

  if (sig !== state.tileSig) {
    state.tileSig = sig;
    state.tileNodes.clear();
    el.tiles.innerHTML = '';
    for (const d of defs) {
      const btn = document.createElement('button');
      btn.className = 'tile';
      btn.dataset.id = d.id;
      btn.innerHTML =
        `<span class="tile-thumb"><canvas></canvas></span>` +
        `<span class="tile-info"><span class="tile-name">${esc(d.name)}</span>` +
        `<span class="tile-value"></span></span>`;
      btn.addEventListener('click', () => select(d.id));
      el.tiles.appendChild(btn);
      state.tileNodes.set(d.id, {
        root: btn,
        values: btn.querySelector('.tile-value'),
        canvas: btn.querySelector('canvas'),
      });
    }
  }

  for (const d of defs) {
    const node = state.tileNodes.get(d.id);
    if (!node) continue;
    node.values.innerHTML = d.lines.map((l) => `<span>${esc(l)}</span>`).join('');
    node.root.classList.toggle('active', state.selected === d.id);
    node.tileData = d;
  }
}

/* ------------------------------------------------------------------ 详情 */
function diskEntry(key) {
  const disks = state.current.disks || {};
  return Object.values(disks).find((d) => (d.letter || d.index) === key) || null;
}

function buildDetail() {
  const cur = state.current;
  const st = state.static;
  if (!cur.cpu) return null;
  const sel = state.selected;

  if (sel === 'cpu') {
    return {
      sig: 'cpu',
      title: 'CPU',
      subtitle: st.cpu_name || '',
      chartLabel: '% 利用率',
      chartMax: '100%',
      series: [{ data: state.history.cpu || [], color: COLOR.cpu, fill: true }],
      max: 100,
      cols: [
        [
          { l: '利用率', v: `${cur.cpu.usage}%` },
          { l: '速度', v: `${cur.cpu.speed.toFixed(2)} GHz` },
          { l: '进程', v: cur.cpu.processes },
          { l: '线程', v: cur.cpu.threads },
          { l: '句柄', v: cur.cpu.handles },
          { l: '正常运行时间', v: fmtUptime(cur.cpu.uptime) },
        ],
        [
          { l: '基准速度', v: `${(st.cpu_base_mhz / 1000 || 0).toFixed(2)} GHz` },
          { l: '插槽', v: st.sockets || 1 },
          { l: '内核', v: st.physical_cores },
          { l: '逻辑处理器', v: st.logical_cores },
          { l: '虚拟化', v: st.virt || '—' },
          { l: 'L1 缓存', v: fmtCache(st.l1), dense: true },
          { l: 'L2 缓存', v: fmtCache(st.l2), dense: true },
          { l: 'L3 缓存', v: fmtCache(st.l3), dense: true },
        ],
      ],
    };
  }

  if (sel === 'mem') {
    return {
      sig: 'mem',
      title: '内存',
      subtitle: `${fmtGB(cur.mem.total)} GB`,
      chartLabel: '内存使用量',
      chartMax: `${fmtGB(cur.mem.total)} GB`,
      series: [{ data: state.history.mem || [], color: COLOR.mem, fill: true }],
      max: 100,
      cols: [
        [
          { l: '使用中', v: `${fmtGB(cur.mem.used)} GB` },
          { l: '可用', v: `${fmtGB(cur.mem.free)} GB` },
          { l: '已提交', v: `${fmtGB(cur.mem.committed)}/${fmtGB(cur.mem.commit_limit)} GB` },
          { l: '已缓存', v: `${fmtGB(cur.mem.cached)} GB` },
        ],
        [
          { l: '速度', v: `${st.mem_speed || '—'} MHz` },
          { l: '插槽', v: `${st.mem_used_slots}/${st.mem_slots}` },
          { l: '外形规格', v: st.mem_form || '—' },
          { l: '已使用比例', v: `${Math.round(cur.mem.percent)}%` },
        ],
      ],
    };
  }

  if (sel.startsWith('disk:')) {
    const key = sel.slice(5);
    const d = diskEntry(key);
    if (!d) return null;
    const meta = (st.disks || {})[d.index] || {};
    const hist = (state.diskHistory || {})[key] || {};
    const totalBytes = d.total || meta.size || 0;
    return {
      sig: sel,
      title: d.label,
      subtitle: meta.model || '',
      chartLabel: '磁盘传输速率',
      chartMax: '',
      series: [
        { data: hist.read || [], color: COLOR.read },
        { data: hist.write || [], color: COLOR.write },
      ],
      max: null,
      legend: [
        { color: COLOR.read, text: `读取 ${fmtDiskRate(d.read)}` },
        { color: COLOR.write, text: `写入 ${fmtDiskRate(d.write)}` },
      ],
      cols: [
        [
          { l: '活动时间', v: `${Math.round(d.active)}%` },
          { l: '读取速度', v: fmtDiskRate(d.read) },
          { l: '写入速度', v: fmtDiskRate(d.write) },
          { l: '分区使用率', v: `${Math.round(d.percent || 0)}%` },
        ],
        [
          { l: '类型', v: [meta.type, meta.bus ? `(${meta.bus})` : ''].filter(Boolean).join(' ') || '—' },
          { l: '容量', v: fmtBytes(totalBytes) },
          { l: '已使用', v: fmtBytes(d.used || 0) },
          { l: '文件系统', v: d.filesystem || '—' },
        ],
      ],
    };
  }

  if (sel === 'net') {
    const wifi = cur.net.wifi || {};
    const iface = cur.net.iface || {};
    return {
      sig: 'net',
      title: 'Wi-Fi',
      subtitle: iface.name || 'WLAN',
      chartLabel: '吞吐量',
      chartMax: '',
      series: [
        { data: state.history.net_rx || [], color: COLOR.rx },
        { data: state.history.net_tx || [], color: COLOR.tx },
      ],
      max: null,
      legend: [
        { color: COLOR.rx, text: `接收 ${fmtRate(cur.net.rx)}` },
        { color: COLOR.tx, text: `发送 ${fmtRate(cur.net.tx)}` },
      ],
      cols: [
        [
          { l: '适配器', v: iface.name || '—' },
          { l: 'SSID', v: wifi.ssid || '—' },
          { l: '连接类型', v: wifi.radio || '—' },
          { l: '状态', v: wifi.state || '—' },
        ],
        [
          { l: '信号强度', v: wifi.signal || '—' },
          { l: '接收速率', v: wifi.rx_rate ? `${wifi.rx_rate} Mbps` : '—' },
          { l: '传输速率', v: wifi.tx_rate ? `${wifi.tx_rate} Mbps` : '—' },
          { l: 'IPv4 地址', v: iface.ip || '—' },
        ],
      ],
    };
  }

  if (sel === 'gpu') {
    const g = cur.gpu;
    return {
      sig: 'gpu',
      title: 'GPU 0',
      subtitle: st.gpu_name || '',
      chartLabel: '% 利用率',
      chartMax: '100%',
      series: [{ data: state.history.gpu || [], color: COLOR.gpu, fill: true }],
      max: 100,
      cols: [
        [
          { l: '利用率', v: `${g.util}%` },
          { l: '温度', v: `${g.temp} °C` },
          { l: '专用 GPU 内存', v: fmtBytes(g.used) },
        ],
        [
          { l: '总显存', v: fmtBytes(g.total) },
          { l: '驱动版本', v: st.gpu_driver || '—' },
          { l: 'GPU 类型', v: '独立' },
        ],
      ],
    };
  }

  return null;
}

function renderDetail(force) {
  const spec = buildDetail();
  if (!spec) return;

  if (force || spec.sig !== state.detailSig) {
    state.detailSig = spec.sig;
    state.statNodes = [];

    const legend = (spec.legend || [])
      .map((x) => `<span class="lg"><i style="background:${x.color}"></i>${esc(x.text)}</span>`)
      .join('');

    const cols = spec.cols
      .map((col) => {
        const items = col
          .map((s, i) => {
            const cls = s.dense ? ' stat-dense' : '';
            return `<div class="stat${cls}"><div class="stat-label">${esc(s.l)}</div>` +
              `<div class="stat-value" data-idx="${state.statNodes.length + i}">${esc(s.v)}</div></div>`;
          })
          .join('');
        state.statNodes.push(...col.map((s) => s.v));
        return `<div class="stats-col">${items}</div>`;
      })
      .join('');

    el.detail.innerHTML =
      `<div class="detail-head">` +
      `<h1 class="detail-title">${esc(spec.title)}</h1>` +
      `<div class="detail-subtitle">${esc(spec.subtitle)}</div>` +
      `</div>` +
      `<div class="chart">` +
      `<div class="chart-top"><span>${esc(spec.chartLabel)}${legend ? '  ' + legend : ''}</span>` +
      `<span>${esc(spec.chartMax)}</span></div>` +
      `<div class="chart-plot"><canvas></canvas><div class="chart-tip"></div></div>` +
      `<div class="chart-bottom"><span>60 秒</span><span>0</span></div>` +
      `</div>` +
      `<div class="stats">${cols}</div>`;

    state.chartCanvas = el.detail.querySelector('.chart-plot canvas');
    state.chartTip = el.detail.querySelector('.chart-tip');
    bindChartHover();
    el.detail.scrollTop = 0;
  } else {
    // 只更新数值，避免重建 canvas 造成闪烁
    const nodes = el.detail.querySelectorAll('.stat-value');
    let idx = 0;
    for (const col of spec.cols) {
      for (const s of col) {
        const node = nodes[idx++];
        if (node && node.textContent !== String(s.v)) node.textContent = s.v;
      }
    }
    const sub = el.detail.querySelector('.detail-subtitle');
    if (sub && sub.textContent !== spec.subtitle) sub.textContent = spec.subtitle;
    if (spec.legend) {
      const top = el.detail.querySelector('.chart-top span');
      if (top) {
        top.innerHTML = esc(spec.chartLabel) + '  ' + spec.legend
          .map((x) => `<span class="lg"><i style="background:${x.color}"></i>${esc(x.text)}</span>`)
          .join('');
      }
    }
  }
}

/* 图表悬停提示 */
function bindChartHover() {
  const plot = el.detail.querySelector('.chart-plot');
  if (!plot) return;
  plot.addEventListener('mousemove', (ev) => {
    const spec = buildDetail();
    if (!spec || !state.chartCanvas) return;
    const rect = state.chartCanvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const prog = state.progress || 0;
    const step = rect.width / (POINTS - 1);
    const series = spec.series[0];
    const n = series.data.length;
    if (n < 2) return;
    // 反解：x = w - (n-1-i)*step - prog*step
    const iFloat = (n - 1) - (rect.width - x - prog * step) / step;
    const i = Math.round(clamp(iFloat, 0, n - 1));
    const max = spec.max || niceMax(Math.max(...spec.series.flatMap((s) => s.data)) * 1.15);
    const lines = spec.series.map((s) => {
      const v = s.data[i] ?? 0;
      return `${s.label || ''}${formatChartValue(v, spec)}`;
    });
    const tip = state.chartTip;
    tip.textContent = lines.filter(Boolean).join(' / ');
    tip.classList.add('show');
    const tx = clamp(x - tip.offsetWidth / 2, 4, rect.width - tip.offsetWidth - 4);
    tip.style.left = tx + 'px';
    tip.style.top = '10px';
  });
  plot.addEventListener('mouseleave', () => {
    if (state.chartTip) state.chartTip.classList.remove('show');
  });
}

function formatChartValue(v, spec) {
  if (spec.sig === 'cpu' || spec.sig === 'gpu') return `${v.toFixed(0)}%`;
  if (spec.sig === 'mem') return `${fmtGB(v / 100 * (state.current.mem?.total || 0))} GB`;
  if (spec.sig === 'net') return fmtRate(v);
  if (spec.sig.startsWith('disk')) return fmtDiskRate(v);
  return String(v);
}

/* ------------------------------------------------------------------ 选择 */
function select(id) {
  if (state.selected === id) return;
  state.selected = id;
  state.detailSig = '';
  renderTiles();
  renderDetail(true);
}

/* ------------------------------------------------------------------ 主循环 */
let lastFrame = performance.now();

function frame(now) {
  const elapsed = now - state.lastFetch;
  state.progress = clamp(elapsed / STEP_MS, 0, 1);

  for (const node of state.tileNodes.values()) {
    const d = node.tileData;
    if (!d) continue;
    drawPlot(node.canvas, d.series, { max: d.max, grid: false });
  }

  const spec = buildDetail();
  if (spec && state.chartCanvas) {
    drawPlot(state.chartCanvas, spec.series, { max: spec.max, grid: true });
  }

  lastFrame = now;
  requestAnimationFrame(frame);
}

/* ------------------------------------------------------------------ 轮询 */
async function poll() {
  try {
    const res = await fetch('/api/stats', { cache: 'no-store' });
    const data = await res.json();
    state.static = data.static || {};
    state.current = data.current || {};
    state.history = data.history || {};
    state.diskHistory = data.disk_history || {};
    state.lastFetch = performance.now();
    state.progress = 0;
    if (state.page === 'performance') {
      renderTiles();
      renderDetail(false);
      if (!state.ready) {
        state.ready = true;
        renderDetail(true);
      }
    }
  } catch (_) {
    /* 后端未就绪时静默重试 */
  }
}

/* ------------------------------------------------------------------ 交互 */
function toast(msg) {
  el.toast.textContent = msg;
  el.toast.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.toast.classList.remove('show'), 1800);
}

function bindWindowControls() {
  const api = window.pywebview?.api;
  const pinBtn = document.getElementById('btn-pin');
  let pinned = false;

  pinBtn.addEventListener('click', () => {
    pinned = !pinned;
    pinBtn.classList.toggle('on', pinned);
    api?.set_on_top?.(pinned);
  });
  document.getElementById('btn-min').addEventListener('click', () => api?.minimize?.());
  document.getElementById('btn-max').addEventListener('click', () => api?.toggle_max?.());
  document.getElementById('btn-close').addEventListener('click', () => api?.close?.());
}

function bindRail() {
  document.querySelectorAll('.rail-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.rail-item').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const page = btn.dataset.page;
      const label = btn.querySelector('span').textContent;
      el.title.textContent = page === 'performance' ? '性能' : label;

      if (page === 'performance') {
        state.page = 'performance';
        el.tiles.style.display = '';
        state.tileSig = '';
        state.detailSig = '';
        renderTiles();
        renderDetail(true);
      } else {
        state.page = page;
        showPlaceholder(label);
      }
    });
  });
}

function showPlaceholder(name) {
  state.detailSig = '';
  el.tiles.style.display = 'none';
  el.detail.innerHTML =
    `<div class="placeholder"><strong>${esc(name)}</strong>` +
    `<span>本工具聚焦「性能」页，其他页签暂未实现。</span></div>`;
  state.tileSig = '';
}

function bindNewTask() {
  document.getElementById('btn-newtask').addEventListener('click', () => {
    const api = window.pywebview?.api;
    if (!api?.run) {
      toast('仅桌面版可用');
      return;
    }
    const dlg = document.createElement('div');
    dlg.className = 'modal-mask';
    dlg.innerHTML =
      `<div class="modal">` +
      `<div class="modal-title">新建任务</div>` +
      `<div class="modal-desc">输入要运行的程序或命令行。</div>` +
      `<input class="modal-input" id="mt-input" value="" placeholder="例如：notepad.exe 或 cmd /k ipconfig">` +
      `<div class="modal-actions">` +
      `<button class="ghost-btn" id="mt-cancel">取消</button>` +
      `<button class="ghost-btn primary" id="mt-ok">确定</button>` +
      `</div></div>`;
    document.body.appendChild(dlg);
    const input = dlg.querySelector('#mt-input');
    input.focus();

    const close = () => dlg.remove();
    const run = async () => {
      const cmd = input.value.trim();
      if (!cmd) return;
      const ok = await api.run(cmd);
      close();
      toast(ok ? `已启动：${cmd}` : '启动失败');
    };

    dlg.querySelector('#mt-cancel').addEventListener('click', close);
    dlg.querySelector('#mt-ok').addEventListener('click', run);
    dlg.addEventListener('click', (e) => { if (e.target === dlg) close(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') run();
      if (e.key === 'Escape') close();
    });
  });
}

/* ------------------------------------------------------------------ 启动 */
function init() {
  bindRail();
  bindNewTask();
}

window.addEventListener('pywebviewready', bindWindowControls);
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

poll();
setInterval(poll, STEP_MS);
requestAnimationFrame(frame);

/* 轻舟 / Canoe 管理面板 —— 无构建步骤的原生 JS
 *
 * 几条自我约束：
 *   1. **不用 innerHTML 渲染任何数据**。用户名 / 备注 / 节点名都是管理员填的，
 *      但它们同样可能带 < > &，一律走 textContent，免得把面板自己注了。
 *   2. **不发任何跨域请求**。所有资源同源，管理后台不去 ping 第三方。
 *   3. 令牌放 sessionStorage（关掉标签页就没了），不放 cookies —— 免掉 CSRF 面。
 */
'use strict';

/* =========================================================================
 * 状态与请求
 * ========================================================================= */

const TOKEN_KEY = 'canoe.panel.token';

const state = {
  token: sessionStorage.getItem(TOKEN_KEY) || '',
  me: null,
  page: 'overview',
};

async function api(path, opts = {}) {
  const { method = 'GET', body, form } = opts;
  const headers = {};
  if (state.token) headers['Authorization'] = 'Bearer ' + state.token;

  let payload;
  if (form) {
    payload = form;                       // FormData：别设 Content-Type，浏览器自己带 boundary
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(path, { method, headers, body: payload });
  } catch (err) {
    throw new ApiError('连不上服务端', 0);
  }

  if (res.status === 401) {
    // 令牌失效：退回登录页
    setToken('');
    showLogin('登录已失效，请重新登录');
    throw new ApiError('登录已失效', 401);
  }

  let data = null;
  const text = await res.text();
  if (text) { try { data = JSON.parse(text); } catch (_) { data = text; } }

  if (!res.ok) {
    let msg = 'HTTP ' + res.status;
    const detail = data && data.detail;
    if (typeof detail === 'string') msg = detail;
    else if (detail && typeof detail === 'object') msg = detail.detail || detail.code || msg;
    else if (Array.isArray(detail) && detail.length) msg = detail[0].msg || msg;
    throw new ApiError(msg, res.status);
  }
  return data;
}

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function setToken(t) {
  state.token = t || '';
  if (t) sessionStorage.setItem(TOKEN_KEY, t);
  else sessionStorage.removeItem(TOKEN_KEY);
}

/* =========================================================================
 * DOM 小工具
 * ========================================================================= */

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

const $ = (sel) => document.querySelector(sel);

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }

function fmtTime(epoch) {
  if (!epoch) return '—';
  const d = new Date(epoch * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtSize(n) {
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(i ? 1 : 0) + ' ' + u[i];
}

function fmtExpire(epoch) {
  if (!epoch) return h('span', { class: 'tag', text: '永不过期' });
  const left = epoch * 1000 - Date.now();
  if (left < 0) return h('span', { class: 'tag bad', text: '已过期 ' + fmtTime(epoch) });
  const days = Math.floor(left / 86400000);
  const cls = days <= 3 ? 'tag warn' : 'tag';
  return h('span', { class: cls, text: fmtTime(epoch) + `（剩 ${days} 天）` });
}

function tag(text, kind) { return h('span', { class: 'tag' + (kind ? ' ' + kind : ''), text }); }

/* =========================================================================
 * 提示 / 弹窗
 * ========================================================================= */

function toast(message, kind = '') {
  const box = $('#toasts');
  const el = h('div', { class: 'toast ' + kind, text: message });
  box.append(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 250); }, 3600);
}

let modalSubmit = null;

function openModal({ title, fields = [], values = {}, submitText = '确定', wide = false, onSubmit }) {
  $('#modal-title').textContent = title;
  const body = clear($('#modal-body'));
  const inputs = {};

  for (const f of fields) {
    if (f.type === 'group') {
      body.append(h('div', { class: 'field-group', text: f.label }));
      continue;
    }

    const id = 'f_' + f.key;
    let input;
    if (f.type === 'select') {
      input = h('select', { id });
      for (const o of f.options) input.append(h('option', { value: o.value, text: o.label }));
    } else if (f.type === 'textarea') {
      input = h('textarea', { id, placeholder: f.placeholder || '' });
    } else if (f.type === 'file') {
      input = h('input', { id, type: 'file', accept: f.accept || '' });
    } else {
      input = h('input', { id, type: f.type || 'text', placeholder: f.placeholder || '' });
    }

    if (f.type === 'checkbox') {
      input.checked = !!values[f.key];
      body.append(h('label', { class: 'field switch' },
        input,
        h('span', { text: f.label }),
        f.help ? h('span', { class: 'help', text: f.help }) : null));
      inputs[f.key] = input;
      continue;
    }

    if (f.type !== 'file') {
      const v = values[f.key];
      input.value = (v === null || v === undefined) ? (f.default ?? '') : v;
    }

    body.append(h('label', { class: 'field' },
      h('span', { text: f.label + (f.required ? ' *' : '') }),
      input,
      f.help ? h('span', { class: 'help', text: f.help }) : null));

    inputs[f.key] = input;
  }

  modalSubmit = async () => {
    const out = {};
    for (const f of fields) {
      if (f.type === 'group') continue;
      const el = inputs[f.key];
      if (f.type === 'checkbox') out[f.key] = el.checked;
      else if (f.type === 'file') out[f.key] = el.files[0] || null;
      else if (f.type === 'number') out[f.key] = el.value === '' ? null : Number(el.value);
      else out[f.key] = el.value;
    }
    return onSubmit(out);
  };

  $('#modal-root').hidden = false;
  const first = body.querySelector('input, select, textarea');
  if (first) first.focus();
}

function closeModal() {
  $('#modal-root').hidden = true;
  modalSubmit = null;
}

/* =========================================================================
 * 表格
 * ========================================================================= */

function renderTable(columns, rows, emptyText = '暂无数据') {
  if (!rows.length) return h('div', { class: 'empty', text: emptyText });
  const thead = h('thead', null, h('tr', null,
    columns.map((c) => h('th', { class: c.wrap ? 'wrap' : '' , text: c.title }))));
  const tbody = h('tbody', null, rows.map((row) =>
    h('tr', null, columns.map((c) => {
      const cell = h('td', { class: c.wrap ? 'wrap' : '' });
      const v = c.render ? c.render(row) : row[c.key];
      cell.append(v instanceof Node ? v : document.createTextNode(v === null || v === undefined ? '—' : String(v)));
      return cell;
    }))));
  return h('div', { class: 'table-wrap' }, h('table', null, thead, tbody));
}

function actionCell(...nodes) { return h('td', { class: 'actions' }, ...nodes); }

/* =========================================================================
 * 页面：概览
 * ========================================================================= */

function statCard(k, v, unit, cls) {
  return h('div', { class: 'stat ' + (cls || '') },
    h('div', { class: 'k', text: k }),
    h('div', { class: 'v' }, String(v), unit ? h('small', { text: unit }) : null));
}

async function pageOverview(root) {
  const s = await api('/api/admin/stats');
  const push = s.push || {};
  root.append(h('div', { class: 'stat-grid' },
    statCard('用户总数', s.users_total, '', 'info'),
    statCard('正常', s.users_active, '', 'good'),
    statCard('已封禁', s.users_banned, '', s.users_banned ? 'warn' : ''),
    statCard('节点总数', s.nodes_total),
    statCard('已启用节点', s.nodes_enabled, '', 'good'),
    statCard('在线会话', s.sessions_online, '', s.sessions_online ? 'good' : ''),
    statCard('配置版本', s.config_version, '', 'info'),
    statCard('推送连接', push.connections || 0, `（${push.users || 0} 个账号）`),
  ));

  const warn = [];
  if (!s.nodes_enabled) warn.push('还没有启用任何节点 —— 客户端启航会收到 503 no_node');
  if (!s.users_active) warn.push('还没有正常状态的用户');

  root.append(h('div', { class: 'card' },
    h('h3', { text: '提示' }),
    warn.length
      ? h('ul', null, warn.map((t) => h('li', { text: t })))
      : h('div', { class: 'muted', text: '一切正常。' }),
    h('div', { class: 'card-actions', style: 'margin-top:12px' },
      h('button', { class: 'btn btn-ghost btn-sm', text: '看接口文档 (/docs)', onclick: () => window.open('/docs', '_blank') }),
      h('button', { class: 'btn btn-ghost btn-sm', text: '下载客户端安装包目录', onclick: () => window.open('/downloads/', '_blank') })),
  ));
}

/* =========================================================================
 * 页面：用户
 * ========================================================================= */

const userFields = [
  { key: 'username', label: '用户名', required: true, placeholder: '3-32 位字母数字下划线' },
  { key: 'password', label: '密码', type: 'password', required: true, help: '至少 8 位' },
  { key: 'expire_days', label: '有效天数', type: 'number', default: 30, help: '留空或 0 表示永不过期' },
  { key: 'max_devices', label: '设备数上限', type: 'number', default: 3 },
  { key: 'remark', label: '备注' },
];

async function pageUsers(root) {
  const data = await api('/api/admin/users?size=200');
  const rows = data.items || [];

  root.append(h('div', { class: 'card-actions' },
    h('button', {
      class: 'btn btn-primary btn-sm', text: '新建用户',
      onclick: () => openModal({
        title: '新建用户', fields: userFields, values: { expire_days: 30, max_devices: 3 },
        onSubmit: async (v) => {
          await api('/api/admin/users', { method: 'POST', body: {
            username: v.username, password: v.password,
            expire_days: v.expire_days === '' || v.expire_days === null ? null : Number(v.expire_days),
            max_devices: v.max_devices ? Number(v.max_devices) : null,
            remark: v.remark || '',
          }});
          toast('用户已创建', 'ok'); closeModal(); render();
        },
      }),
    }),
  ));

  root.append(h('div', { class: 'card' }, renderTable([
    { title: 'ID', key: 'id' },
    { title: '用户名', key: 'username' },
    { title: '角色', render: (r) => r.role === 'admin' ? tag('管理员', 'admin') : tag('用户') },
    { title: '状态', render: (r) => r.status === 'active' ? tag('正常', 'ok') : tag('已封禁', 'bad') },
    { title: '在线', render: (r) => r.online ? tag('在线', 'ok') : tag('离线') },
    { title: '到期', render: (r) => fmtExpire(r.expire_at) },
    { title: '设备上限', key: 'max_devices' },
    { title: '备注', key: 'remark', wrap: true },
    { title: '最近登录', render: (r) => fmtTime(r.last_login_at) },
    {
      title: '操作', render: (r) => actionCell(
        h('button', { class: 'btn btn-ghost btn-sm', text: '编辑', onclick: () => editUser(r) }),
        h('button', { class: 'btn btn-ghost btn-sm', text: '绑定节点', onclick: () => bindNodes(r) }),
        r.status === 'active'
          ? h('button', { class: 'btn btn-danger btn-sm', text: '封禁', onclick: () => simple('/api/admin/users/' + r.id + '/ban', 'POST', '已封禁，令牌与会话立即失效') })
          : h('button', { class: 'btn btn-ghost btn-sm', text: '解封', onclick: () => simple('/api/admin/users/' + r.id + '/unban', 'POST', '已解封') }),
        h('button', { class: 'btn btn-danger btn-sm', text: '删除', onclick: () => confirmDo(`删除用户 ${r.username}？`, () => simple('/api/admin/users/' + r.id, 'DELETE', '已删除')) }),
      ),
    },
  ], rows, `还没有用户，点上面的「新建用户」`)));
}

function editUser(r) {
  const expire = r.expire_at ? new Date(r.expire_at * 1000).toISOString().slice(0, 10) : '';
  openModal({
    title: '编辑用户 · ' + r.username,
    fields: [
      { key: 'password', label: '改密码', type: 'password', help: '留空表示不改' },
      { key: 'expire_date', label: '到期日期', type: 'date', help: '留空 = 永不过期' },
      { key: 'max_devices', label: '设备数上限', type: 'number' },
      { key: 'remark', label: '备注' },
      { key: 'role', label: '角色', type: 'select', options: [
        { value: 'user', label: '普通用户' }, { value: 'admin', label: '管理员' }] },
    ],
    values: { max_devices: r.max_devices, remark: r.remark, role: r.role, expire_date: expire },
    onSubmit: async (v) => {
      const body = { max_devices: Number(v.max_devices) || 0, remark: v.remark || '', role: v.role };
      if (v.password) body.password = v.password;
      body.expire_at = v.expire_date ? Math.floor(new Date(v.expire_date + 'T23:59:59').getTime() / 1000) : 0;
      await api('/api/admin/users/' + r.id, { method: 'PATCH', body });
      toast('已保存', 'ok'); closeModal(); render();
    },
  });
}

async function bindNodes(r) {
  const nodes = (await api('/api/admin/nodes')).items || [];
  if (!nodes.length) { toast('还没有节点', 'warn'); return; }
  openModal({
    title: `把节点绑定给 ${r.username}`,
    fields: [
      { type: 'group', label: '勾选要绑定的节点（不勾则自动分配第一个可用节点）' },
      ...nodes.map((n) => ({ key: 'node_' + n.id, label: `${n.name}（#${n.id}）${n.enabled ? '' : ' · 已停用'}`, type: 'checkbox' })),
    ],
    values: {},
    submitText: '绑定',
    onSubmit: async (v) => {
      const ids = Object.entries(v).filter(([k, on]) => k.startsWith('node_') && on).map(([k]) => Number(k.slice(5)));
      if (!ids.length) { toast('一个都没勾', 'warn'); return; }
      let added = 0;
      for (const id of ids) {
        const res = await api(`/api/admin/nodes/${id}/bind`, { method: 'POST', body: { node_ids: [r.id] } });
        added += res.added || 0;
      }
      toast(`绑定完成（新增 ${added} 条）`, 'ok'); closeModal();
    },
  });
}

/* =========================================================================
 * 页面：节点
 * ========================================================================= */

const nodeFields = () => [
  { type: 'group', label: '基本' },
  { key: 'name', label: '节点名（客户端只看到这个）', required: true, placeholder: '香港-01' },
  { key: 'sort_order', label: '排序', type: 'number', default: 100, help: '数字越小越优先' },
  { key: 'remark', label: '备注' },
  { key: 'enabled', label: '启用', type: 'checkbox', default: true },

  { type: 'group', label: '中转入口（客户端会拿到这些）' },
  { key: 'entry_host', label: '入口域名', help: '客户端连的就是它，不是真实节点' },
  { key: 'entry_port', label: '入口端口', type: 'number', default: 443 },
  { key: 'entry_path', label: '入口路径', placeholder: '/e/hk01', help: '留空自动生成 /e/n<id>' },
  { key: 'entry_uuid', label: '入口 UUID', help: '留空自动生成' },
  { key: 'entry_sni', label: '入口 SNI', help: '留空跟随入口域名' },
  { key: 'entry_transport', label: '传输', type: 'select', options: [
    { value: 'ws', label: 'WebSocket' }, { value: 'grpc', label: 'gRPC' }, { value: 'tcp', label: 'TCP' }] },
  { key: 'entry_tls', label: '入口启用 TLS', type: 'checkbox', default: true },

  { type: 'group', label: '⚠ 真实节点（绝不下发给客户端，只用来生成中转层配置）' },
  { key: 'real_protocol', label: '协议', type: 'select', options: [
    { value: 'vless', label: 'VLESS' }, { value: 'vmess', label: 'VMess' }, { value: 'trojan', label: 'Trojan' },
    { value: 'shadowsocks', label: 'Shadowsocks' }, { value: 'hysteria2', label: 'Hysteria2' },
    { value: 'tuic', label: 'TUIC' }] },
  { key: 'real_host', label: '真实地址' },
  { key: 'real_port', label: '真实端口', type: 'number', default: 443 },
  { key: 'real_uuid', label: '真实 UUID / 密码' },
  { key: 'real_sni', label: '真实 SNI' },
  { key: 'real_flow', label: 'Flow', placeholder: 'xtls-rprx-vision' },
  { key: 'real_network', label: '传输网络', placeholder: 'tcp' },
  { key: 'real_ws_path', label: 'WS 路径' },
  { key: 'real_fingerprint', label: '指纹', default: 'chrome' },
  { key: 'real_tls', label: '真实节点启用 TLS', type: 'checkbox', default: true },
];

function nodeBody(v) {
  const num = (x, d) => (x === '' || x === null || x === undefined || isNaN(Number(x))) ? d : Number(x);
  return {
    name: v.name, remark: v.remark || '', enabled: !!v.enabled, sort_order: num(v.sort_order, 100),
    entry_host: v.entry_host || '', entry_port: num(v.entry_port, 443),
    entry_uuid: v.entry_uuid || '', entry_path: v.entry_path || '',
    entry_sni: v.entry_sni || '', entry_transport: v.entry_transport || 'ws',
    entry_tls: !!v.entry_tls, entry_insecure: false,
    real_protocol: v.real_protocol || 'vless', real_host: v.real_host || '',
    real_port: num(v.real_port, 443), real_uuid: v.real_uuid || '',
    real_flow: v.real_flow || '', real_tls: !!v.real_tls, real_sni: v.real_sni || '',
    real_fingerprint: v.real_fingerprint || 'chrome', real_network: v.real_network || 'tcp',
    real_ws_path: v.real_ws_path || '', real_ws_host: '', real_grpc_service: '',
    real_insecure: false, real_extra: {},
  };
}

async function pageNodes(root) {
  const data = await api('/api/admin/nodes');
  const rows = data.items || [];

  root.append(h('div', { class: 'card-actions' },
    h('button', {
      class: 'btn btn-primary btn-sm', text: '新建节点',
      onclick: () => openModal({
        title: '新建节点', fields: nodeFields(), values: { enabled: true, entry_tls: true, real_tls: true, sort_order: 100, entry_port: 443, real_port: 443, real_fingerprint: 'chrome', real_network: 'tcp', entry_transport: 'ws', real_protocol: 'vless' },
        submitted: '创建', onSubmit: async (v) => {
          const res = await api('/api/admin/nodes', { method: 'POST', body: nodeBody(v) });
          toast(`节点已创建（配置版本 → ${res.config_version}，已推送 ${res.pushed} 条连接）`, 'ok');
          closeModal(); render();
        },
      }),
    }),
    h('button', { class: 'btn btn-ghost btn-sm', text: '重新加载中转层', onclick: relayReload }),
  ));

  root.append(h('div', { class: 'card' }, renderTable([
    { title: 'ID', key: 'id' },
    { title: '节点名', key: 'name' },
    { title: '状态', render: (r) => r.enabled ? tag('启用', 'ok') : tag('停用') },
    { title: '排序', key: 'sort_order' },
    { title: '入口（客户端可见）', render: (r) => h('span', { class: 'mono', text: `${r.entry.host || '?'}:${r.entry.port}${r.entry.path || ''}` }) },
    { title: '真实节点（仅服务端）', render: (r) => h('span', { class: 'secret', text: `${r.real.protocol}://${r.real.host || '?'}:${r.real.port}` }) },
    {
      title: '操作', render: (r) => actionCell(
        h('button', { class: 'btn btn-ghost btn-sm', text: '编辑', onclick: () => openModal({
          title: '编辑节点 · ' + r.name, fields: nodeFields(),
          values: { ...r.entry, ...r.real, name: r.name, remark: r.remark, enabled: r.enabled, sort_order: r.sort_order },
          onSubmit: async (v) => {
            const res = await api('/api/admin/nodes/' + r.id, { method: 'PATCH', body: nodeBody(v) });
            toast(`已保存（配置版本 → ${res.config_version}）`, 'ok');
            closeModal(); render();
          },
        }) }),
        h('button', { class: 'btn btn-danger btn-sm', text: '删除', onclick: () => confirmDo(`删除节点 ${r.name}？`, () => simple('/api/admin/nodes/' + r.id, 'DELETE', '已删除')) }),
      ),
    },
  ], rows, '还没有节点 —— 客户端启航会收到 503，先建一个')));
}

/* =========================================================================
 * 页面：会话
 * ========================================================================= */

async function pageSessions(root) {
  const data = await api('/api/admin/sessions?online=true');
  const rows = data.items || [];

  root.append(h('div', { class: 'card-actions' },
    h('button', {
      class: 'btn btn-ghost btn-sm', text: '显示全部（含历史）',
      onclick: async () => {
        const all = await api('/api/admin/sessions?online=false&limit=200');
        clear(root).append(h('div', { class: 'card' }, renderTable(sessionCols(), all.items || [], '没有会话')));
      },
    }),
  ));
  root.append(h('div', { class: 'card' }, renderTable(sessionCols(), rows, '当前没有在线会话')));
}

function sessionCols() {
  return [
    { title: '会话 ID', render: (r) => h('span', { class: 'mono', text: r.session_id.slice(0, 12) + '…' }) },
    { title: '用户', key: 'username' },
    { title: '节点', key: 'node_name' },
    { title: '设备', key: 'device_id' },
    { title: '来源 IP', key: 'client_ip' },
    { title: '模式', key: 'mode' },
    { title: '状态', render: (r) => r.revoked ? tag('已吊销', 'bad') : (r.online ? tag('在线', 'ok') : tag('离线')) },
    { title: '最后心跳', render: (r) => fmtTime(r.last_seen) },
    {
      title: '操作', render: (r) => actionCell(
        h('button', { class: 'btn btn-danger btn-sm', text: '踢下线', onclick: () => simple('/api/admin/sessions/' + r.session_id, 'DELETE', '已踢下线（会同时推送 kick）') }),
      ),
    },
  ];
}

/* =========================================================================
 * 页面：发布
 * ========================================================================= */

async function pageReleases(root) {
  const data = await api('/api/admin/releases');
  const rows = data.items || [];

  root.append(h('div', { class: 'card-actions' },
    h('button', {
      class: 'btn btn-primary btn-sm', text: '上传安装包并发布',
      onclick: () => openModal({
        title: '发布客户端新版本',
        fields: [
          { key: 'version', label: '版本号', required: true, placeholder: '1.1.0' },
          { key: 'min_version', label: '最低要求版本', help: '低于它的客户端会被提示强制升级；留空不强制' },
          { key: 'notes', label: '更新说明', type: 'textarea', placeholder: '修复 TUN 快速重连卡顿' },
          { key: 'file', label: '安装包（zip）', type: 'file', accept: '.zip', required: true },
        ],
        submitText: '上传',
        onSubmit: async (v) => {
          if (!v.file) { toast('还没选文件', 'warn'); return; }
          const fd = new FormData();
          fd.append('version', v.version);
          fd.append('notes', v.notes || '');
          fd.append('min_version', v.min_version || '');
          fd.append('file', v.file);
          await api('/api/admin/releases/upload', { method: 'POST', form: fd });
          toast('已发布，在线客户端会收到推送', 'ok'); closeModal(); render();
        },
      }),
    }),
    h('button', {
      class: 'btn btn-ghost btn-sm', text: '预览客户端会拿到什么',
      onclick: async () => {
        const p = await api('/api/admin/releases/latest-preview');
        openModal({ title: '客户端看到的最新版本', fields: [], submitText: '关闭',
          onSubmit: () => closeModal() });
        clear($('#modal-body')).append(h('pre', { class: 'code', text: JSON.stringify(p, null, 2) }));
      },
    }),
  ));

  root.append(h('div', { class: 'card' }, renderTable([
    { title: '版本', render: (r) => h('b', { text: r.version }) },
    { title: '安装包', key: 'filename' },
    { title: '大小', render: (r) => fmtSize(r.size) },
    { title: 'SHA256', render: (r) => h('span', { class: 'mono muted', text: r.sha256 ? r.sha256.slice(0, 12) + '…' : '—' }) },
    { title: '最低版本', key: 'min_version' },
    { title: '发布时间', render: (r) => fmtTime(r.published_at) },
    { title: '状态', render: (r) => r.enabled ? tag('已发布', 'ok') : tag('已撤下') },
    {
      title: '操作', render: (r) => actionCell(
        h('button', { class: 'btn btn-ghost btn-sm', text: '下载', onclick: () => window.open('/downloads/' + r.filename, '_blank') }),
        h('button', { class: 'btn btn-danger btn-sm', text: '撤下', onclick: () => confirmDo(`撤下版本 ${r.version}？（连安装包一起删）`,
          () => simple(`/api/admin/releases/${r.id}?delete_file=true`, 'DELETE', '已撤下')) }),
      ),
    },
  ], rows, '还没有发布过任何版本 —— 客户端点「更新」会收到 404')));
}

/* =========================================================================
 * 页面：中转层
 * ========================================================================= */

async function relayReload() {
  const res = await api('/api/admin/relay/reload', { method: 'POST' });
  if (res.reloaded) toast(res.ok ? '已重新加载' : 'reload 失败：' + (res.stderr || '').slice(0, 120), res.ok ? 'ok' : 'err');
  else toast(res.hint || '未配置 reload hook', 'warn');
}

async function pageRelay(root) {
  root.append(h('div', { class: 'card' },
    h('h3', { text: '中转层配置' }),
    h('div', { class: 'muted', text: '真实节点只出现在生成的配置里；客户端永远拿不到。改完节点记得重新加载。' }),
    h('div', { class: 'card-actions', style: 'margin-top:12px' },
      h('button', { class: 'btn btn-ghost btn-sm', text: '渲染 sing-box 配置', onclick: () => showConfig('singbox') }),
      h('button', { class: 'btn btn-ghost btn-sm', text: '渲染 Nginx 配置', onclick: () => showConfig('nginx') }),
      h('button', { class: 'btn btn-primary btn-sm', text: '重新加载中转层', onclick: relayReload }),
    ),
  ));
  const box = h('div', { class: 'card' }, h('h3', { text: '输出' }),
    h('div', { class: 'muted', text: '点上面的按钮生成配置' }));
  root.append(box);
}

async function showConfig(fmt) {
  const text = await api('/api/admin/relay/config?fmt=' + fmt, { raw: true, text: true });
  const cards = document.querySelectorAll('.page .card');
  const out = cards[cards.length - 1];
  clear(out).append(h('h3', { text: fmt === 'nginx' ? 'Nginx 配置' : 'sing-box 配置' }),
    h('div', { class: 'card-actions' },
      h('button', { class: 'btn btn-ghost btn-sm', text: '复制', onclick: () => {
        navigator.clipboard.writeText(typeof text === 'string' ? text : JSON.stringify(text, null, 2));
        toast('已复制', 'ok');
      }})),
    h('pre', { class: 'code', text: typeof text === 'string' ? text : JSON.stringify(text, null, 2) }));
}

/* =========================================================================
 * 通用动作
 * ========================================================================= */

async function simple(path, method, okMsg) {
  try {
    await api(path, { method });
    toast(okMsg, 'ok');
    render();
  } catch (err) { toast(err.message, 'err'); }
}

function confirmDo(question, fn) {
  openModal({
    title: '确认', fields: [
      { type: 'group', label: question },
    ], submitText: '确定', onSubmit: async () => { closeModal(); await fn(); },
  });
}

/* =========================================================================
 * 导航与渲染
 * ========================================================================= */

const PAGES = [
  { key: 'overview', label: '概览', ico: '◈', title: '概览', render: pageOverview },
  { key: 'users',    label: '用户', ico: '☺', title: '用户管理', render: pageUsers },
  { key: 'nodes',    label: '节点', ico: '⛵', title: '节点管理', render: pageNodes },
  { key: 'sessions', label: '会话', ico: '⇄', title: '在线会话', render: pageSessions },
  { key: 'releases', label: '发布', ico: '⇪', title: '客户端版本发布', render: pageReleases },
  { key: 'relay',    label: '中转层', ico: '⚙', title: '中转层配置', render: pageRelay },
];

function buildNav() {
  const nav = clear($('#nav'));
  for (const p of PAGES) {
    nav.append(h('div', {
      class: 'nav-item' + (state.page === p.key ? ' active' : ''),
      onclick: () => { state.page = p.key; buildNav(); render(); },
    }, h('span', { class: 'ico', text: p.ico }), h('span', { text: p.label })));
  }
}

async function render() {
  const meta = PAGES.find((p) => p.key === state.page) || PAGES[0];
  $('#page-title').textContent = meta.title;
  const root = clear($('#page'));
  const loading = h('div', { class: 'empty', text: '加载中…' });
  root.append(loading);
  try {
    await meta.render(root);
  } catch (err) {
    if (err.status === 401) return;
    clear(root).append(h('div', { class: 'card' },
      h('h3', { text: '加载失败' }),
      h('div', { class: 'muted', text: err.message })));
  }
}

/* =========================================================================
 * 登录流程
 * ========================================================================= */

function showLogin(message) {
  $('#app-screen').hidden = true;
  $('#login-screen').hidden = false;
  const err = $('#login-error');
  if (message) { err.hidden = false; err.textContent = message; }
  else { err.hidden = true; err.textContent = ''; }
  $('#login-pass').value = '';
}

async function showApp() {
  $('#login-screen').hidden = true;
  $('#app-screen').hidden = false;
  $('#whoami').append(h('b', { text: state.me.username }),
    h('br'), document.createTextNode(state.me.role === 'admin' ? '管理员' : state.me.role));
  buildNav();
  await render();
  pollHealth();
}

async function pollHealth() {
  const dot = $('#health-dot');
  try { await api('/api/health'); dot.className = 'dot ok'; dot.title = '服务端正常'; }
  catch (_) { dot.className = 'dot bad'; dot.title = '连不上'; }
  setTimeout(pollHealth, 15000);
}

async function tryResume() {
  if (!state.token) { showLogin(); return; }
  try {
    const me = await api('/api/me');
    if (me.role !== 'admin') { setToken(''); showLogin('这个账号不是管理员，进不了面板'); return; }
    state.me = me;
    await showApp();
  } catch (_) { showLogin(); }
}

/* =========================================================================
 * 启动
 * ========================================================================= */

function init() {
  $('#login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('#login-btn');
    btn.disabled = true; btn.textContent = '登录中…';
    try {
      const res = await api('/api/login', {
        method: 'POST',
        body: {
          username: $('#login-user').value.trim(),
          password: $('#login-pass').value,
          device_id: 'web-panel-' + (localStorage.getItem('canoe.panel.device') || (() => {
            const d = Math.random().toString(36).slice(2, 14);
            localStorage.setItem('canoe.panel.device', d);
            return d;
          })()),
          device_name: 'Web 管理面板',
        },
      });
      setToken(res.token);
      state.me = res.user;
      if (res.user.role !== 'admin') {
        setToken(''); showLogin('这个账号不是管理员，进不了面板'); return;
      }
      $('#whoami').textContent = '';
      await showApp();
      toast('欢迎回来，' + res.user.username, 'ok');
    } catch (err) {
      setToken('');
      showLogin(err.message);
    } finally {
      btn.disabled = false; btn.textContent = '登 录';
    }
  });

  $('#logout-btn').addEventListener('click', async () => {
    try { await api('/api/logout', { method: 'POST', body: {} }); } catch (_) {}
    setToken(''); state.me = null;
    $('#whoami').textContent = '';
    showLogin();
  });

  $('#refresh-btn').addEventListener('click', render);
  $('#modal-close').addEventListener('click', closeModal);
  $('#modal-cancel').addEventListener('click', closeModal);
  $('#modal-root').querySelector('.modal-mask').addEventListener('click', closeModal);
  $('#modal-ok').addEventListener('click', async () => {
    if (!modalSubmit) return;
    const btn = $('#modal-ok');
    btn.disabled = true;
    try { await modalSubmit(); }
    catch (err) { toast(err.message, 'err'); }
    finally { btn.disabled = false; }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('#modal-root').hidden) closeModal();
  });

  tryResume();
}

document.addEventListener('DOMContentLoaded', init);

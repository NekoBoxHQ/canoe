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
  page: 'users',
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

/* -------------------------------------------------------------------------
 * 一按就干的动作（封禁 / 解封 / 删除 / 踢下线 / 撤版本……）
 *
 * 这两个函数**必须存在**：列表里那些按钮都直接调它们。曾经在重构里
 * 把定义删掉了、调用点留着 —— 表现是点「封禁」「删除」毫无反应，
 * 连报错都没有（按钮的 onclick 里抛 ReferenceError，浏览器只写进
 * 控制台，页面上什么都没有）。DOM 测试当时只渲染表格、不点按钮，
 * 所以一路绿灯放过去了。
 * ------------------------------------------------------------------------- */

/** 调一个接口 -> 提示 -> 重画当前页。失败就把原因弹出来，不静默吞掉。 */
async function simple(path, method, okText) {
  try {
    await api(path, { method });
  } catch (err) {
    toast(err.message || '操作失败', 'err');
    return false;
  }
  toast(okText, 'ok');
  await render();
  return true;
}

/** 动手前先问一句。用在删东西这种收不回来的操作上。 */
function confirmDo(question, run) {
  if (!window.confirm(question)) return false;
  return run();
}

let modalSubmit = null;

function openModal({ title, fields = [], values = {}, submitText = '确定', wide = false, onSubmit,
                    onMount }) {
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
      input = h('textarea', {
        id,
        placeholder: f.placeholder || '',
        rows: String(f.rows || 8),
        spellcheck: 'false',
      });
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
      const val = (v === null || v === undefined) ? (f.default ?? '') : v;
      if (f.type === 'select') {
        // 用 option.selected 标，别写 select.value —— 有的 DOM 实现
        // （比如测试环境用的 linkedom）上 value 是只读的，一写就抛。
        for (const opt of input.options) {
          opt.selected = opt.value === String(val);
        }
      } else {
        input.value = val;
      }
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
  // 弹窗搭好之后的钩子：给调用方一个机会去接线（比如"勾满就不让再勾"）。
  // 传的是 inputs 那份映射，跟 onSubmit 拿到的是同一个 key。
  if (onMount) onMount(inputs);

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

/**
 * 一排统计卡。原来是「概览」页的全部内容，后来用户说那一页没必要单开，
 * 就搬到用户页顶上（新建用户按钮上面）了。包成函数是因为它要在
 * 用户页里 await 一次 stats。
 */
async function statsGrid() {
  const s = await api('/api/admin/stats');
  const push = s.push || {};
  return h('div', { class: 'stat-grid' },
    statCard('用户总数', s.users_total, '', 'info'),
    statCard('正常', s.users_active, '', 'good'),
    statCard('已封禁', s.users_banned, '', s.users_banned ? 'warn' : ''),
    statCard('节点总数', s.nodes_total),
    statCard('已启用节点', s.nodes_enabled, '', 'good'),
    statCard('在线会话', s.sessions_online, '', s.sessions_online ? 'good' : ''),
    // 以前这里显示 config_version（中转层时代的全局配置版本号），
    // 订阅模式下没有这个东西了，改成对管理员更有用的：客户端发到哪一版了
    statCard('客户端版本', s.latest_client_version || '未发布', '', 'info'),
    statCard('推送连接', push.connections || 0, `（${push.users || 0} 个账号）`),
  );
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

  root.append(await statsGrid());
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
    {
      title: '分发',
      render: (r) => (r.subscription_lines
        ? tag(`${r.subscription_lines} 个节点`, 'ok')
        : tag('未配置', 'warn')),
    },
    { title: '设备上限', key: 'max_devices' },
    { title: '备注', key: 'remark', wrap: true },
    { title: '最近登录', render: (r) => fmtTime(r.last_login_at) },
    {
      title: '操作', render: (r) => actionCell(
        h('button', { class: 'btn btn-ghost btn-sm', text: '编辑', onclick: () => editUser(r) }),
        h('button', { class: 'btn btn-ghost btn-sm', text: '分配节点', onclick: () => bindNodes(r) }),
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
      { key: 'username', label: '用户名', required: true,
        placeholder: '3-32 位字母数字下划线',
        help: '改了不影响已登录的令牌，只是下次登录要用新名字' },
      { key: 'password', label: '改密码', type: 'password', help: '留空表示不改' },
      { key: 'expire_date', label: '到期日期', type: 'date', help: '留空 = 永不过期' },
      { key: 'max_devices', label: '设备数上限', type: 'number' },
      { key: 'remark', label: '备注' },
      { key: 'role', label: '角色', type: 'select', options: [
        { value: 'user', label: '普通用户' }, { value: 'admin', label: '管理员' }] },
    ],
    values: { username: r.username, max_devices: r.max_devices, remark: r.remark,
              role: r.role, expire_date: expire },
    onSubmit: async (v) => {
      const body = { max_devices: Number(v.max_devices) || 0, remark: v.remark || '', role: v.role };
      if (v.username && v.username !== r.username) body.username = v.username;
      if (v.password) body.password = v.password;
      body.expire_at = v.expire_date ? Math.floor(new Date(v.expire_date + 'T23:59:59').getTime() / 1000) : 0;
      await api('/api/admin/users/' + r.id, { method: 'PATCH', body });
      // 把自己改了名：侧边栏那份是登录时的快照，得跟着换
      if (body.username && r.id === state.me?.id) {
        state.me.username = body.username;
        renderWhoami();
      }
      toast('已保存', 'ok'); closeModal(); render();
    },
  });
}

async function bindNodes(r) {
  const data = await api('/api/admin/nodes');
  const nodes = data.items || [];
  if (!nodes.length) { toast('还没有节点 —— 先去「节点」页加一个', 'warn'); return; }
  const bound = new Set(r.node_ids || []);
  // 上限服务端说了算（见 /api/admin/users 的 max_nodes_per_user）——
  // 客户端底部就 6 个灯位，绑多了那边显示不出来。
  const max = data.max_nodes_per_user || 6;

  openModal({
    title: `给 ${r.username} 分配节点`,
    fields: [
      { type: 'group',
        label: `勾选这个客户能用哪些节点，最多 ${max} 个（客户端底部就 ${max} 盏灯）。`
             + '一个都不勾 = 停止对他分发。' },
      ...nodes.map((n) => ({
        key: 'node_' + n.id,
        // 只写备注。这是**勾选清单**，不是节点表 —— 挑的时候认的是
        // "香港家宽"这个名字，编号和地址在这里是噪音，一列扫下来全是
        // 冒号数字，反而找不着人。地址和协议在「节点」页看。
        //
        // 「· 已停用」留着：它不是一个标签，是个警告 —— 勾了也不会
        // 生效（订阅里不会出现），不标出来的话点完保存会一脸问号。
        label: `${n.remark || n.name}${n.enabled ? '' : ' · 已停用'}`,
        type: 'checkbox',
      })),
    ],
    // 回填当前绑定，不然每次打开都是全空的，看不出现在分的是哪几个
    values: Object.fromEntries(nodes.map((n) => ['node_' + n.id, bound.has(n.id)])),
    submitText: '保存',
    // 勾满 max 个就把还没勾的置灰。只靠提交时报错的话，用户得点一次
    // 「保存」才知道超了，不如当场就点不动。
    onSubmit: async (v) => {
      const ids = Object.entries(v)
        .filter(([k, on]) => k.startsWith('node_') && on)
        .map(([k]) => Number(k.slice(5)));
      if (ids.length > max) { toast(`最多只能选 ${max} 个`, 'err'); return; }
      const res = await api(`/api/admin/users/${r.id}/nodes`, { method: 'PUT', body: { node_ids: ids } });
      toast(ids.length ? `已分配 ${ids.length} 个节点` : '已清空 —— 这个客户下次交互就会被收回订阅', 'ok');
      if (res.pushed) toast(`已推送给在线的 ${res.pushed} 条连接`, 'ok');
      closeModal(); render();
    },
    // 勾选到上限就把其余禁用
    onMount: (inputs) => {
      const boxes = Object.entries(inputs).filter(([k]) => k.startsWith('node_'));
      const sync = () => {
        const used = boxes.filter(([, el]) => el.checked).length;
        for (const [, el] of boxes) el.disabled = !el.checked && used >= max;
      };
      for (const [, el] of boxes) el.addEventListener('change', sync);
      sync();
    },
  });
}

/* =========================================================================
 * 页面：节点
 * ========================================================================= */

const nodeFields = () => [
  { key: 'link', label: '节点链接', required: true, type: 'textarea', rows: 3,
    placeholder: 'ss://2022-blake3-aes-128-gcm:服务端密钥:用户密钥@主机:端口#名称',
    help: '一行就行。支持 ss:// vmess:// vless:// trojan:// —— 链接里已经带着协议、地址、端口、密钥了。' },
  { key: 'remark', label: '备注（给自己看的）', help: '留空就显示链接里 # 后面的名字' },
  { key: 'sort_order', label: '排序', type: 'number', default: 100, help: '数字越小越靠前' },
  { key: 'enabled', label: '启用', type: 'checkbox', default: true },
];

function nodeBody(v) {
  const num = (x, d) => (x === '' || x === null || x === undefined || isNaN(Number(x))) ? d : Number(x);
  return {
    link: (v.link || '').trim(),
    remark: v.remark || '',
    enabled: !!v.enabled,
    sort_order: num(v.sort_order, 100),
  };
}

//: 协议在列表里用简称。链接里解出来的是全名（见 canoe_core/links.py 的
//: `SCHEMES`），但「shadowsocks」摆在标签里太长了 —— 一列协议标签就它
//: 一个把宽度撑到别人两倍，扫下来是一个长条。SS 是通行写法。
//: 只收长得离谱的；vmess / vless / trojan 本来就短，照原样。
const PROTO_SHORT = { shadowsocks: 'SS' };
const protoName = (p) => PROTO_SHORT[String(p || '').toLowerCase()] || p;

async function pageNodes(root) {
  const data = await api('/api/admin/nodes');
  const rows = data.items || [];

  root.append(h('div', { class: 'card-actions' },
    h('button', {
      class: 'btn btn-primary btn-sm', text: '新建节点',
      onclick: () => openModal({
        title: '新建节点', fields: nodeFields(),
        values: { enabled: true, sort_order: 100 },
        submitText: '创建',
        onSubmit: async (v) => {
          const res = await api('/api/admin/nodes', { method: 'POST', body: nodeBody(v) });
          toast(`节点已创建${res.pushed ? `，已通知 ${res.pushed} 条在线连接` : ''}`, 'ok');
          closeModal(); render();
        },
      }),
    }),
  ));

  root.append(h('div', { class: 'card' }, renderTable([
    { title: 'ID', key: 'id' },
    { title: '名称', render: (r) => h('span', {},
        h('strong', { text: r.remark || r.name }),
        r.remark && r.name !== r.remark ? h('span', { class: 'muted', text: '  ' + r.name }) : '') },
    // 协议和地址分两列。原来挤在一格里（"shadowsocks one.leycc.com:33222"），
    // 协议名长短不一，"one." 和 "h." 就对不齐 —— 协议越多越花。
    // 拆开之后地址那列左边缘永远在同一个位置，什么协议都齐。
    { title: '协议', render: (r) => r.protocol
        ? tag(protoName(r.protocol)) : h('span', { class: 'muted', text: '—' }) },
    { title: '地址', render: (r) => r.valid
        ? h('span', { class: 'mono', text: `${r.host}:${r.port}` })
        : tag('链接认不出', 'bad') },
    { title: '状态', render: (r) => r.enabled ? tag('启用', 'ok') : tag('停用') },
    { title: '排序', key: 'sort_order' },
    {
      title: '操作', render: (r) => actionCell(
        h('button', { class: 'btn btn-ghost btn-sm', text: '编辑', onclick: () => openModal({
          title: '编辑节点 · ' + (r.remark || r.name), fields: nodeFields(),
          values: { link: r.link, remark: r.remark, enabled: r.enabled, sort_order: r.sort_order },
          onSubmit: async (v) => {
            const res = await api('/api/admin/nodes/' + r.id, { method: 'PATCH', body: nodeBody(v) });
            toast(`已保存${res.pushed ? `，已通知 ${res.pushed} 条在线连接` : ''}`, 'ok');
            closeModal(); render();
          },
        }) }),
        h('button', { class: 'btn btn-danger btn-sm', text: '删除', onclick: () => confirmDo(`删除节点「${r.remark || r.name}」？`, () => simple('/api/admin/nodes/' + r.id, 'DELETE', '已删除')) }),
      ),
    },
  ], rows, '还没有节点 —— 点上面的「新建节点」，把链接粘进去就行')));
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

// 「概览」整页删掉了 —— 那一页就一排统计卡，用户说没必要单开，
// 搬到用户页顶上去了。默认落地页也跟着改成「用户」。
const PAGES = [
  { key: 'users',    label: '用户', ico: '☺', title: '用户管理', render: pageUsers },
  { key: 'nodes',    label: '节点', ico: '⛵', title: '节点管理', render: pageNodes },
  { key: 'sessions', label: '会话', ico: '⇄', title: '在线会话', render: pageSessions },
  { key: 'releases', label: '发布', ico: '⇪', title: '客户端版本发布', render: pageReleases },
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

// 渲染当前页。
//
// 这里**不放「加载中…」占位**：那个占位 append 上去之后一直没删，
// 结果每次切页都在内容上面挂一行"加载中…"（用户看到的）。而且它本来
// 也只是一闪而过的字，不如干脆等数据回来直接出内容 —— 接口都是本机
// 或自家服务端，慢不到哪去。
async function render() {
  const meta = PAGES.find((p) => p.key === state.page) || PAGES[0];
  $('#page-title').textContent = meta.title;
  const root = clear($('#page'));
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

//: 收掉启动屏（index.html 里那个 booting 类）。
//: showApp / showLogin 都要调 —— 谁先跑都不能让它挂在那儿。
function endBoot() { document.documentElement.classList.remove('booting'); }

function showLogin(message) {
  endBoot();
  $('#app-screen').hidden = true;
  $('#login-screen').hidden = false;
  const err = $('#login-error');
  if (message) { err.hidden = false; err.textContent = message; }
  else { err.hidden = true; err.textContent = ''; }
  $('#login-pass').value = '';
}

//: 侧边栏底部那个「我是谁」。单独拎出来是因为改名之后要重画一次 ——
//: state.me 是登录那一刻的快照，改完名字它还留着旧的。
//:
//: 一行：「管理员：admin」。角色在前当标签、用户名在后加粗，读起来是
//: "谁在操作"，比原来上下两行（用户名 / 角色）省一行高度。
function renderWhoami() {
  const el = $('#whoami');
  el.textContent = '';
  el.append(
    document.createTextNode((state.me.role === 'admin' ? '管理员' : state.me.role) + '：'),
    h('b', { text: state.me.username }),
  );
}

async function showApp() {
  endBoot();
  $('#login-screen').hidden = true;
  $('#app-screen').hidden = false;
  renderWhoami();
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

  // 没令牌的话启动屏压根不该出现（行内脚本没打 booting 类），
  // 这里只是兜底：万一 sessionStorage 读到了空串之类的边界。
  if (!state.token) endBoot();
  tryResume();
}

document.addEventListener('DOMContentLoaded', init);

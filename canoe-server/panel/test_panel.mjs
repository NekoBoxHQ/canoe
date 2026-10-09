/* 面板的 DOM 冒烟测试 —— 用 linkedom 造一个 DOM，把 app.js 真跑一遍。
 *
 * 起 curl 只能证明静态文件发得出去；这个能证明**页面真的渲染得出来**：
 * 登录 -> 六个标签页逐个渲染 -> 弹窗能构造出来。
 *
 * 用法（需要 bun，只为这一条 JS 测试装个运行时）：
 *     cd canoe-server/panel
 *     bun add -d linkedom
 *     bun run test_panel.mjs
 *
 * 不放进 Python 那套测试里，是因为它需要 JS 运行时 —— 服务端本身不需要。
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseHTML } from 'linkedom';

//: 面板目录。默认就是脚本所在目录；从别处跑时可以用 PANEL_DIR 指过来。
const PANEL = process.env.PANEL_DIR || dirname(fileURLToPath(import.meta.url));

let pass = 0, fail = 0;
const check = (label, cond, extra = '') => {
  if (cond) { pass++; console.log(`  [ok]   ${label}`); }
  else { fail++; console.log(`  [FAIL] ${label}  ${extra}`); }
};

// ---------- 造 DOM ----------
const html = readFileSync(`${PANEL}/index.html`, 'utf8');
const { window, document, Node } = parseHTML(html);

// ---------- 浏览器 API 垫片 ----------
const store = new Map();
const storage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
};

globalThis.window = window;
globalThis.document = document;
globalThis.Node = Node;
globalThis.sessionStorage = storage;
globalThis.localStorage = storage;
globalThis.navigator = { clipboard: { writeText: () => {} } };
globalThis.setTimeout = (fn, ms) => 0;   // 别让轮询心跳把测试挂住
globalThis.confirm = () => true;

// ---------- fetch 假实现 ----------
const calls = [];
const FIXTURES = [
  [/\/api\/login$/, { token: 'tok-admin', user: { id: 1, username: 'admin', role: 'admin' } }],
  [/\/api\/me$/, { id: 1, username: 'admin', role: 'admin', status: 'active' }],
  [/\/api\/health$/, { ok: true, app: 'Canoe Server', version: '1.0.0' }],
  [/\/api\/admin\/stats/, {
    users_total: 12, users_active: 11, users_banned: 1, nodes_total: 3, nodes_enabled: 2,
    sessions_total: 40, sessions_online: 5, config_version: 7, push: { connections: 4, users: 2 },
  }],
  [/\/api\/admin\/users\?/, { items: [
    { id: 1, username: 'admin', role: 'admin', status: 'active', online: true, expire_at: 0, max_devices: 3, remark: '主账号', last_login_at: 1790000000, subscription: '', subscription_lines: 0 },
    { id: 2, username: 'demo', role: 'user', status: 'banned', online: false, expire_at: 1799000000, max_devices: 3, remark: '', last_login_at: null,
      subscription: 'ss://2022-blake3-aes-128-gcm:AAAA:BBBB@one.leycc.com:33222#日本\nss://x:y@two.example.com:443#备用', subscription_lines: 2 },
  ]}],
  [/\/api\/admin\/nodes$/, { items: [
    { id: 3, name: '香港-01', remark: '', enabled: true, sort_order: 10,
      entry: { transport: 'ws', host: 'canoe.example.com', port: 443, uuid: 'u-1', path: '/e/hk01', sni: 'canoe.example.com', tls: true, insecure: false },
      real: { protocol: 'vless', host: '203.0.113.7', port: 8443, uuid: 'r-1', flow: '', tls: true, sni: 'real.example.com', fingerprint: 'chrome', network: 'tcp', ws_path: '', ws_host: '', grpc_service: '', insecure: false, extra: {} },
      created_at: 0, updated_at: 0 },
  ]}],
  [/\/api\/admin\/sessions/, { items: [
    { session_id: 'abcdef0123456789', user_id: 2, username: 'demo', node_name: '香港-01', device_id: 'dev-1', client_ip: '1.2.3.4', mode: 'system_proxy', online: true, revoked: false, created_at: 0, last_seen: 1790000000 },
  ]}],
  [/\/api\/admin\/releases$/, { items: [
    { id: 1, version: '1.1.0', filename: 'Canoe-1.1.0.zip', size: 83276159, sha256: 'a'.repeat(64), notes: 'x', min_version: '1.0.0', enabled: true, published_at: 1790000000 },
  ], latest: '1.1.0' }],
];

globalThis.fetch = async (path, opts = {}) => {
  calls.push(`${opts.method || 'GET'} ${path}`);
  const hit = FIXTURES.find(([re]) => re.test(path));
  const body = hit ? hit[1] : {};
  return {
    ok: true, status: 200,
    text: async () => JSON.stringify(body),
  };
};

// ---------- 加载 app.js ----------
const src = readFileSync(`${PANEL}/app.js`, 'utf8');
const run = new Function('window', 'document', 'Node', 'sessionStorage', 'localStorage',
                         'navigator', 'fetch', 'setTimeout', 'console', src);
run(window, document, Node, storage, storage, globalThis.navigator, globalThis.fetch,
    globalThis.setTimeout, console);

const tick = () => new Promise((r) => queueMicrotask(r));

console.log('\n== 面板 DOM 冒烟测试 ==\n');
console.log('[1] 启动');

document.dispatchEvent(new window.Event('DOMContentLoaded'));
await tick(); await tick();

check('登录页可见', !document.querySelector('#login-screen').hidden);
check('主界面初始隐藏', document.querySelector('#app-screen').hidden);

console.log('\n[2] 登录');
const form = document.querySelector('#login-form');
document.querySelector('#login-user').value = 'admin';
document.querySelector('#login-pass').value = 'secret123';
form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
await tick(); await tick(); await tick();

check('登录后主界面显示', !document.querySelector('#app-screen').hidden, JSON.stringify(calls));
check('登录页隐藏', document.querySelector('#login-screen').hidden);
check('令牌已存进 sessionStorage', !!storage.getItem('canoe.panel.token'));
check('侧边栏有 6 个入口', document.querySelectorAll('#nav .nav-item').length === 6,
      String(document.querySelectorAll('#nav .nav-item').length));
check('身份显示出来了', /admin/.test(document.querySelector('#whoami').textContent));

console.log('\n[3] 概览页');
const page = document.querySelector('#page');
check('渲染出了统计卡', page.querySelectorAll('.stat').length >= 6,
      String(page.querySelectorAll('.stat').length));
check('在线会话数字正确', /5/.test(page.textContent), page.textContent.slice(0, 200));
check('没有「加载失败」', !page.textContent.includes('加载失败'), page.textContent.slice(0, 200));

console.log('\n[4] 逐个标签页渲染');
const navItems = [...document.querySelectorAll('#nav .nav-item')];
const names = ['概览', '用户', '节点', '会话', '发布', '中转层'];
for (let i = 0; i < navItems.length; i++) {
  navItems[i].dispatchEvent(new window.Event('click', { bubbles: true }));
  await tick(); await tick(); await tick();
  const body = document.querySelector('#page').textContent;
  check(`${names[i]} 页渲染出来了`, body.length > 10 && !body.includes('加载失败'),
        body.slice(0, 160));
}

console.log('\n[5] 表格内容');
// 回到用户页
navItems[1].dispatchEvent(new window.Event('click', { bubbles: true }));
await tick(); await tick(); await tick();
const usersTxt = document.querySelector('#page').textContent;
check('用户页列出 admin / demo', usersTxt.includes('admin') && usersTxt.includes('demo'));
check('封禁状态有标签', usersTxt.includes('已封禁'));

// 节点页 —— real_* 必须显示（管理端该看得到）
navItems[2].dispatchEvent(new window.Event('click', { bubbles: true }));
await tick(); await tick(); await tick();
const nodesTxt = document.querySelector('#page').textContent;
check('节点页显示真实节点（管理端应当能看到）',
      nodesTxt.includes('203.0.113.7'), nodesTxt.slice(0, 200));
check('节点页也显示入口（客户端可见的那个）', nodesTxt.includes('canoe.example.com'));

console.log('\n[6] 弹窗能构造出来');
navItems[4].dispatchEvent(new window.Event('click', { bubbles: true }));   // 发布页
await tick(); await tick(); await tick();
const newBtn = [...document.querySelectorAll('#page button')].find((b) => b.textContent.includes('上传安装包'));
check('发布页有「上传安装包」按钮', !!newBtn);
newBtn.dispatchEvent(new window.Event('click', { bubbles: true }));
await tick();
check('弹窗打开了', !document.querySelector('#modal-root').hidden);
const modalTxt = document.querySelector('#modal-body').textContent;
check('弹窗里有版本号/说明/文件字段',
      modalTxt.includes('版本号') && modalTxt.includes('更新说明') && modalTxt.includes('安装包'),
      modalTxt.slice(0, 160));
document.querySelector('#modal-close').dispatchEvent(new window.Event('click', { bubbles: true }));
check('弹窗关掉了', document.querySelector('#modal-root').hidden);

console.log('\n[6.5] hidden 属性真的能藏住');
// 上面那条只验了 .hidden **属性**。属性为真不等于屏幕上看不见 ——
// 浏览器默认样式里那条 [hidden]{display:none} 是 UA 规则，作者样式里
// 只要给同一个元素写了 display 就会整条盖掉。踩过：
//   .modal-root{display:flex}  -> 登录页上永久挂着一块空对话框
//   #app-screen{display:grid}  -> ID 选择器优先级更高，后台界面一直
//                                 压在登录页后面渲染，画面发虚重叠
// linkedom 不做样式计算，所以这里只能查样式表本身写了没写那条兜底规则。
const css = readFileSync(`${PANEL}/style.css`, 'utf8');
check('★ 样式表里有 [hidden]{display:none} 兜底（否则弹窗/后台界面藏不住）',
      /\[hidden\][^{]*\{[^}]*display\s*:\s*none/.test(css), '没找到 [hidden] 规则');
check('★ 该规则带 !important（否则压不过 #id 那种选择器）',
      /\[hidden\][^{]*\{[^}]*display\s*:\s*none\s*!important/.test(css));

console.log('\n[7] 订阅编辑栏');
navItems[1].dispatchEvent(new window.Event('click', { bubbles: true }));   // 用户页
await tick(); await tick(); await tick();
const usersPage = document.querySelector('#page');
const usersNow = usersPage.textContent;
check('用户列表显示分发状态', usersNow.includes('个节点') && usersNow.includes('未配置'),
      usersNow.slice(0, 200));

const subBtns = [...usersPage.querySelectorAll('button')].filter((b) => b.textContent.trim() === '订阅');
check('每行有「订阅」按钮', subBtns.length === 2, String(subBtns.length));
// 点 demo 那行（它有订阅内容，能验回填）
subBtns[1].dispatchEvent(new window.Event('click', { bubbles: true }));
await tick();
const subModal = document.querySelector('#modal-body');
const area = subModal.querySelector('textarea');
check('弹窗里有订阅输入框', !!area);
check('★ 订阅用多行文本框（一行一个链接）',
      area && Number(area.getAttribute('rows')) >= 8,
      area ? String(area.getAttribute('rows')) : '');
check('★ 回填了现有订阅内容', area && area.value.includes('ss://'), area ? area.value.slice(0, 60) : '');
check('弹窗里提示清空即停止分发',
      subModal.textContent.includes('停止分发'), subModal.textContent.slice(0, 160));
document.querySelector('#modal-close').dispatchEvent(new window.Event('click', { bubbles: true }));

console.log(`\n${'='.repeat(48)}\n通过 ${pass} 项，失败 ${fail} 项\n${'='.repeat(48)}\n`);
process.exit(fail ? 1 : 0);

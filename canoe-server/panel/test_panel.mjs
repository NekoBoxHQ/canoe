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
//: 请求体也留一份 —— 光看"调了 PATCH"不够，得看它到底改了什么。
const bodies = [];
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
      node_ids: [3], subscription: '', subscription_lines: 1 },
  ]}],
  [/\/api\/admin\/nodes$/, { items: [
    { id: 3, name: '香港-01', remark: '香港家宽', enabled: true, sort_order: 10,
      link: 'ss://2022-blake3-aes-128-gcm:AAAA:BBBB@hk.example.com:33222#%E9%A6%99%E6%B8%AF-01',
      protocol: 'shadowsocks', host: 'hk.example.com', port: 33222, valid: true,
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
  if (opts.body) {
    try { bodies.push({ method: opts.method || 'GET', path, body: JSON.parse(opts.body) }); }
    catch (_) { /* 传的是 FormData 之类，不看 */ }
  }
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
//: render() 要连着 await 好几次（接口 -> 建 DOM），一个 tick 不够。
const settle = async (n = 8) => { for (let i = 0; i < n; i++) await tick(); };

console.log('\n== 面板 DOM 冒烟测试 ==\n');
console.log('[1] 启动');

document.dispatchEvent(new window.Event('DOMContentLoaded'));
await tick(); await tick();

check('登录页可见', !document.querySelector('#login-screen').hidden);
check('★ 登录页不写「只有 role=admin 能进」那句（用户说没必要）',
      !/role\s*=\s*admin/.test(document.querySelector('#login-screen').textContent),
      document.querySelector('#login-screen').textContent.slice(0, 120));
check('主界面初始隐藏', document.querySelector('#app-screen').hidden);

console.log('\n[2] 登录');
// 模拟"刷新时带着令牌"：index.html 的行内脚本会先打上 booting 类，
// 把登录框按住、顶上启动屏。登录成功后 showApp() 必须把它收掉，
// 否则用户会永远卡在「正在验证登录状态」。
document.documentElement.classList.add('booting');
const form = document.querySelector('#login-form');
document.querySelector('#login-user').value = 'admin';
document.querySelector('#login-pass').value = 'secret123';
form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
await tick(); await tick(); await tick();

check('登录后主界面显示', !document.querySelector('#app-screen').hidden, JSON.stringify(calls));
check('登录页隐藏', document.querySelector('#login-screen').hidden);
check('令牌已存进 sessionStorage', !!storage.getItem('canoe.panel.token'));
check('侧边栏有 5 个入口（中转层已删除）', document.querySelectorAll('#nav .nav-item').length === 5,
      String(document.querySelectorAll('#nav .nav-item').length));
check('身份显示出来了', /admin/.test(document.querySelector('#whoami').textContent));
check('★ 进后台时收掉了启动屏（否则会卡在「正在验证」）',
      !document.documentElement.classList.contains('booting'));
check('★ 进后台时收掉了启动屏（否则会卡在「正在验证」）',
      !document.documentElement.classList.contains('booting'));

console.log('\n[3] 概览页');
const page = document.querySelector('#page');
check('渲染出了统计卡', page.querySelectorAll('.stat').length >= 6,
      String(page.querySelectorAll('.stat').length));
check('在线会话数字正确', /5/.test(page.textContent), page.textContent.slice(0, 200));
check('没有「加载失败」', !page.textContent.includes('加载失败'), page.textContent.slice(0, 200));

console.log('\n[4] 逐个标签页渲染');
const navItems = [...document.querySelectorAll('#nav .nav-item')];
const names = ['概览', '用户', '节点', '会话', '发布'];
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

// 节点页 —— 一个节点就是一行链接，列表显示解析出来的协议/主机/端口
navItems[2].dispatchEvent(new window.Event('click', { bubbles: true }));
await tick(); await tick(); await tick();
const nodesTxt = document.querySelector('#page').textContent;
check('节点页显示备注', nodesTxt.includes('香港家宽'), nodesTxt.slice(0, 200));
check('★ 节点页显示解析出来的主机端口',
      nodesTxt.includes('hk.example.com:33222'), nodesTxt.slice(0, 200));
check('★ 节点页显示协议', nodesTxt.includes('shadowsocks'), nodesTxt.slice(0, 200));
check('节点页没有"入口 / 真实节点"那两列了',
      !nodesTxt.includes('真实节点') && !nodesTxt.includes('入口'), nodesTxt.slice(0, 200));

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

console.log('\n[7] 给客户分配节点');
navItems[1].dispatchEvent(new window.Event('click', { bubbles: true }));   // 用户页
await tick(); await tick(); await tick();
const usersPage = document.querySelector('#page');
check('用户列表显示分发状态', usersPage.textContent.includes('个节点') || usersPage.textContent.includes('未配置'),
      usersPage.textContent.slice(0, 200));

const bindBtn = [...usersPage.querySelectorAll('button')].find((b) => b.textContent.trim() === '分配节点');
check('每行有「分配节点」按钮', !!bindBtn);
bindBtn.dispatchEvent(new window.Event('click', { bubbles: true }));
// bindNodes 要先 await 拉节点列表才弹窗，一个 tick 不够
await tick(); await tick(); await tick(); await tick();
const bindModal = document.querySelector('#modal-body');
check('弹窗里列出了可选的节点',
      bindModal.textContent.includes('hk.example.com') || bindModal.textContent.includes('香港家宽'),
      bindModal.textContent.slice(0, 200));
check('★ 用勾选框而不是让管理员手打链接',
      bindModal.querySelectorAll('input[type=checkbox]').length >= 1,
      String(bindModal.querySelectorAll('input[type=checkbox]').length));
check('★ 回填了当前已绑的节点（demo 绑了 #3）',
      !!bindModal.querySelector('input[type=checkbox]'),
      bindModal.textContent.slice(0, 120));
document.querySelector('#modal-close').dispatchEvent(new window.Event('click', { bubbles: true }));

console.log('\n[8] 改用户名');
// 回到用户页，打开 demo 那一行的「编辑」
navItems[1].dispatchEvent(new window.Event('click', { bubbles: true }));
await tick(); await tick(); await tick();
const editBtn = [...document.querySelector('#page').querySelectorAll('button')]
  .find((b) => b.textContent.trim() === '编辑');
check('用户行有「编辑」按钮', !!editBtn);
editBtn.dispatchEvent(new window.Event('click', { bubbles: true }));
await tick();

const userModal = document.querySelector('#modal-body');
check('★ 编辑弹窗里有用户名输入框', !!userModal.querySelector('#f_username'),
      userModal.textContent.slice(0, 160));
check('★ 用户名回填了当前值',
      userModal.querySelector('#f_username')?.value === 'admin',
      String(userModal.querySelector('#f_username')?.value));

// 改个名字提交，看看请求体里到底带了什么
bodies.length = 0;
userModal.querySelector('#f_username').value = 'captain';
userModal.querySelector('#f_password').value = '';
document.querySelector('#modal-ok').dispatchEvent(new window.Event('click', { bubbles: true }));
await tick(); await tick(); await tick();

const patch = bodies.find((b) => b.method === 'PATCH' && /\/api\/admin\/users\/\d+/.test(b.path));
check('★ 提交时发的是 PATCH', !!patch, JSON.stringify(bodies));
check('★ 请求体里带着新用户名', patch?.body?.username === 'captain', JSON.stringify(patch));
check('没填密码就不发 password（不会把密码清空）',
      patch && !('password' in patch.body), JSON.stringify(patch));
check('★ 管理员改自己的名字后，侧边栏跟着换（不是登出前的旧快照）',
      document.querySelector('#whoami').textContent.includes('captain'),
      document.querySelector('#whoami').textContent);

console.log('\n[9] 行操作按钮真的能干成活');
// 踩过：`simple` / `confirmDo` 这两个助手函数在重构里被删掉了，
// 调用点却留着 —— 点「封禁」「删除」「踢下线」毫无反应，页面上连个
// 错都不显示（onclick 里抛 ReferenceError，只有控制台看得见）。
// 原来那套测试只渲染表格、从不点按钮，于是一路绿灯。
navItems[1].dispatchEvent(new window.Event('click', { bubbles: true }));   // 用户页
await tick(); await tick(); await tick();

const rowBtn = (label) => [...document.querySelector('#page').querySelectorAll('button')]
  .find((b) => b.textContent.trim() === label);

calls.length = 0;
bodies.length = 0;
rowBtn('封禁').dispatchEvent(new window.Event('click', { bubbles: true }));
await settle();
check('★ 点「封禁」真的发了请求',
      calls.some((c) => /^POST \/api\/admin\/users\/\d+\/ban$/.test(c)), JSON.stringify(calls));

calls.length = 0;
window.confirm = () => true;      // 删除会先问一句
rowBtn('删除').dispatchEvent(new window.Event('click', { bubbles: true }));
await settle();
check('★ 点「删除」真的发了 DELETE',
      calls.some((c) => /^DELETE \/api\/admin\/users\/\d+$/.test(c)), JSON.stringify(calls));

// 取消确认时不能动手
window.confirm = () => false;
calls.length = 0;
rowBtn('删除').dispatchEvent(new window.Event('click', { bubbles: true }));
await settle();
check('★ 确认框点取消就不发请求',
      !calls.some((c) => c.startsWith('DELETE')), JSON.stringify(calls));
window.confirm = () => true;

// 结构性兜底：onclick 里调的每个函数都得真有定义。
// 上面两条是行为测试，只覆盖点到的按钮；这条把整个文件的按钮都扫一遍。
const handlerFns = new Set();
for (const m of src.matchAll(/onclick:\s*\(\)\s*=>\s*([A-Za-z_$][\w$]*)\s*\(/g)) {
  handlerFns.add(m[1]);
}
check('扫到了一批按钮处理函数', handlerFns.size >= 5, [...handlerFns].join(','));
for (const name of [...handlerFns].sort()) {
  const defined = new RegExp(`(?:^|\\n)\\s*(?:async\\s+)?function\\s+${name}\\s*\\(|(?:const|let|var)\\s+${name}\\s*=`).test(src);
  check(`★ onclick 里用到的 ${name}() 有定义`, defined);
}

console.log('\n[10] 刷新时不该闪一下登录框');
// 用户报的："每次刷新会弹一下登录框"。原因：HTML 里 #login-screen 默认
// 就是可见的，而验令牌要一个网络来回 —— 那段时间登录卡片已经画出来了，
// 看着像"我掉线了"，然后才消失。
// 现在由 index.html 里的行内脚本赶在首次绘制之前打上 booting 类。
const bootHtml = readFileSync(`${PANEL}/index.html`, 'utf8');
check('★ index.html 里有启动屏', /id="boot-screen"/.test(bootHtml));
check('★ 行内脚本在 <head> 里（早于 body 绘制）',
      bootHtml.indexOf('<script>') < bootHtml.indexOf('<body>'), '脚本跑到 body 后面去了');

const inlineScript = (bootHtml.match(/<script>([\s\S]*?)<\/script>/) || [])[1] || '';
check('抠出了行内脚本', inlineScript.includes('booting'), inlineScript.slice(0, 80));

// 真跑一遍那段行内脚本，看它到底会不会打上 booting
const runInline = (token) => {
  document.documentElement.classList.remove('booting');
  if (token) storage.setItem('canoe.panel.token', token);
  else storage.removeItem('canoe.panel.token');
  new Function('sessionStorage', 'document', inlineScript)(storage, document);
  return document.documentElement.classList.contains('booting');
};
check('★ 有令牌 -> 打上 booting（登录框被按住）', runInline('tok-admin') === true);
check('★ 没令牌 -> 不打（照常显示登录框）', runInline('') === false);

const bootCss = readFileSync(`${PANEL}/style.css`, 'utf8');
check('★ CSS: booting 时藏掉登录框',
      /html\.booting\s+#login-screen\s*\{[^}]*display\s*:\s*none/.test(bootCss), '没找到这条规则');
check('★ CSS: booting 时显示启动屏',
      /html\.booting\s+#boot-screen\s*\{[^}]*display\s*:\s*flex/.test(bootCss), '没找到这条规则');
check('★ CSS: 启动屏默认藏着（不然没令牌时也会露一下）',
      /#boot-screen\s*\{[^}]*display\s*:\s*none/.test(bootCss), '没找到这条规则');
check('启动屏没用 hidden 属性（会被 [hidden]{display:none!important} 锁死）',
      !/<div id="boot-screen"[^>]*\shidden/.test(bootHtml));

console.log(`\n${'='.repeat(48)}\n通过 ${pass} 项，失败 ${fail} 项\n${'='.repeat(48)}\n`);
process.exit(fail ? 1 : 0);

'use strict';

/* 有令牌时在首次绘制前盖上启动屏。外置成文件是为了允许严格 CSP。 */
try {
  if (sessionStorage.getItem('canoe.panel.token')) {
    document.documentElement.classList.add('booting');
  }
} catch (_) {
  /* 无痕模式禁 sessionStorage：照常显示登录框。 */
}

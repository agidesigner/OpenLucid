/**
 * Pinned Apps — 把应用 pin 到侧边栏导航
 * 用法：在每个含侧边栏的页面引入此脚本（Avatar 之前）
 */
const PINNED_KEY = 'od_pinned_apps';

// 已知应用的导航定义（app_id → 导航配置）
// 新应用上线时在此添加一行即可
const APP_NAV_DEFS = {
  topic_studio: { nameKey: 'app_topic_studio', emoji: '💡', href: '/topic-studio.html' },
  kb_qa: { nameKey: 'app_kb_qa', emoji: '💬', href: '/kb-qa.html' },
};

function getPinnedApps() {
  try { return JSON.parse(localStorage.getItem(PINNED_KEY) || '[]'); }
  catch { return []; }
}

function isAppPinned(appId) {
  return getPinnedApps().includes(appId);
}

function togglePinnedApp(appId) {
  const pinned = getPinnedApps();
  const idx = pinned.indexOf(appId);
  if (idx >= 0) pinned.splice(idx, 1);
  else pinned.push(appId);
  localStorage.setItem(PINNED_KEY, JSON.stringify(pinned));
  injectPinnedNav();           // 实时刷新当前页侧边栏
  return pinned.includes(appId);
}

function injectPinnedNav() {
  // 找到"应用"导航链接作为插入锚点
  const appsLink = document.querySelector('nav a[href="/apps.html"]');
  if (!appsLink) return;
  const nav = appsLink.parentElement;

  // 移除上次注入的条目
  nav.querySelectorAll('[data-pinned-app]').forEach(el => el.remove());

  const pinned = getPinnedApps();
  const currentPath = location.pathname;

  // 倒序插入，保持 localStorage 中的顺序（最先 pin 的显示在最上面）
  [...pinned].reverse().forEach(appId => {
    const def = APP_NAV_DEFS[appId];
    if (!def) return;
    const isActive = currentPath === def.href;
    const a = document.createElement('a');
    a.href = def.href;
    a.setAttribute('data-pinned-app', appId);
    a.className = 'sidebar-item' + (isActive ? ' active' : '');
    a.innerHTML = `<span class="text-base leading-none w-[18px] text-center">${def.emoji}</span>${t(def.nameKey)}`;
    nav.insertBefore(a, appsLink);
  });
}

document.addEventListener('DOMContentLoaded', injectPinnedNav);

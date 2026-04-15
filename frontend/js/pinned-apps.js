/**
 * Sidebar navigation restructure — runs on every page.
 *
 * Transforms the hardcoded sidebar into a grouped structure:
 *
 *   🏠 Offers
 *   🎨 Brand Kit
 *
 *   — CREATE —
 *   🎬 Script Writer
 *   📝 Content Studio
 *   💡 Topic Studio
 *
 *   — LIBRARY —
 *   📁 Creations
 *   🎬 Videos
 *
 * Removes: Apps (redundant middle-tier) and KB Q&A (low-frequency,
 * accessed from offer detail instead).
 */

// Tools shown in the "Create" section — always visible, not user-toggleable
const CREATE_TOOLS = [
  { labelKey: 'app_script_writer',   emoji: '🎬', href: '/script-writer.html' },
  { labelKey: 'app_content_studio',  emoji: '📝', href: '/content-studio.html' },
  { labelKey: 'app_topic_studio',    emoji: '💡', href: '/topic-studio.html' },
];

function _makeSectionHeader(labelKey) {
  const div = document.createElement('div');
  div.className = 'nav-section-header';
  div.setAttribute('data-sidebar-generated', '1');
  div.style.cssText = 'padding:14px 12px 4px;font-size:10px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#50506A;';
  div.textContent = t(labelKey);
  return div;
}

function _makeNavItem({ emoji, labelKey, href, currentPath }) {
  const a = document.createElement('a');
  a.href = href;
  a.setAttribute('data-sidebar-generated', '1');
  a.className = 'sidebar-item' + (currentPath === href ? ' active' : '');
  a.innerHTML = `<span class="text-base leading-none w-[18px] text-center">${emoji}</span>${t(labelKey)}`;
  return a;
}

function restructureSidebar() {
  const nav = document.querySelector('aside.bg-sidebar nav, aside nav');
  if (!nav) return;

  // Prevent double-run
  if (nav.getAttribute('data-sidebar-restructured') === '1') return;

  const currentPath = location.pathname;

  // 1. Remove the "Apps" link (redundant middle tier)
  nav.querySelectorAll('a[href="/apps.html"]').forEach(a => a.remove());

  // 1b. Move Brand Kit to sit directly under Offers (top-level peer of Offers)
  const brandkitLink = nav.querySelector('a[href="/brandkit-list.html"]');
  const offersLink = nav.querySelector('a[href="/"]');
  if (brandkitLink && offersLink && brandkitLink.previousElementSibling !== offersLink) {
    nav.insertBefore(brandkitLink, offersLink.nextSibling);
  }

  // 2. Find anchor: the Creations link (everyone has it)
  const creationsLink = nav.querySelector('a[href="/creations.html"]');
  if (!creationsLink) return;

  // 3. Build and insert "Create" section before Creations link
  const createHeader = _makeSectionHeader('nav_group_create');
  nav.insertBefore(createHeader, creationsLink);

  for (const tool of CREATE_TOOLS) {
    const item = _makeNavItem({ ...tool, currentPath });
    nav.insertBefore(item, creationsLink);
  }

  // 4. Insert "Library" section header before Creations
  const libraryHeader = _makeSectionHeader('nav_group_library');
  nav.insertBefore(libraryHeader, creationsLink);

  nav.setAttribute('data-sidebar-restructured', '1');
}

// ── Legacy pinned-app helpers kept for apps.html compatibility ─────────────
// (apps.html still has pin/unpin UI; these are no-ops now but keep the API)
function getPinnedApps() { return []; }
function isAppPinned() { return false; }
function togglePinnedApp() { return false; }

document.addEventListener('DOMContentLoaded', restructureSidebar);

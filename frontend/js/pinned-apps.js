/**
 * Sidebar navigation grouping — runs on every page.
 *
 * HTML source already lays out the KB + Library tier:
 *
 *   🏠 Offers
 *   🎨 Brand Kit
 *   📁 Creations     ← anchor for section insertion
 *   🎬 Videos
 *
 * This script inserts the "Create" tools + section headers, producing:
 *
 *   🏠 Offers
 *   🎨 Brand Kit
 *   — CREATE —
 *   🎬 Script Writer
 *   📝 Content Studio
 *   💡 Topic Studio
 *   — LIBRARY —
 *   📁 Creations
 *   🎬 Videos
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

  // Idempotent: safe on re-entry / page reloads
  if (nav.getAttribute('data-sidebar-restructured') === '1') return;

  const currentPath = location.pathname;

  // Anchor: the Creations link must exist in the HTML source.
  const creationsLink = nav.querySelector('a[href="/creations.html"]');
  if (!creationsLink) return;

  // Insert CREATE section header + tools before Creations.
  nav.insertBefore(_makeSectionHeader('nav_group_create'), creationsLink);
  for (const tool of CREATE_TOOLS) {
    nav.insertBefore(_makeNavItem({ ...tool, currentPath }), creationsLink);
  }

  // Insert LIBRARY section header before Creations.
  nav.insertBefore(_makeSectionHeader('nav_group_library'), creationsLink);

  nav.setAttribute('data-sidebar-restructured', '1');
}

document.addEventListener('DOMContentLoaded', restructureSidebar);

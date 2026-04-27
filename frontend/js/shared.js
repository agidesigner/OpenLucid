/**
 * OpenLucid UI kit — shared client-side helpers.
 *
 * Loaded on every authenticated page. Exposes on window:
 *   - formatRelative(iso)   — localized relative time
 *   - toast(msg, opts)      — non-blocking notifications
 *   - toast.success / error / warning / info — shortcuts
 *
 * Also injects a <style id="od-tokens"> block with the semantic color
 * and component tokens (.tag-*, .od-toast-*, .od-status-dot, .od-divider).
 *
 * Token pattern: every semantic piece composes background + text + border
 * so pages don't need to restate the triplet each time.
 */

// ── Localized relative time ────────────────────────────────────────────
window.formatRelative = function (iso) {
  if (!iso) return '';
  const then = new Date(iso);
  const diff = (Date.now() - then.getTime()) / 1000;
  const lang = (window._odLang || 'zh') === 'en' ? 'en' : 'zh';
  if (diff < 60) return lang === 'en' ? 'just now' : '刚刚';
  if (diff < 3600) {
    const n = Math.round(diff / 60);
    return lang === 'en' ? `${n}m ago` : `${n} 分钟前`;
  }
  if (diff < 86400) {
    const n = Math.round(diff / 3600);
    return lang === 'en' ? `${n}h ago` : `${n} 小时前`;
  }
  if (diff < 86400 * 30) {
    const n = Math.round(diff / 86400);
    return lang === 'en' ? `${n}d ago` : `${n} 天前`;
  }
  return then.toLocaleDateString(lang === 'en' ? 'en-US' : 'zh-CN');
};

// ── Design tokens (injected once) ──────────────────────────────────────
// Preconnect + load Bricolage Grotesque (variable display face).
// Used only on large numbers (.num-display) and AI CTAs — not body text —
// so the 1 stylesheet stays worth its <100KB subset.
(function injectBrand() {
  if (document.getElementById('od-brand-font')) return;
  const pre1 = document.createElement('link');
  pre1.rel = 'preconnect';
  pre1.href = 'https://fonts.googleapis.com';
  const pre2 = document.createElement('link');
  pre2.rel = 'preconnect';
  pre2.href = 'https://fonts.gstatic.com';
  pre2.crossOrigin = 'anonymous';
  const link = document.createElement('link');
  link.id = 'od-brand-font';
  link.rel = 'stylesheet';
  link.href = 'https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&display=swap';
  document.head.appendChild(pre1);
  document.head.appendChild(pre2);
  document.head.appendChild(link);
})();

(function injectTokens() {
  if (document.getElementById('od-tokens')) return;
  const css = `
/* ── Signature numerals ──────────────────────────────────────────
   Use on large displayed numbers only (scores, counts, versions,
   timestamps). Gives the product a non-generic numeric voice while
   leaving body text on the system stack (fast FCP, 0 CLS in CJK).
*/
.num-display {
  font-family: 'Bricolage Grotesque', ui-sans-serif, -apple-system, BlinkMacSystemFont, sans-serif;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
  font-feature-settings: 'ss01' on;
}

/* ── AI-CTA shimmer ──────────────────────────────────────────────
   Add .ai-cta alongside the existing Tailwind gradient classes
   (from-purple-600 to-accent). On hover the gradient slides, giving
   AI-powered actions a subtle visual signature distinct from
   regular accent buttons.
*/
.ai-cta {
  background-size: 200% 100%;
  background-position: 0% 50%;
  transition: background-position 0.5s ease, opacity 0.2s ease, transform 0.1s ease;
}
.ai-cta:hover { background-position: 100% 50%; opacity: 1 !important; }
.ai-cta:active { transform: translateY(1px); }
.ai-cta .ai-spark { flex: none; opacity: 0.95; }

/* ── Semantic tag/pill tokens ─────────────────────────────────────
   Usage: <span class="od-tag od-tag-primary">Script</span>
   Shapes: .od-tag (pill). Colors: primary / success / warning / danger / info / neutral.
*/
.od-tag{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:500;line-height:1;padding:3px 8px;border-radius:9999px;border:1px solid transparent;white-space:nowrap}
.od-tag-primary { background:#EFF4FF; color:#1849C6; border-color:#DBE5FF }
.od-tag-success { background:#ECFDF5; color:#047857; border-color:#D1FAE5 }
.od-tag-warning { background:#FFFBEB; color:#B45309; border-color:#FEF3C7 }
.od-tag-danger  { background:#FEF2F2; color:#B91C1C; border-color:#FEE2E2 }
.od-tag-info    { background:#F1F5F9; color:#334155; border-color:#E2E8F0 }
.od-tag-neutral { background:#F3F4F6; color:#4B5563; border-color:#E5E7EB }
/* Non-semantic categorical hues — use only to distinguish unrelated types
   (e.g. knowledge categories). Do not use for status. */
.od-tag-violet  { background:#F5F3FF; color:#6D28D9; border-color:#EDE9FE }
.od-tag-pink    { background:#FDF2F8; color:#BE185D; border-color:#FCE7F3 }
.od-tag-teal    { background:#F0FDFA; color:#0F766E; border-color:#CCFBF1 }

/* Square variant (.od-chip) for rectangular labels (e.g. source badges). */
.od-chip{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:500;line-height:1;padding:3px 6px;border-radius:4px;border:1px solid transparent;white-space:nowrap}
.od-chip-primary { background:#EFF4FF; color:#1849C6; border-color:#DBE5FF }
.od-chip-success { background:#ECFDF5; color:#047857; border-color:#D1FAE5 }
.od-chip-warning { background:#FFFBEB; color:#B45309; border-color:#FEF3C7 }
.od-chip-danger  { background:#FEF2F2; color:#B91C1C; border-color:#FEE2E2 }
.od-chip-info    { background:#F1F5F9; color:#334155; border-color:#E2E8F0 }
.od-chip-neutral { background:#F3F4F6; color:#4B5563; border-color:#E5E7EB }
.od-chip-violet  { background:#F5F3FF; color:#6D28D9; border-color:#EDE9FE }
.od-chip-pink    { background:#FDF2F8; color:#BE185D; border-color:#FCE7F3 }
.od-chip-teal    { background:#F0FDFA; color:#0F766E; border-color:#CCFBF1 }

/* ── Status dot ───────────────────────────────────────────────────
   Usage: <span class="od-dot od-dot-success"></span>
*/
.od-dot{display:inline-block;width:6px;height:6px;border-radius:9999px;flex:none}
.od-dot-primary { background:#3B82F6 }
.od-dot-success { background:#10B981 }
.od-dot-warning { background:#F59E0B }
.od-dot-danger  { background:#EF4444 }
.od-dot-info    { background:#64748B }
.od-dot-neutral { background:#9CA3AF }
.od-dot-violet  { background:#8B5CF6 }
.od-dot-pink    { background:#EC4899 }
.od-dot-teal    { background:#14B8A6 }

/* ── Numbered step circle ────────────────────────────────────────
   Usage: <span class="od-step-circle od-step-primary">1</span>
   Fixed 20px circle with a bold digit, backgrounded by the given variant.
*/
.od-step-circle{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:9999px;font-size:11px;font-weight:700;flex:none}
.od-step-primary { background:#DBEAFE; color:#1D4ED8 }
.od-step-success { background:#D1FAE5; color:#047857 }
.od-step-warning { background:#FEF3C7; color:#B45309 }
.od-step-danger  { background:#FEE2E2; color:#B91C1C }
.od-step-violet  { background:#EDE9FE; color:#6D28D9 }
.od-step-neutral { background:#E5E7EB; color:#4B5563 }

/* ── Toast ────────────────────────────────────────────────────────
   Container is auto-created on first toast() call. Each toast slides
   in from the top-right, auto-dismisses, and cleans itself up.
*/
#od-toast-container{position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none;max-width:calc(100vw - 32px)}
.od-toast{pointer-events:auto;min-width:240px;max-width:420px;padding:10px 14px;border-radius:10px;font-size:13px;line-height:1.45;color:#fff;box-shadow:0 8px 24px rgba(17,24,39,.18),0 2px 6px rgba(17,24,39,.08);opacity:0;transform:translateY(-8px);transition:opacity .22s ease,transform .22s ease;display:flex;align-items:flex-start;gap:10px;word-break:break-word}
.od-toast.od-toast-visible{opacity:1;transform:translateY(0)}
.od-toast-success { background:#059669 }
.od-toast-error   { background:#DC2626 }
.od-toast-warning { background:#D97706 }
.od-toast-info    { background:#334155 }
.od-toast-icon{flex:none;width:18px;height:18px;margin-top:1px;font-weight:700;text-align:center}
.od-toast-close{margin-left:auto;opacity:.7;cursor:pointer;font-size:14px;line-height:1;padding:0 2px}
.od-toast-close:hover{opacity:1}
  `;
  const style = document.createElement('style');
  style.id = 'od-tokens';
  style.textContent = css;
  document.head.appendChild(style);
})();

// ── Guest-mode visibility ────────────────────────────────────────────
// Gate `.owner-only` elements (KB / Brand Kit / Settings entry points,
// destructive edit buttons) on whether the current session is an owner.
// Also exposes `window._od_is_guest` and emits a `od-auth-me` event with
// the /auth/me payload so page scripts can adapt without re-fetching.
(function injectGuestStyles() {
  if (document.getElementById('od-guest-styles')) return;
  // Banner is position:fixed so it doesn't become a flex item of the
  // row-flex body (which was stealing a column on the left). The zh/en
  // text is short enough to live in a single 24px top strip; full-detail
  // copy lives on the Settings → 访客分享 card where owners manage it.
  //
  // Also upgrade every h-screen page to dvh (dynamic viewport height).
  // Why: Tailwind's h-screen resolves to 100vh, which on mobile browsers
  // includes the address bar. Combined with body's overflow-hidden, the
  // last ~100px of any long result card (notably script-writer + content-
  // studio) ends up behind the address bar and is unreachable by scroll.
  // dvh tracks the actual visible viewport as chrome collapses, so the
  // bottom of long generated content is always scrollable into view.
  const css = `
body.h-screen { height: 100dvh; }
/* Owner-only elements: hidden by default, only revealed once
   detectSession() confirms a real owner session. This protects against
   three failure modes simultaneously:
     - guest cookie session (body.is-guest)
     - no-auth / open-access (body has no class)
     - /me request still in flight (no class yet)
   Real owners get a brief paint-flash where the avatar/menu pop in
   after detectSession resolves; that's the right trade for not leaking
   "settings" links to non-owners. */
body:not(.is-owner) .owner-only { display: none !important; }
body.is-guest { padding-top: 24px; }
#od-guest-banner { display: none; }
body.is-guest #od-guest-banner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: linear-gradient(90deg, #FEF3C7 0%, #FDE68A 100%);
  color: #78350F;
  font-size: 11px;
  font-weight: 500;
  height: 24px;
  padding: 0 14px;
  border-bottom: 1px solid #FCD34D;
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  pointer-events: none;
}
#od-guest-banner .od-guest-exit {
  margin-left: 14px;
  padding: 3px 12px;
  border-radius: 9999px;
  background: #1C1C28;
  color: #FDE68A;
  font-weight: 700;
  font-size: 11px;
  letter-spacing: 0.02em;
  cursor: pointer;
  pointer-events: auto;
  transition: background 0.15s, transform 0.1s;
  border: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.15);
}
#od-guest-banner .od-guest-exit:hover { background: #2A2A3C; }
#od-guest-banner .od-guest-exit:active { transform: translateY(1px); }
`;
  const s = document.createElement('style');
  s.id = 'od-guest-styles';
  s.textContent = css;
  document.head.appendChild(s);
})();

(function ensureGuestBanner() {
  if (document.getElementById('od-guest-banner')) return;
  const banner = document.createElement('div');
  banner.id = 'od-guest-banner';
  const en = (window._odLang || 'zh') === 'en';
  banner.innerHTML = `
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>
    <span>${en ? 'Guest mode — create content only; knowledge base is read-only' : '访客模式 — 只可以创建内容，不能修改知识库'}</span>
    <button type="button" class="od-guest-exit" title="${en ? 'Clear guest cookie and go to sign in' : '清除访客 cookie 并前往登录'}">
      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
      ${en ? 'Sign in as owner' : '以创建者身份登录'}
    </button>
  `;
  // Sole interactive element for guests — clicking clears both cookies
  // and redirects to /signin.html. The guest cookie otherwise has a
  // sliding 365-day expiry, so without this exit an owner using a
  // browser that once visited as guest would be stuck in guest mode.
  banner.querySelector('.od-guest-exit').addEventListener('click', async () => {
    try {
      await fetch('/api/v1/auth/signout', { method: 'POST', credentials: 'same-origin' });
    } catch (_) { /* even if the POST fails, still redirect — the cookie
                    will either naturally expire or the user can clear
                    it manually, and we shouldn't block the flow */ }
    location.href = '/signin.html';
  });
  if (document.body) document.body.insertBefore(banner, document.body.firstChild);
  else document.addEventListener('DOMContentLoaded', () => document.body.insertBefore(banner, document.body.firstChild));
})();

(function detectSession() {
  // Skip on unauthenticated pages (they shouldn't hit /auth/me themselves)
  const p = (location.pathname || '').toLowerCase();
  if (p.endsWith('/signin.html') || p.endsWith('/install.html')) return;
  fetch('/api/v1/auth/me', { credentials: 'same-origin' })
    .then(r => (r.ok ? r.json() : null))
    .then(me => {
      const isGuest = !!(me && me.is_guest);
      document.body.classList.toggle('is-guest', isGuest);
      document.body.classList.toggle('is-owner', !!me && !isGuest);
      window._od_is_guest = isGuest;
      window._od_me = me;
      document.dispatchEvent(new CustomEvent('od-auth-me', { detail: me }));
    })
    .catch(() => {});
})();

// ── Toast ──────────────────────────────────────────────────────────────
(function setupToast() {
  const ICONS = { success: '✓', error: '!', warning: '!', info: 'i' };

  function ensureContainer() {
    let c = document.getElementById('od-toast-container');
    if (!c) {
      c = document.createElement('div');
      c.id = 'od-toast-container';
      document.body.appendChild(c);
    }
    return c;
  }

  function show(message, opts = {}) {
    const type = opts.type || 'info';
    const duration = opts.duration != null ? opts.duration : (type === 'error' ? 5500 : 3500);
    const container = ensureContainer();

    const el = document.createElement('div');
    el.className = `od-toast od-toast-${type}`;

    const icon = document.createElement('span');
    icon.className = 'od-toast-icon';
    icon.textContent = ICONS[type] || '';
    el.appendChild(icon);

    const body = document.createElement('span');
    body.textContent = String(message ?? '');
    body.style.flex = '1';
    el.appendChild(body);

    const close = document.createElement('span');
    close.className = 'od-toast-close';
    close.textContent = '×';
    close.onclick = () => dismiss();
    el.appendChild(close);

    container.appendChild(el);
    requestAnimationFrame(() => el.classList.add('od-toast-visible'));

    let timer = null;
    function dismiss() {
      if (timer) clearTimeout(timer);
      el.classList.remove('od-toast-visible');
      setTimeout(() => el.remove(), 260);
    }
    if (duration > 0) timer = setTimeout(dismiss, duration);
    return dismiss;
  }

  window.toast = show;
  window.toast.success = (m, o) => show(m, { ...(o || {}), type: 'success' });
  window.toast.error   = (m, o) => show(m, { ...(o || {}), type: 'error' });
  window.toast.warning = (m, o) => show(m, { ...(o || {}), type: 'warning' });
  window.toast.info    = (m, o) => show(m, { ...(o || {}), type: 'info' });
})();

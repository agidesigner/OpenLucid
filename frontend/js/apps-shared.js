/**
 * OpenLucid — shared helpers for creation apps (Script Writer, Content Studio, ...)
 *
 * Usage: include this AFTER i18n.js and BEFORE the page's inline Alpine component.
 *   <script src="/js/apps-shared.js"></script>
 */

// ── Topic suggestion (shared by Script Writer + Content Studio) ────────────
//
// Calls the script-writer suggest-topic endpoint, which uses the offer's
// knowledge base + goal to propose a concrete topic.
//
// Returns: Promise<string>  — the suggested topic text
// Throws:  Error with user-facing message on failure
window.odSuggestTopic = async function({
  offer_id,
  goal,
  strategy_unit_id = undefined,
  config_id = undefined,
} = {}) {
  if (!offer_id) throw new Error('offer_id is required');
  const res = await fetch('/api/v1/apps/script-writer/suggest-topic', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      offer_id,
      goal,
      strategy_unit_id,
      config_id,
      language: typeof getApiLang === 'function' ? getApiLang() : 'zh-CN',
    }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `suggest-topic failed (${res.status})`);
  }
  const data = await res.json();
  return data.topic || '';
};

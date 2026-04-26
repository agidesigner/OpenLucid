/**
 * Shared trend-bridge panel — the "🔥 趁热点 / external context"
 * collapsible widget that lives at the top of every creation app
 * (script-writer, content-studio; topic-studio still uses its own
 * local panel implementation).
 *
 * Two responsibilities:
 *   1. Direct input — user pastes a URL or text describing a hot
 *      topic; ``trendExtract()`` pulls the text via /ai/extract-text
 *      into a shared 8000-char textarea.
 *   2. Inheritance display — when the page arrives with a
 *      ``topic_plan_id`` URL param, ``trendLoadInherited(planId)``
 *      fetches the plan and surfaces its persisted hotspot in a
 *      read-only banner so the user can see "this script is being
 *      generated WITH a DeepSeek V4 trend bridge" without
 *      re-pasting.
 *
 * Each app does:
 *
 *   x-data="Object.assign(myAppState(), window.odTrendPanel.makeState())"
 *
 * — gives the page these reactive fields + methods:
 *
 *   trendUrl            string       — URL field content
 *   trendText           string       — textarea content (extract-filled or pasted)
 *   trendPanelOpen      bool         — disclosure state of <details>
 *   trendExtracting     bool         — Extract button spinner
 *   trendInherited      object|null  — hotspot inherited from a topic_plan_id
 *   trendExtract()      method       — call from @click on the Extract button
 *   trendLoadInherited(planId)       — call from init() with URL's topic_plan_id
 *
 * On window.odTrendPanel:
 *
 *   makeState()         — factory for the reactive fields above
 *   toBody(state)       — { external_context_text, external_context_url }
 *                          for request payload; returns {} when empty so
 *                          ``...toBody(this)`` is safe to spread.
 */
(function () {
  // ── Methods bound onto the Alpine component itself ────────────────
  //
  // We attach these to the state object (rather than calling
  // ``window.odTrendPanel.extract(this, '/api/v1')`` from a template)
  // because Alpine inline expressions don't reliably resolve module-
  // level ``const`` (like ``API``) or external arguments. By exposing
  // ``trendExtract()`` / ``trendLoadInherited()`` directly on the
  // component, the template just writes ``@click="trendExtract()"`` —
  // simpler, no scope gotchas, and ``this`` inside the method is
  // unambiguous.
  async function _trendExtract() {
    if (!this.trendUrl || this.trendExtracting) return;
    this.trendExtracting = true;
    try {
      const fd = new FormData();
      fd.append('url', this.trendUrl.trim());
      const res = await fetch('/api/v1/ai/extract-text', {
        method: 'POST',
        body: fd,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        const msg = (window.t ? window.t('hotspot_extract_failed') : 'Extraction failed')
                    + (err.detail ? ': ' + err.detail : '');
        if (window.toast) window.toast.error(msg);
      } else {
        const data = await res.json();
        this.trendText = (data.text || '').slice(0, 8000);
      }
    } catch (e) {
      console.error('trendExtract failed', e);
      if (window.toast) window.toast.error(window.t ? window.t('hotspot_extract_failed') : 'Extraction failed');
    }
    this.trendExtracting = false;
  }

  async function _trendLoadInherited(planId) {
    if (!planId) return;
    try {
      const res = await fetch(`/api/v1/topic-plans/${planId}`);
      if (!res.ok) return;
      const plan = await res.json();
      if (plan && plan.hotspot && (plan.hotspot.event || plan.hotspot.keywords)) {
        this.trendInherited = plan.hotspot;
      }
    } catch (e) {
      console.warn('trend-panel: failed to load plan', planId, e);
    }
  }

  function makeState() {
    return {
      trendUrl: '',
      trendText: '',
      trendPanelOpen: false,
      trendExtracting: false,
      trendInherited: null,
      // Methods bound to the component (Alpine sees them as
      // first-class methods of the data scope).
      trendExtract: _trendExtract,
      trendLoadInherited: _trendLoadInherited,
    };
  }

  /**
   * Render the panel state into request-body fields.
   *
   * Returns ``{}`` when no signal is present — caller spreads with
   * ``...window.odTrendPanel.toBody(this)`` and the keys simply don't
   * appear in the payload, so backend defaults apply.
   *
   * Both ``external_context_text`` and ``external_context_url`` are
   * sent only when text is non-empty — URL alone (without extracted
   * text) is meaningless; the extract step has already pulled text
   * into ``trendText`` if the user used URL input.
   */
  function toBody(state) {
    const text = (state.trendText || '').trim();
    if (!text) return {};
    const url = (state.trendUrl || '').trim();
    return {
      external_context_text: text,
      external_context_url: url || undefined,
    };
  }

  window.odTrendPanel = {
    makeState,
    toBody,
  };
})();

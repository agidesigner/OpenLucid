/**
 * Shared output-language picker — one select-element pattern used by
 * every creation app (script-writer, content-studio, topic-studio,
 * kb-qa, strategy-unit).
 *
 * Behavior contract:
 *   - On page load, the select shows the UI locale as a placeholder.
 *   - When an offer is selected, ``detectFromOffer()`` calls
 *     /offers/{id}/primary_lang and switches the select to whatever
 *     the offer's KB content is in (zh ↔ en) — UNLESS the user has
 *     manually changed the dropdown, in which case their pick wins.
 *   - On submit, ``toBody()`` only emits ``language`` when the user
 *     manually overrode. Otherwise it omits the field so the backend
 *     re-runs KB detection authoritatively (single source of truth).
 *
 * Rationale: prior to v1.3.4 each app sent ``language: getApiLang()``
 * unconditionally, which forced English-KB offers to render Chinese
 * copy whenever the operator's UI was zh-CN. Centralising the rule
 * here means a future content app inherits the right behavior by
 * spreading ``window.odLangPicker.makeState()`` instead of
 * re-implementing the three-flag dance.
 *
 * Each app does:
 *
 *   x-data="Object.assign(myAppState(), window.odLangPicker.makeState())"
 *
 * exposing these reactive fields:
 *
 *   outputLang              'zh' | 'en'  — what the picker shows
 *   detectedLang            'zh' | 'en' | '' — KB primary language
 *   languageManualOverride  bool — true once user changes the select
 *
 * The select markup:
 *
 *   <select x-model="outputLang" @change="languageManualOverride = true">
 *     <option value="zh">🇨🇳 中文</option>
 *     <option value="en">🇺🇸 English</option>
 *   </select>
 *
 * On offer change:  await window.odLangPicker.detectFromOffer(this, offerId);
 * On submit body:   ...window.odLangPicker.toBody(this)
 */
(function () {
  function makeState() {
    return {
      outputLang: window._odLang === 'zh' ? 'zh' : 'en',
      detectedLang: '',
      languageManualOverride: false,
    };
  }

  /**
   * Probe ``/api/v1/offers/{id}/primary_lang`` and update the picker
   * to match the KB's content language. Respects the user's manual
   * override — once they touch the select, we never re-overwrite.
   *
   * Resilient: silent on fetch failures (degrades to UI-locale
   * default the picker was initialised with).
   */
  async function detectFromOffer(state, offerId, apiBase = '/api/v1') {
    if (!offerId) return;
    try {
      const r = await fetch(`${apiBase}/offers/${offerId}/primary_lang`);
      if (!r.ok) return;
      const d = await r.json();
      state.detectedLang = d.language || '';
      if (!state.languageManualOverride && state.detectedLang) {
        state.outputLang = state.detectedLang;
      }
    } catch (e) {
      // Silent. Picker keeps UI-locale default.
    }
  }

  /**
   * Emit the request-body fragment for ``language``.
   *
   * The picker is the single visible source of truth for the operator
   * — whatever it shows, we send. Two cases produce a "real" picker
   * value worth honoring:
   *
   *   - User manually changed the dropdown (``languageManualOverride``)
   *   - ``/primary_lang`` succeeded and set ``detectedLang``
   *
   * The third case — both flags empty — means detection failed (offer
   * empty, request failed, etc.) AND the user hasn't picked. The
   * picker is showing the UI-locale fallback, which we should NOT
   * forward to the backend; emit ``{}`` so resolve_output_language()
   * can KB-detect from the server-side sample.
   *
   * Why not just always send: a ZH-UI operator visiting an EN-KB
   * offer with /primary_lang briefly down would otherwise force ZH
   * output by sending ``zh-CN`` (the UI default) — exactly the
   * "frontend defaults win" failure mode the system-wide rule was
   * written to prevent.
   *
   * Why not just send on manual override (the prior, narrower rule):
   * the frontend's /primary_lang and the per-service KB samplers can
   * disagree (different field weightings). Honoring detected lang
   * here keeps the visible picker authoritative — what you see is
   * what you get — at the cost of trusting the (already-displayed)
   * detection.
   *
   * Caller spreads: ``...window.odLangPicker.toBody(this)``
   */
  function toBody(state) {
    if (!state.languageManualOverride && !state.detectedLang) return {};
    return {
      language: state.outputLang === 'zh' ? 'zh-CN' : 'en',
    };
  }

  /**
   * Reset to the un-overridden state — e.g., when the offer changes
   * and the new offer might be in a different KB language. Clears
   * the manual-override flag so the next ``detectFromOffer`` can
   * actually update the picker.
   */
  function resetForNewOffer(state) {
    state.languageManualOverride = false;
    state.detectedLang = '';
  }

  window.odLangPicker = {
    makeState,
    detectFromOffer,
    toBody,
    resetForNewOffer,
  };
})();

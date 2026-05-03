/**
 * Shared header-selector helpers — Offer + LLM Model dropdowns that
 * appear at the top of every "app" page (script-writer / content-
 * studio / topic-studio / future apps).
 *
 * Pre-component the three pages each duplicated:
 *   - an empty placeholder ``<option value="">`` (Select an offer /
 *     Default model) that meant "no choice", forcing the user to pick
 *     before the page worked.
 *   - their own copy of the URL → localStorage → first-offer fallback
 *     chain. The fallbacks were inconsistent (one defaulted to "" if
 *     cache miss, another picked the first offer, another silently
 *     left the user staring at an empty dropdown).
 *
 * v1.2.x standardises on:
 *   Offer  — URL param > localStorage[od_last_<appId>_offer]    > offers[0] (newest)
 *   Model  —              localStorage[od_last_<appId>_config]  > active model > configs[0]
 *
 * The /offers list endpoint sorts by created_at DESC, so offers[0] is
 * the newest. The /settings/llm endpoint exposes ``is_active: bool``
 * on each config — this is the user's "currently active" model in
 * Settings → LLM. Only one config is active at a time.
 *
 * appId is the short string used in localStorage keys. Existing
 * keys we keep for back-compat:
 *   - "sw"     (script-writer; was od_last_sw_offer)
 *   - "topic"  (topic-studio; was od_last_topic_offer)
 *   - "cs"     (content-studio; new — content-studio had no localStorage
 *              before this change)
 *
 * Usage in a page's Alpine.js x-data init():
 *
 *     this.offerId  = window.odHeaderSelectors.pickInitialOffer(
 *         this.offers, "sw", this._urlOfferId);
 *     this.configId = window.odHeaderSelectors.pickInitialConfig(
 *         this.llmConfigs, "sw");
 *
 *     // In @change handlers, persist:
 *     onOfferChange()  { window.odHeaderSelectors.rememberOffer("sw",  this.offerId);  ... }
 *     onConfigChange() { window.odHeaderSelectors.rememberConfig("sw", this.configId); }
 *
 * The 7 lines above replace the ~15 lines of priority-chain logic
 * each page used to carry inline.
 */
(function () {
  const KEY_OFFER  = (appId) => `od_last_${appId}_offer`;
  const KEY_CONFIG = (appId) => `od_last_${appId}_config`;
  // The model key is a composite `<config_id>::<model_name>` so users
  // can pick any (provider, model) combo their saved configs can reach,
  // not just the config's default model.
  const KEY_MODEL  = (appId) => `od_last_${appId}_model_key`;
  const SEP = "::";

  function _readKey(key) {
    try { return localStorage.getItem(key) || ''; } catch { return ''; }
  }
  function _writeKey(key, value) {
    try { localStorage.setItem(key, value); } catch { /* private mode */ }
  }

  /**
   * Expand the flat ``/settings/llm`` list into one entry per
   * (config, callable model) — fetching each config's available
   * models in parallel via ``/settings/llm/{id}/models`` (server-side
   * 5-min TTL cache, so repeat hits are cheap).
   *
   * Resilient by design: if ``/models`` fails for any reason (provider
   * down, key invalid, no /v1/models endpoint), that config still
   * contributes one entry — its saved default model. So the dropdown
   * is never empty just because one upstream is flaky.
   *
   * @param {Array<{id, label, provider, model_name, is_active}>} llmConfigs
   * @param {string} apiBase — usually "/api/v1"
   * @returns {Promise<Array<{key, config_id, model_name, label, provider, is_active, is_default}>>}
   *   ``key`` = ``<config_id>::<model_name>`` (use as ``<option value>``)
   *   ``label`` = ``<config.label> / <model_name>``
   *   ``is_default`` = the saved default for this config (preserves old UX)
   */
  async function expandConfigsToModels(llmConfigs, apiBase = '/api/v1') {
    if (!Array.isArray(llmConfigs) || llmConfigs.length === 0) return [];

    // /settings/* is owner-only; skip the per-config probe for guests/
    // unauthenticated visitors so they don't see N × 403 in the
    // console. Fallback path (just ``cfg.model_name`` per config)
    // already handles missing model lists.
    if (typeof window.odAwaitMe === 'function') await window.odAwaitMe();
    const isOwner = typeof window.odIsOwner === 'function' && window.odIsOwner();

    const fetched = await Promise.all(llmConfigs.map(async (cfg) => {
      if (!isOwner) return [cfg.model_name];
      try {
        const r = await fetch(`${apiBase}/settings/llm/${cfg.id}/models`);
        if (!r.ok) return [cfg.model_name];
        const body = await r.json();
        const models = Array.isArray(body?.models) ? body.models : [];
        if (models.length === 0) return [cfg.model_name];
        // Always include the default first, dedup against fetched list.
        const out = [cfg.model_name];
        for (const m of models) {
          if (m && m !== cfg.model_name) out.push(m);
        }
        return out;
      } catch {
        return [cfg.model_name];
      }
    }));

    const out = [];
    llmConfigs.forEach((cfg, i) => {
      for (const modelName of fetched[i]) {
        out.push({
          key: `${cfg.id}${SEP}${modelName}`,
          config_id: cfg.id,
          model_name: modelName,
          // ``Provider / model_name`` reads naturally and matches the
          // existing config-label convention (which is the same shape).
          label: `${cfg.provider || cfg.label || 'LLM'} / ${modelName}`,
          provider: cfg.provider,
          is_active: !!cfg.is_active,
          is_default: modelName === cfg.model_name,
        });
      }
    });
    return out;
  }

  /**
   * Resolve the offer to pre-select on page load.
   * @param {Array<{id:string}>} offers — fetched from /offers (sorted newest-first by the API)
   * @param {string} appId
   * @param {string} [urlOfferId] — from ?offer_id=... (optional; takes priority when present)
   * @returns {string} offer id, or '' when no offer is available
   */
  function pickInitialOffer(offers, appId, urlOfferId) {
    if (!Array.isArray(offers) || offers.length === 0) return '';
    if (urlOfferId && offers.find(o => o.id === urlOfferId)) return urlOfferId;
    const cached = _readKey(KEY_OFFER(appId));
    if (cached && offers.find(o => o.id === cached)) return cached;
    return offers[0].id;  // newest, by API ordering
  }

  /**
   * Resolve the LLM config to pre-select on page load.
   * @param {Array<{id:string,is_active:boolean}>} llmConfigs — from /settings/llm
   * @param {string} appId
   * @returns {string} config id, or '' when no config is configured
   */
  function pickInitialConfig(llmConfigs, appId) {
    if (!Array.isArray(llmConfigs) || llmConfigs.length === 0) return '';
    const cached = _readKey(KEY_CONFIG(appId));
    if (cached && llmConfigs.find(c => c.id === cached)) return cached;
    const active = llmConfigs.find(c => c.is_active);
    if (active) return active.id;
    return llmConfigs[0].id;
  }

  /** Persist the user's offer pick. No-op for empty values. */
  function rememberOffer(appId, offerId) {
    if (offerId) _writeKey(KEY_OFFER(appId), offerId);
  }

  /** Persist the user's model pick. No-op for empty values. */
  function rememberConfig(appId, configId) {
    if (configId) _writeKey(KEY_CONFIG(appId), configId);
  }

  /**
   * Pick the initial model from the expanded options list.
   * Priority: cached composite key > active config's default > first option.
   * Back-compat: if ``KEY_CONFIG`` exists from before composite keys, treat
   * it as "this config's default model" rather than ignoring the user's
   * prior choice.
   * @param {Array<{key, config_id, model_name, is_active, is_default}>} modelOptions
   * @param {string} appId
   * @returns {string} composite model key, or '' when no options
   */
  function pickInitialModelKey(modelOptions, appId) {
    if (!Array.isArray(modelOptions) || modelOptions.length === 0) return '';
    const cachedKey = _readKey(KEY_MODEL(appId));
    if (cachedKey && modelOptions.find(m => m.key === cachedKey)) return cachedKey;
    // Back-compat: previous UI only stored config_id. Resolve to that
    // config's default model so old users don't get reset to a random pick.
    const legacyConfig = _readKey(KEY_CONFIG(appId));
    if (legacyConfig) {
      const match = modelOptions.find(m => m.config_id === legacyConfig && m.is_default);
      if (match) return match.key;
    }
    const active = modelOptions.find(m => m.is_active && m.is_default);
    if (active) return active.key;
    return modelOptions[0].key;
  }

  /** Persist the user's model pick (composite key). */
  function rememberModelKey(appId, modelKey) {
    if (modelKey) _writeKey(KEY_MODEL(appId), modelKey);
  }

  /** Parse a composite ``<config_id>::<model_name>`` key. */
  function splitModelKey(modelKey) {
    if (!modelKey || typeof modelKey !== 'string') return { config_id: '', model_name: '' };
    const i = modelKey.indexOf(SEP);
    if (i < 0) return { config_id: modelKey, model_name: '' };
    return {
      config_id: modelKey.slice(0, i),
      model_name: modelKey.slice(i + SEP.length),
    };
  }

  window.odHeaderSelectors = {
    pickInitialOffer,
    pickInitialConfig,
    rememberOffer,
    rememberConfig,
    expandConfigsToModels,
    pickInitialModelKey,
    rememberModelKey,
    splitModelKey,
  };
})();

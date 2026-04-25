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

  function _readKey(key) {
    try { return localStorage.getItem(key) || ''; } catch { return ''; }
  }
  function _writeKey(key, value) {
    try { localStorage.setItem(key, value); } catch { /* private mode */ }
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

  window.odHeaderSelectors = {
    pickInitialOffer,
    pickInitialConfig,
    rememberOffer,
    rememberConfig,
  };
})();

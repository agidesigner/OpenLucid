/**
 * Shared "save preference" modal — one DOM mount, shared across image-
 * studio / content-studio / offer management. Refine surfaces call
 * `window.odMemorySave.open({...})` after a successful refine; the
 * offer tab uses the same modal for manual add/edit.
 *
 * Why a global rather than three Alpine components: the modal is
 * ephemeral, has no per-page state, and embedding it in three Alpine
 * roots would mean three copies of the same markup + methods (the
 * pages already share i18n.js this way for the same reason).
 *
 * The modal trusts callers to pass the right scope_id / merchant_id /
 * surface — there's no validation here. Server enforces scope
 * existence + cap; we surface its error message verbatim.
 */
(function () {
  'use strict';

  const STATE = {
    open: false,
    saving: false,
    error: '',
    // Inputs (mutated by the inputs in the modal markup)
    content: '',
    scopeType: 'offer',          // 'offer' | 'merchant'
    surface: 'all',              // 'all' | 'image' | 'script'
    // Context passed by caller — used to build the request and
    // labels but not shown as fields the user can edit.
    _offerId: null,
    _merchantId: null,
    _offerName: '',
    _merchantName: '',
    _defaultSurface: 'all',
    _mode: 'create',             // 'create' | 'edit'
    _memoryId: null,
    _source: 'manual',           // 'manual' | 'refine_capture'
    _scopeLocked: false,
    _sourceRef: null,
    _onSaved: null,              // optional caller callback
  };

  // i18n helper — fall back to English literal when t() unavailable.
  function _t(key, fallback) {
    try {
      const v = window.t ? window.t(key) : null;
      return v && v !== key ? v : (fallback || key);
    } catch { return fallback || key; }
  }

  function _normalizeSurface(value) {
    return ['all', 'image', 'script'].includes(value) ? value : 'all';
  }

  function _render() {
    const root = document.getElementById('od-memory-save-modal');
    if (!root) return;
    if (!STATE.open) {
      root.style.display = 'none';
      return;
    }
    root.style.display = 'flex';

    // Update text bindings each render so locale switches mid-session
    // don't leave stale labels.
    root.querySelector('[data-md-title]').textContent = _t('memory_save_title', 'Save preference');
    root.querySelector('[data-md-content-label]').textContent = _t('memory_save_content_label', 'Rule');
    root.querySelector('[data-md-content-hint]').textContent = _t('memory_save_content_hint', 'Future generations on this scope will follow this rule.');
    root.querySelector('[data-md-scope-label]').textContent = _t('memory_save_scope_label', 'Apply to');
    root.querySelector('[data-md-surface-label]').textContent = _t('memory_save_surface_label', 'Used by');
    root.querySelector('[data-md-cancel]').textContent = _t('btn_cancel', 'Cancel');
    root.querySelector('[data-md-save]').textContent = STATE.saving
      ? _t('memory_save_saving', 'Saving…')
      : _t('memory_save_btn', 'Save');

    // Scope radios — labels include the actual offer / merchant name
    // so the user knows what "this brand" means concretely.
    const offerLbl = root.querySelector('[data-md-scope-offer]');
    const merchantLbl = root.querySelector('[data-md-scope-merchant]');
    offerLbl.textContent = _t('memory_save_scope_offer', 'This product')
      + (STATE._offerName ? ` (${STATE._offerName})` : '');
    merchantLbl.textContent = _t('memory_save_scope_merchant', 'Whole brand')
      + (STATE._merchantName ? ` (${STATE._merchantName})` : '');

    const surfaceAll = root.querySelector('[data-md-surface-all]');
    surfaceAll.textContent = _t('memory_save_surface_all', 'All generators');
    root.querySelector('[data-md-surface-image]').textContent = _t('memory_save_surface_image', 'Image generation only');
    root.querySelector('[data-md-surface-script]').textContent = _t('memory_save_surface_script', 'Script writing only');

    // Disable the offer-scope radio when caller didn't pass an
    // offer_id (rare; happens for merchant-scoped surfaces).
    root.querySelector('input[data-md-scope-offer-input]').disabled = STATE._scopeLocked || !STATE._offerId;
    root.querySelector('input[data-md-scope-merchant-input]').disabled = STATE._scopeLocked || !STATE._merchantId;

    // Sync inputs from STATE (one-way every render — cheap, ~3 nodes).
    const ta = root.querySelector('textarea[data-md-content]');
    if (document.activeElement !== ta) ta.value = STATE.content;
    root.querySelectorAll('input[name="od-md-scope"]').forEach(r => {
      r.checked = (r.value === STATE.scopeType);
    });
    root.querySelectorAll('input[name="od-md-surface"]').forEach(r => {
      r.checked = r.value === STATE.surface;
    });

    const errEl = root.querySelector('[data-md-error]');
    errEl.textContent = STATE.error || '';
    errEl.style.display = STATE.error ? 'block' : 'none';

    const saveBtn = root.querySelector('[data-md-save]');
    saveBtn.disabled = STATE.saving || !STATE.content.trim();
  }

  function _mount() {
    if (document.getElementById('od-memory-save-modal')) return;

    const html = `
<div id="od-memory-save-modal"
     style="display:none; position:fixed; inset:0; z-index:70; background:rgba(0,0,0,0.4); backdrop-filter:blur(4px); align-items:center; justify-content:center; padding:1rem;">
  <div style="background:#fff; border-radius:1rem; box-shadow:0 25px 50px -12px rgba(0,0,0,0.25); max-width:32rem; width:100%; padding:1.25rem;">
    <div style="display:flex; align-items:center; gap:0.5rem; margin-bottom:0.75rem;">
      <span style="font-size:1.125rem;">💾</span>
      <h3 data-md-title style="font-size:1rem; font-weight:600; color:#111827; margin:0;"></h3>
    </div>

    <label data-md-content-label style="display:block; font-size:0.75rem; font-weight:500; color:#4b5563; margin-bottom:0.375rem;"></label>
    <textarea data-md-content rows="3" maxlength="500"
              style="width:100%; font-size:0.875rem; border:1px solid #e5e7eb; border-radius:0.5rem; padding:0.5rem 0.75rem; resize:none; outline:none;"></textarea>
    <p data-md-content-hint style="font-size:0.6875rem; color:#9ca3af; margin-top:0.25rem;"></p>

    <div style="margin-top:0.875rem;">
      <label data-md-scope-label style="display:block; font-size:0.75rem; font-weight:500; color:#4b5563; margin-bottom:0.375rem;"></label>
      <div style="display:flex; flex-direction:column; gap:0.375rem; font-size:0.8125rem; color:#374151;">
        <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
          <input type="radio" name="od-md-scope" value="offer" data-md-scope-offer-input>
          <span data-md-scope-offer></span>
        </label>
        <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
          <input type="radio" name="od-md-scope" value="merchant" data-md-scope-merchant-input>
          <span data-md-scope-merchant></span>
        </label>
      </div>
    </div>

    <div style="margin-top:0.75rem;">
      <label data-md-surface-label style="display:block; font-size:0.75rem; font-weight:500; color:#4b5563; margin-bottom:0.375rem;"></label>
      <div style="display:flex; flex-direction:column; gap:0.375rem; font-size:0.8125rem; color:#374151;">
        <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
          <input type="radio" name="od-md-surface" value="all">
          <span data-md-surface-all></span>
        </label>
        <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
          <input type="radio" name="od-md-surface" value="image">
          <span data-md-surface-image></span>
        </label>
        <label style="display:flex; align-items:center; gap:0.5rem; cursor:pointer;">
          <input type="radio" name="od-md-surface" value="script">
          <span data-md-surface-script></span>
        </label>
      </div>
    </div>

    <p data-md-error style="display:none; margin-top:0.625rem; font-size:0.75rem; color:#dc2626; background:#fef2f2; border-radius:0.5rem; padding:0.375rem 0.625rem;"></p>

    <div style="display:flex; align-items:center; justify-content:flex-end; gap:0.5rem; margin-top:1rem;">
      <button data-md-cancel
              style="font-size:0.75rem; color:#6b7280; padding:0.5rem 0.75rem; border-radius:0.5rem; background:transparent; border:none; cursor:pointer;"></button>
      <button data-md-save
              style="font-size:0.75rem; font-weight:600; color:#fff; background:#9333ea; padding:0.5rem 1rem; border-radius:0.5rem; border:none; cursor:pointer;"></button>
    </div>
  </div>
</div>`;
    const c = document.createElement('div');
    c.innerHTML = html;
    document.body.appendChild(c.firstElementChild);

    const root = document.getElementById('od-memory-save-modal');

    // Click outside the inner card to close — guard against clicking
    // inside, which would otherwise register on the backdrop too.
    root.addEventListener('click', (e) => { if (e.target === root) close(); });

    root.querySelector('[data-md-cancel]').addEventListener('click', close);
    root.querySelector('[data-md-save]').addEventListener('click', _save);

    root.querySelector('textarea[data-md-content]').addEventListener('input', (e) => {
      STATE.content = e.target.value;
      _render();
    });
    root.querySelectorAll('input[name="od-md-scope"]').forEach(r => {
      r.addEventListener('change', (e) => {
        STATE.scopeType = e.target.value;
        STATE.error = '';
        _render();
      });
    });
    root.querySelectorAll('input[name="od-md-surface"]').forEach(r => {
      r.addEventListener('change', (e) => {
        STATE.surface = _normalizeSurface(e.target.value);
        _render();
      });
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && STATE.open) close();
    });
  }

  function open(opts) {
    opts = opts || {};
    _mount();
    STATE.open = true;
    STATE.saving = false;
    STATE.error = '';
    STATE.content = (opts.content || '').slice(0, 500);
    STATE._offerId = opts.offerId || null;
    STATE._merchantId = opts.merchantId || null;
    STATE._offerName = opts.offerName || '';
    STATE._merchantName = opts.merchantName || '';
    STATE._defaultSurface = _normalizeSurface(opts.surface || 'all');
    STATE._mode = opts.memoryId ? 'edit' : 'create';
    STATE._memoryId = opts.memoryId || null;
    STATE._source = opts.source || (opts.sourceRef ? 'refine_capture' : 'manual');
    STATE._sourceRef = opts.sourceRef || null;
    STATE._onSaved = typeof opts.onSaved === 'function' ? opts.onSaved : null;
    STATE._scopeLocked = STATE._mode === 'edit';
    // Default scope: offer (most specific). Editing keeps the existing
    // scope because PATCH intentionally changes only content/surface.
    STATE.scopeType = opts.scopeType || (STATE._offerId ? 'offer' : 'merchant');
    // Default surface: the caller's surface (so the radio renders
    // pre-selected). User can opt to "all" if the rule is broader.
    STATE.surface = _normalizeSurface(STATE._defaultSurface);
    _render();
    setTimeout(() => {
      const ta = document.querySelector('#od-memory-save-modal textarea[data-md-content]');
      if (ta) ta.focus();
    }, 50);
  }

  function close() {
    STATE.open = false;
    _render();
  }

  async function _save() {
    if (!STATE.content.trim() || STATE.saving) return;
    const scopeId = STATE.scopeType === 'offer' ? STATE._offerId : STATE._merchantId;
    if (STATE._mode !== 'edit' && !scopeId) {
      STATE.error = _t('memory_save_no_scope', 'No matching scope id available');
      _render();
      return;
    }
    STATE.saving = true;
    STATE.error = '';
    _render();
    try {
      const body = {
        content: STATE.content.trim(),
        surface: STATE.surface,
      };
      let url = '/api/v1/memories';
      let method = 'POST';
      if (STATE._mode === 'edit' && STATE._memoryId) {
        url = `/api/v1/memories/${STATE._memoryId}`;
        method = 'PATCH';
      } else {
        body.scope_type = STATE.scopeType;
        body.scope_id = scopeId;
        body.source = STATE._source;
        if (STATE._sourceRef) body.source_ref = STATE._sourceRef;
      }
      const r = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        STATE.error = (err.error?.message || err.detail
          || _t('memory_save_failed', 'Save failed')).toString();
        return;
      }
      const saved = await r.json();
      if (STATE._onSaved) {
        try { STATE._onSaved(saved); } catch (e) { console.error(e); }
      }
      // Surface a quick confirmation through the existing toast
      // helper when present — degrades silently otherwise.
      try {
        if (window.toast?.success) {
          window.toast.success(_t('memory_save_ok', 'Saved'));
        }
      } catch {}
      close();
    } catch (e) {
      console.error(e);
      STATE.error = (e && e.message) || _t('memory_save_failed', 'Save failed');
    } finally {
      STATE.saving = false;
      _render();
    }
  }

  window.odMemorySave = { open, close };
})();

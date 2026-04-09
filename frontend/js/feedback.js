/**
 * In-app feedback widget — floating button + modal.
 * Self-mounts to the body. No mount div needed.
 *
 * Auto-hides if backend reports feedback is disabled (FEEDBACK_TO_EMAIL not set).
 *
 * Usage: <script src="/js/feedback.js"></script> in the page <head>.
 */
(function() {
  // Avoid double-mount on hot reload
  if (window.__odFeedbackMounted) return;
  window.__odFeedbackMounted = true;

  document.addEventListener('alpine:init', () => {
    Alpine.data('odFeedbackWidget', () => ({
      enabled: false,
      open: false,
      message: '',
      email: '',
      includeContext: true,
      sending: false,
      sent: false,
      errorMsg: '',

      async init() {
        try {
          const r = await fetch('/api/v1/feedback/status');
          if (r.ok) {
            const d = await r.json();
            this.enabled = !!d.enabled;
          }
        } catch {}
      },

      reset() {
        this.message = '';
        this.email = '';
        this.includeContext = true;
        this.sent = false;
        this.errorMsg = '';
      },

      openModal() {
        this.reset();
        this.open = true;
      },

      closeModal() {
        this.open = false;
      },

      async submit() {
        if (this.message.trim().length < 2 || this.sending) return;
        this.sending = true;
        this.errorMsg = '';
        try {
          const body = { message: this.message.trim() };
          if (this.email.trim()) body.email = this.email.trim();
          if (this.includeContext) {
            body.page_url = location.href;
            body.user_agent = navigator.userAgent;
          }
          const r = await fetch('/api/v1/feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
          if (r.ok || r.status === 204) {
            this.sent = true;
            setTimeout(() => { this.open = false; }, 1500);
          } else {
            const err = await r.json().catch(() => ({}));
            this.errorMsg = err.detail || err.error?.message || ('HTTP ' + r.status);
          }
        } catch (e) {
          this.errorMsg = e.message || 'Network error';
        }
        this.sending = false;
      },
    }));

    // Inject the widget into the body once Alpine is ready
    const el = document.createElement('div');
    el.setAttribute('x-data', 'odFeedbackWidget()');
    el.setAttribute('x-init', 'init()');
    el.innerHTML = `
<button x-show="enabled && !open" x-cloak
        @click="openModal()"
        :title="t('feedback_button_title')"
        class="fixed bottom-5 right-5 z-40 w-11 h-11 rounded-full bg-white border border-gray-200 shadow-lg flex items-center justify-center text-gray-500 hover:text-accent hover:border-accent transition-all hover:scale-105">
  <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
</button>

<div x-show="open" x-cloak
     @keydown.escape.window="closeModal()"
     class="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center p-4"
     @click="closeModal()">
  <div class="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden" @click.stop>
    <div class="p-5 border-b flex items-center justify-between">
      <h2 class="text-base font-semibold text-gray-900" x-text="t('feedback_modal_title')"></h2>
      <button @click="closeModal()" class="text-gray-400 hover:text-gray-600">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
      </button>
    </div>

    <div x-show="!sent" class="p-5 space-y-4">
      <div>
        <textarea x-model="message" rows="5" maxlength="4000"
                  :placeholder="t('feedback_placeholder')"
                  class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 leading-relaxed focus:outline-none focus:ring-2 focus:ring-accent/20 focus:border-accent resize-none"></textarea>
      </div>

      <div>
        <label class="block text-[11px] font-medium text-gray-500 mb-1" x-text="t('feedback_email_label')"></label>
        <input x-model="email" type="email" maxlength="254"
               :placeholder="t('feedback_email_placeholder')"
               class="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-accent/20 focus:border-accent">
      </div>

      <label class="flex items-center gap-2 text-xs text-gray-500 cursor-pointer select-none">
        <input type="checkbox" x-model="includeContext" class="rounded border-gray-300 text-accent focus:ring-accent/20">
        <span x-text="t('feedback_include_context')"></span>
      </label>

      <div x-show="errorMsg" class="text-xs text-red-500" x-text="errorMsg"></div>
    </div>

    <div x-show="sent" class="p-8 text-center">
      <div class="w-12 h-12 mx-auto rounded-full bg-green-50 flex items-center justify-center mb-3">
        <svg class="w-6 h-6 text-green-500" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>
      </div>
      <p class="text-sm font-medium text-gray-800" x-text="t('feedback_sent_title')"></p>
      <p class="text-xs text-gray-500 mt-1" x-text="t('feedback_sent_body')"></p>
    </div>

    <div x-show="!sent" class="p-4 border-t flex items-center justify-end gap-2">
      <button @click="closeModal()" class="px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-lg" x-text="t('feedback_cancel')"></button>
      <button @click="submit()" :disabled="sending || message.trim().length < 2"
              class="px-4 py-2 text-sm bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-50 transition">
        <span x-text="sending ? t('feedback_sending') : t('feedback_send')"></span>
      </button>
    </div>
  </div>
</div>`;
    document.body.appendChild(el);
  });
})();

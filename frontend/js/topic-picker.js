/**
 * Strategy Unit Picker modal — shared component.
 *
 * Usage:
 *   1. Add <script src="/js/topic-picker.js"></script> before the Alpine <script defer> tag.
 *   2. Add <div id="topic-picker-mount"></div> before </body>.
 *   3. Open the picker: window.dispatchEvent(new CustomEvent('open-topic-picker', { detail: { offerId } }))
 *   4. Handle selection: @topic-picker-select.window="handler($event.detail.unitId)"
 *      unitId is null when user chooses "不指定".
 */
document.addEventListener('alpine:init', () => {
  Alpine.data('topicPicker', () => ({
    show: false,
    units: [],

    init() {
      window.addEventListener('open-topic-picker', async (e) => {
        const res = await fetch(`/api/v1/strategy-units?offer_id=${e.detail.offerId}&page_size=50`);
        if (res.ok) this.units = (await res.json()).items;
        this.show = true;
      });
    },

    select(unitId) {
      this.show = false;
      window.dispatchEvent(new CustomEvent('topic-picker-select', { detail: { unitId: unitId || null } }));
    },
  }));

  document.querySelectorAll('#topic-picker-mount').forEach(mount => {
    mount.outerHTML = `
<div x-data="topicPicker" x-init="init()">
  <div x-show="show" x-cloak class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="show = false" x-transition.opacity>
    <div class="bg-white rounded-2xl w-full max-w-sm shadow-2xl" x-transition.scale.95 @click.stop>
      <div class="px-6 py-4 border-b">
        <h3 class="font-semibold text-gray-800">${t('picker_title')}</h3>
        <p class="text-xs text-gray-400 mt-0.5">${t('picker_subtitle')}</p>
      </div>
      <div class="px-6 py-4 space-y-2 max-h-64 overflow-y-auto">
        <button @click="select(null)"
                class="w-full text-left px-4 py-3 rounded-lg border border-gray-200 hover:border-accent/40 hover:bg-accent-light/30 transition text-sm text-gray-600">
          ${t('picker_no_unit')}
        </button>
        <template x-for="u in units" :key="u.id">
          <button @click="select(u.id)"
                  class="w-full text-left px-4 py-3 rounded-lg border border-gray-200 hover:border-accent/40 hover:bg-accent-light/30 transition">
            <div class="text-sm font-medium text-gray-800" x-text="u.name"></div>
            <div class="flex flex-wrap gap-1 mt-1">
              <span x-show="u.audience_segment" class="text-[10px] bg-green-50 text-green-600 px-1.5 py-0.5 rounded" x-text="u.audience_segment"></span>
              <span x-show="u.scenario" class="text-[10px] bg-orange-50 text-orange-600 px-1.5 py-0.5 rounded" x-text="u.scenario"></span>
              <span x-show="u.channel" class="text-[10px] bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded" x-text="u.channel"></span>
            </div>
          </button>
        </template>
        <div x-show="units.length === 0" class="text-center py-4 text-sm text-gray-400">${t('picker_empty')}</div>
      </div>
      <div class="px-6 py-3 border-t flex justify-end">
        <button @click="show = false" class="px-4 py-2 text-sm text-gray-500 hover:bg-gray-50 rounded-lg">${t('picker_cancel')}</button>
      </div>
    </div>
  </div>
</div>`;
  });
});

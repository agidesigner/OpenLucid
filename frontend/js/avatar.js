/**
 * User avatar dropdown — shared component.
 * Usage: add <div id="avatar-mount"></div> wherever the avatar should appear.
 * Include this script before the Alpine <script defer> tag.
 */
document.addEventListener('alpine:init', () => {
  Alpine.data('avatarMenu', () => ({
    currentUser: null,
    open: false,

    async init() {
      const res = await fetch('/api/v1/auth/me');
      if (res.ok) this.currentUser = await res.json();
    },

    async signOut() {
      await fetch('/api/v1/auth/signout', { method: 'POST' });
      location.href = '/signin.html';
    },
  }));

  // Inject HTML into every #avatar-mount on the page
  document.querySelectorAll('#avatar-mount').forEach(mount => {
    mount.outerHTML = `
<div x-data="avatarMenu()" x-init="init()" @click.outside="open = false" class="relative">
  <button @click="open = !open"
          class="w-8 h-8 rounded-full bg-accent flex items-center justify-center text-white text-sm font-semibold hover:bg-accent-hover transition-colors select-none"
          x-text="currentUser?.email?.charAt(0).toUpperCase() || '?'"></button>

  <div x-show="open" x-cloak
       x-transition:enter="transition ease-out duration-100"
       x-transition:enter-start="opacity-0 scale-95"
       x-transition:enter-end="opacity-100 scale-100"
       class="absolute right-0 mt-2 w-52 bg-white rounded-xl shadow-lg border border-gray-100 py-1 z-50">

    <div class="px-4 py-2.5 border-b border-gray-100">
      <p class="text-xs text-gray-500 truncate" x-text="currentUser?.email || ''"></p>
    </div>

    <a href="/account.html" class="flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors">
      <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
      ${t('avatar_account')}
    </a>

    <a href="/setting.html" class="flex items-center gap-2.5 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors">
      <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/><circle cx="12" cy="12" r="3" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8"/></svg>
      ${t('avatar_settings')}
    </a>

    <div class="border-t border-gray-100 mt-1 pt-1">
      <button @click="signOut()" class="w-full flex items-center gap-2.5 px-4 py-2.5 text-sm text-red-500 hover:bg-red-50 transition-colors">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
        ${t('avatar_signout')}
      </button>
    </div>
  </div>
</div>`;
  });
});

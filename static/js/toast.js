/**
 * toast.js — Lightweight toast notification system.
 * Usage: showToast('message', 'success'|'error'|'warning'|'info', durationMs)
 * Requires: <div id="toast-container"> in the HTML (added by index.html).
 */
(function () {
    'use strict';

    const MAX_TOASTS = 3;

    window.showToast = function (message, type, duration) {
        type     = type     || 'info';
        duration = duration != null ? duration : 4000;

        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            document.body.appendChild(container);
        }

        // Keep max 3 visible — evict oldest
        while (container.children.length >= MAX_TOASTS) {
            container.removeChild(container.firstChild);
        }

        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.setAttribute('role', 'alert');
        toast.setAttribute('aria-live', 'polite');

        const icon = { success: '✔', error: '✖', warning: '⚠', info: 'ℹ' }[type] || 'ℹ';
        toast.innerHTML =
            `<span class="toast-icon">${icon}</span>` +
            `<span class="toast-msg">${message}</span>` +
            `<button class="toast-close" aria-label="Dismiss">×</button>`;

        const dismiss = () => {
            toast.classList.add('toast-hide');
            toast.addEventListener('animationend', () => toast.remove(), { once: true });
        };
        toast.querySelector('.toast-close').addEventListener('click', dismiss);

        container.appendChild(toast);

        // Auto-dismiss
        if (duration > 0) {
            setTimeout(dismiss, duration);
        }
    };
})();

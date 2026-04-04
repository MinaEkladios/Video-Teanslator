/**
 * theme.js — Dark / light mode toggle.
 * Called by the theme-toggle button in the <header>.
 */
function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
}

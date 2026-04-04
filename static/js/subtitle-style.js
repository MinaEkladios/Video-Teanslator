/**
 * subtitle-style.js — Style presets + getSubtitleStyle() getter
 * Must be loaded BEFORE video-preview.js.
 * Works alongside the existing floating styles panel in index.html.
 */
(function () {
    'use strict';

    // ── Presets ──────────────────────────────────────────────────────────────────
    const PRESETS = {
        Netflix: { font: 'Inter, sans-serif',    size: 20, color: '#ffffff', stroke: '#000000', strokeWidth: 2,   bg: false, bgOpacity: 70, bgColor: '#000000', pos: 'bottom' },
        YouTube: { font: 'Inter, sans-serif',    size: 18, color: '#ffffff', stroke: '#000000', strokeWidth: 0,   bg: true,  bgOpacity: 70, bgColor: '#000000', pos: 'bottom' },
        Classic: { font: 'Inter, sans-serif',    size: 22, color: '#ffff00', stroke: '#000000', strokeWidth: 0,   bg: false, bgOpacity: 70, bgColor: '#000000', pos: 'bottom' },
        Arabic:  { font: "'Cairo', sans-serif",  size: 22, color: '#ffffff', stroke: '#000000', strokeWidth: 2,   bg: false, bgOpacity: 70, bgColor: '#000000', pos: 'bottom' },
    };

    // ── Helper — read a single input value safely ─────────────────────────────
    function val(id, fallback) {
        const el = document.getElementById(id);
        return el ? el.value : fallback;
    }
    function checked(id) {
        const el = document.getElementById(id);
        return el ? el.checked : false;
    }

    // ── getSubtitleStyle — global, used by video-preview.js ──────────────────
    window.getSubtitleStyle = function () {
        return {
            font:        val('font-family', 'Inter, sans-serif'),
            size:        parseInt(val('font-size', '20'), 10) || 20,
            color:       val('font-color', '#ffffff'),
            position:    val('position', 'bottom'),
            stroke:      val('stroke-color', '#000000'),
            strokeWidth: parseFloat(val('stroke-width', '2')) || 0,
            bg:          checked('bg-box'),
            bgOpacity:   parseInt(val('bg-opacity', '70'), 10) || 70,
            bgColor:     val('bg-color', '#000000'),
        };
    };

    // ── applyPreset — called from preset buttons in the HTML ──────────────────
    window.applyPreset = function (name) {
        const p = PRESETS[name];
        if (!p) return;

        const setVal = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
        const setChk = (id, v) => { const el = document.getElementById(id); if (el) el.checked = v; };

        setVal('font-family',  p.font);
        setVal('font-size',    p.size);
        setVal('font-color',   p.color);
        setVal('position',     p.pos);
        setVal('stroke-color', p.stroke);
        setVal('stroke-width', p.strokeWidth);
        setChk('bg-box',       p.bg);
        setVal('bg-opacity',   p.bgOpacity);
        setVal('bg-color',     p.bgColor);

        // Fire input events so all listeners (overlay, labels, saveStyle) react
        ['font-family','font-size','font-color','position','stroke-color',
         'stroke-width','bg-box','bg-opacity','bg-color'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.dispatchEvent(new Event('input', { bubbles: true }));
        });

        _saveStyle();
    };

    // ── Persist to localStorage ───────────────────────────────────────────────
    window.saveStyleToStorage = _saveStyle;
    function _saveStyle() {
        try { localStorage.setItem('subtitle_style', JSON.stringify(window.getSubtitleStyle())); }
        catch (_) {}
    }

    // ── Restore on load ────────────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', () => {

        // Restore previously saved style
        try {
            const stored = JSON.parse(localStorage.getItem('subtitle_style') || 'null');
            if (stored) {
                const setVal = (id, v) => { const el = document.getElementById(id); if (el && v !== undefined) el.value = v; };
                const setChk = (id, v) => { const el = document.getElementById(id); if (el && v !== undefined) el.checked = v; };
                setVal('font-family',  stored.font);
                setVal('font-size',    stored.size);
                setVal('font-color',   stored.color);
                setVal('position',     stored.position);
                setVal('stroke-color', stored.stroke);
                setVal('stroke-width', stored.strokeWidth);
                setChk('bg-box',       stored.bg);
                setVal('bg-opacity',   stored.bgOpacity);
                setVal('bg-color',     stored.bgColor);
            }
        } catch (_) {}

        // Live labels for range inputs
        function bindRangeLabel(rangeId, labelId, suffix) {
            const range = document.getElementById(rangeId);
            const label = document.getElementById(labelId);
            if (!range || !label) return;
            const update = () => { label.textContent = range.value + (suffix || ''); };
            range.addEventListener('input', update);
            update();
        }
        bindRangeLabel('stroke-width', 'stroke-width-val', '');
        bindRangeLabel('bg-opacity',   'bg-opacity-val',   '%');

        // Save on any style control change
        const styleIds = ['font-family','font-size','font-color','position',
                          'stroke-color','stroke-width','bg-box','bg-opacity','bg-color'];
        styleIds.forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('input', _saveStyle);
        });
    });
})();

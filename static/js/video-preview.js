/**
 * video-preview.js — Canvas-based subtitle preview overlay.
 * Requires: getSubtitleStyle()  from subtitle-style.js (loaded first)
 *           getSubtitles()       exposed by script.js  (loaded first)
 *
 * The canvas sits above the legacy subtitle-overlay div (z-index 5).
 * Once initialised, the div-overlay is hidden so only the canvas renders.
 */
(function () {
    'use strict';

    const ARABIC_RE = /[\u0600-\u06FF]/;

    /** hex + opacity(0-100) → rgba() string */
    function hexToRgba(hex, opacity) {
        const h = (hex || '#000000').replace('#', '');
        const r = parseInt(h.slice(0, 2), 16) || 0;
        const g = parseInt(h.slice(2, 4), 16) || 0;
        const b = parseInt(h.slice(4, 6), 16) || 0;
        return `rgba(${r},${g},${b},${(opacity / 100).toFixed(2)})`;
    }

    document.addEventListener('DOMContentLoaded', () => {
        const video  = document.getElementById('main-video');
        const canvas = document.getElementById('subtitle-canvas');
        if (!video || !canvas) return;

        const ctx = canvas.getContext('2d');

        // ── Size sync ──────────────────────────────────────────────────────────
        function syncSize() {
            const rect = video.getBoundingClientRect();
            canvas.width  = Math.round(rect.width  || video.videoWidth  || 640);
            canvas.height = Math.round(rect.height || video.videoHeight || 360);
        }
        video.addEventListener('loadedmetadata', syncSize);
        syncSize();

        // Keep canvas in sync when the video element resizes
        if (typeof ResizeObserver !== 'undefined') {
            new ResizeObserver(syncSize).observe(video);
        }
        window.addEventListener('resize', syncSize);

        // ── Hide legacy div overlay ────────────────────────────────────────────
        document.body.classList.add('canvas-preview-active');

        // ── Word-wrap helper ──────────────────────────────────────────────────
        function wrapLines(ctx, text, maxWidth) {
            const words = text.split(' ');
            const lines = [];
            let current = '';
            for (const word of words) {
                const candidate = current ? `${current} ${word}` : word;
                if (ctx.measureText(candidate).width > maxWidth && current) {
                    lines.push(current);
                    current = word;
                } else {
                    current = candidate;
                }
            }
            if (current) lines.push(current);
            return lines.length ? lines : [text];
        }

        // ── Draw ──────────────────────────────────────────────────────────────
        function drawSubtitle(text) {
            const w = canvas.width;
            const h = canvas.height;
            ctx.clearRect(0, 0, w, h);
            if (!text) return;

            const style = (typeof window.getSubtitleStyle === 'function')
                ? window.getSubtitleStyle()
                : { font: 'Inter, sans-serif', size: 20, color: '#ffffff',
                    stroke: '#000000', strokeWidth: 2, bg: false,
                    bgOpacity: 70, bgColor: '#000000', position: 'bottom' };

            const isArabic   = ARABIC_RE.test(text);
            // Scale font proportionally to canvas width; 640px is the design reference.
            // On a 300px-wide mobile video a 24px subtitle would fill ~8% — too large.
            const scaleFactor = Math.min(1, w / 640);
            const fontSize   = Math.max(8, Math.round((style.size || 20) * scaleFactor));
            const fontFamily = style.font || 'Inter, sans-serif';
            const lineHeight = Math.round(fontSize * 1.3);

            ctx.save();
            ctx.font      = `${fontSize}px ${fontFamily}`;
            ctx.textAlign = 'center';
            ctx.direction = isArabic ? 'rtl' : 'ltr';

            // Word-wrap to 90% of canvas width so text never overflows
            const maxLineWidth = w * 0.9;
            const lines  = wrapLines(ctx, text, maxLineWidth);
            const blockH = lines.length * lineHeight;

            // Vertical position of the BOTTOM of the text block
            const pos = style.position || 'bottom';
            let blockBottom;
            if (pos === 'top') {
                blockBottom = blockH + 24;
            } else if (pos === 'center') {
                blockBottom = h / 2 + blockH / 2;
            } else {  // bottom
                blockBottom = h - 28;
            }
            const blockTop = blockBottom - blockH;
            const x = w / 2;

            // Background box — spans the full multi-line block
            if (style.bg) {
                const maxTextW = Math.max(...lines.map(l => ctx.measureText(l).width));
                const padH = 8, padV = 5;
                ctx.fillStyle = hexToRgba(style.bgColor || '#000000', style.bgOpacity != null ? style.bgOpacity : 70);
                ctx.fillRect(
                    x - maxTextW / 2 - padH,
                    blockTop - padV,
                    maxTextW + padH * 2,
                    blockH + padV * 2
                );
            }

            // Draw each line (stroke then fill)
            const sw = parseFloat(style.strokeWidth) || 0;
            lines.forEach((line, i) => {
                // Baseline of this line: blockTop + (i+1) * lineHeight, shifted up slightly
                const lineY = blockTop + (i + 1) * lineHeight - Math.round(lineHeight * 0.15);
                if (sw > 0 && style.stroke && style.stroke !== 'none') {
                    ctx.lineWidth   = sw * 2;
                    ctx.strokeStyle = style.stroke;
                    ctx.lineJoin    = 'round';
                    ctx.strokeText(line, x, lineY);
                }
                ctx.fillStyle = style.color || '#ffffff';
                ctx.fillText(line, x, lineY);
            });

            ctx.restore();
        }

        // ── Render on timeupdate ───────────────────────────────────────────────
        function render() {
            const now  = video.currentTime;
            const subs = (typeof window.getSubtitles === 'function') ? window.getSubtitles() : [];
            const active = subs.find(s => now >= s.start && now <= s.end);
            drawSubtitle(active ? active.text : null);
        }

        video.addEventListener('timeupdate', render);
        video.addEventListener('pause',      render);
        video.addEventListener('seeked',     render);

        // Redraw on style changes so preview updates instantly even when paused
        document.addEventListener('input', (e) => {
            const styleIds = new Set(['font-family','font-size','font-color','position',
                                      'stroke-color','stroke-width','bg-box','bg-opacity','bg-color']);
            if (e.target && styleIds.has(e.target.id)) render();
        });
    });
})();

document.addEventListener('DOMContentLoaded', () => {
    // --- State ---
    let subtitles = [];
    let currentVideoFilename = null;
    let currentJobId = null;
    let editingId = null;
    // Whether a transcribe/translate job is currently running. When true,
    // the "Start New Job" buttons are disabled until the job finishes.
    let jobInProgress = false;
    // Last rendered subtitle text in the overlay (used to avoid flicker)
    let lastRenderedText = null;

    // ── Undo / Redo state ────────────────────────────────────────────────
    const undoStack = [];
    const redoStack = [];
    const MAX_STACK = 50;

    function snapshot() {
        undoStack.push(JSON.parse(JSON.stringify(subtitles)));
        if (undoStack.length > MAX_STACK) undoStack.shift();
        redoStack.length = 0;
        updateUndoUI();
    }

    function undo() {
        if (!undoStack.length) return;
        redoStack.push(JSON.parse(JSON.stringify(subtitles)));
        subtitles = undoStack.pop();
        renderSubtitleList();
        updateUndoUI();
    }

    function redo() {
        if (!redoStack.length) return;
        undoStack.push(JSON.parse(JSON.stringify(subtitles)));
        subtitles = redoStack.pop();
        renderSubtitleList();
        updateUndoUI();
    }

    function updateUndoUI() {
        const undoBtn = document.getElementById('undo-btn');
        const redoBtn = document.getElementById('redo-btn');
        if (undoBtn) {
            undoBtn.disabled = undoStack.length === 0;
            undoBtn.title = `Undo (${undoStack.length})`;
            const countEl = undoBtn.querySelector('.count');
            if (countEl) countEl.textContent = undoStack.length || '';
        }
        if (redoBtn) redoBtn.disabled = redoStack.length === 0;
    }

    async function saveSegments() {
        if (!currentJobId || !subtitles.length) return;
        const saveStatus = document.getElementById('save-status');
        if (saveStatus) saveStatus.textContent = 'Saving…';
        try {
            const res = await fetch('/api/v1/segments/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_id: currentJobId, segments: subtitles }),
            });
            const data = await res.json();
            if (saveStatus) saveStatus.textContent = data.ok ? 'All saved' : 'Save failed';
        } catch (_) {
            if (saveStatus) saveStatus.textContent = 'Save failed';
        }
    }

    // Expose subtitles for video-preview.js canvas renderer
    window.getSubtitles = () => subtitles;

    // --- DOM Elements ---
    const video = document.getElementById('main-video');
    const videoWrapper = document.getElementById('video-wrapper');
    const subtitleOverlay = document.getElementById('subtitle-overlay');
    const videoUpload = document.getElementById('video-upload');
    const currentTimeDisplay = document.getElementById('current-time');
    const durationDisplay = document.getElementById('duration');

    // Resize video-wrapper to match the loaded video's actual aspect ratio
    video.addEventListener('loadedmetadata', () => {
        if (!videoWrapper || !video.videoWidth || !video.videoHeight) return;
        videoWrapper.style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
        videoWrapper.style.width = 'auto';
        videoWrapper.style.maxWidth = '100%';
    });

    // Startup modal elements (blocking flow at app start)
    const startupModal = document.getElementById('startup-modal');
    const startupUploadInput = document.getElementById('startup-upload-input');
    const startupUploadBtn = document.getElementById('startup-upload-btn');
    const startupTranslateBtn = document.getElementById('startup-translate-btn');
    const startupTargetLang = document.getElementById('startup-target-lang');
    const startupProgress = document.getElementById('startup-progress');
    const startupSpinner = document.getElementById('startup-spinner');
    const startupNewJobBtn = document.getElementById('startup-newjob-btn');
    const mainNewJobBtn = document.getElementById('main-newjob-btn');
    const openStylesBtn = document.getElementById('open-styles-btn');
    const stylesPanel = document.getElementById('styles-panel');
    const closeStylesBtn = document.getElementById('close-styles-btn');

    // Inputs
    const fontFamily = document.getElementById('font-family');
    const fontSize = document.getElementById('font-size');
    const fontColor = document.getElementById('font-color');
    const position = document.getElementById('position');
    const animationType = document.getElementById('animation-type');

    // Preview scaling factor: multiply the user input to compute overlay font-size.
    // Example: PREVIEW_SCALE = 0.2 maps input 10 -> overlay 2 (visual trick requested).
    const PREVIEW_SCALE = 0.4;

    // Editor
    const startTimeInput = document.getElementById('start-time');
    const endTimeInput = document.getElementById('end-time');
    const subTextInput = document.getElementById('subtitle-text');
    const btnAdd = document.getElementById('add-subtitle');
    const btnUpdate = document.getElementById('update-subtitle');
    const btnSplit = document.getElementById('split-subtitle');
    const btnSetStart = document.getElementById('set-current-start');
    const btnSetEnd = document.getElementById('set-current-end');
    const subtitleList = document.getElementById('subtitle-list');
    const btnBurn = document.getElementById('burn-video');
    const statusMsg = document.getElementById('export-status');
    const exportSpinner = document.getElementById('export-spinner');
    const exportText = document.getElementById('export-text');

    // Translation handled in startup modal; editor-level translate controls removed

    // ── Floating progress overlay helpers ─────────────────────────────────────
    const progressOverlay    = document.getElementById('progress-overlay');
    const progressOverlayMsg = document.getElementById('progress-overlay-msg');
    const progressOverlayBar = document.getElementById('progress-overlay-bar');
    const progressOverlayPct = document.getElementById('progress-overlay-pct');

    function showProgress(msg, pct) {
        if (!progressOverlay) return;
        progressOverlay.classList.remove('hidden');
        if (progressOverlayMsg) progressOverlayMsg.textContent = msg || 'Processing\u2026';
        const p = Math.max(0, Math.min(100, Math.round(pct || 0)));
        if (progressOverlayBar) progressOverlayBar.style.width = p + '%';
        if (progressOverlayPct) progressOverlayPct.textContent = p + '%';
    }

    function hideProgress() {
        if (progressOverlay) progressOverlay.classList.add('hidden');
    }

    // --- Event Listeners ---

    // Video Upload (manual fallback) - only uploads, does NOT auto-transcribe
    videoUpload.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        if (exportText) exportText.textContent = "Uploading video...";
        const ok = await uploadFileToServer(file);
        if (ok) {
            if (exportText) exportText.textContent = "Upload complete. Use Translate to generate subtitles.";
        } else {
            if (exportText) exportText.textContent = "Upload failed.";
        }
    });

    // --- Startup Modal Flow ---
    // Helper: upload file to server and set currentVideoFilename + video src
    async function uploadFileToServer(file) {
        const formData = new FormData();
        formData.append('video', file);
        // Clear any previous UI state for a clean upload
        clearPreviousUI();
        startupProgress.textContent = 'Uploading...';
        try {
            const res = await fetch('/upload_video', { method: 'POST', body: formData });
            const data = await res.json();
            if (data.url) {
                currentVideoFilename = data.filename;
                currentJobId = data.job_id || null;
                video.src = data.url;
                startupProgress.textContent = 'Upload successful.';
                return true;
            }
            startupProgress.textContent = 'Upload failed: ' + (data.error || '');
            return false;
        } catch (err) {
            console.error(err);
            startupProgress.textContent = 'Network error during upload.';
            return false;
        }
    }

    // File select inside modal
    startupUploadInput.addEventListener('change', (e) => {
        const f = e.target.files[0];
        if (f) {
            document.getElementById('startup-upload-status').textContent = f.name + ' (' + Math.round(f.size/1024/1024) + 'MB)';
            startupUploadBtn.disabled = false;
        } else {
            document.getElementById('startup-upload-status').textContent = 'No file chosen';
            startupUploadBtn.disabled = true;
        }
    });

    // Upload button in modal
    startupUploadBtn.addEventListener('click', async () => {
        const f = startupUploadInput.files[0];
        if (!f) return;
        startupUploadBtn.disabled = true;
        const ok = await uploadFileToServer(f);
        if (ok) {
            // hide upload button and show translate + start-new-job
            startupUploadBtn.classList.add('hidden');
            startupUploadInput.disabled = true;
            startupTranslateBtn.classList.remove('hidden');
            startupTranslateBtn.disabled = false;
            if (startupNewJobBtn) startupNewJobBtn.classList.remove('hidden');
            if (mainNewJobBtn) mainNewJobBtn.classList.remove('hidden');
            startupProgress.textContent = 'Ready to Translate. Choose language and click Translate & Start.';
        } else {
            startupUploadBtn.disabled = false;
        }
    });

    // Small helper to toggle job-in-progress UI state
    function setJobInProgress(inProgress) {
        jobInProgress = !!inProgress;
        try {
            if (startupNewJobBtn) startupNewJobBtn.disabled = jobInProgress;
            if (mainNewJobBtn) mainNewJobBtn.disabled = jobInProgress;
        } catch (e) {
            // ignore
        }
    }

    // ── Translation retry helper ──────────────────────────────────────────────
    // Calls POST /translate_subtitles in a loop until ALL segments are translated
    // (i.e. every segment with an original_text has text !== original_text),
    // or until a genuine "no internet" condition is confirmed.
    // Returns { subtitles: [...], noInternet: bool, timedOut: bool }
    async function retryTranslateSegments(segs, lang) {
        const RETRY_DELAY_MS = 3000;
        const TIMEOUT_MS = 3 * 60 * 1000; // 3 minutes
        const startTime = Date.now();
        let attempt = 0;
        let currentSegs = segs.slice(); // working copy

        while (true) {
            // ── Timeout check ────────────────────────────────────────────────
            if (Date.now() - startTime > TIMEOUT_MS) {
                return {
                    subtitles: currentSegs,
                    noInternet: false,
                    timedOut: true,
                };
            }

            attempt++;

            // Count how many segments still need translation
            const untranslated = currentSegs.filter(
                s => s.original_text != null && s.text === s.original_text
            );
            const total = currentSegs.length;
            const done  = total - untranslated.length;

            // Front-line offline check
            if (!navigator.onLine) {
                showProgress('\u26a0\ufe0f No internet \u2014 keeping original language', 0);
                return { subtitles: currentSegs, noInternet: true, timedOut: false };
            }

            const pct = Math.round(40 + (done / Math.max(total, 1)) * 50);
            showProgress(
                `Translating\u2026 ${done}/${total} done (attempt ${attempt})`,
                pct
            );

            try {
                const res = await fetch('/translate_subtitles', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ subtitles: currentSegs, target_lang: lang }),
                });

                let body = {};
                try { body = await res.json(); } catch (_) {}

                // Server confirmed no internet (DNS failure)
                if (body.no_internet) {
                    showProgress('\u26a0\ufe0f No internet \u2014 keeping original language', 0);
                    return { subtitles: currentSegs, noInternet: true, timedOut: false };
                }

                if (res.ok && body.subtitles && body.subtitles.length > 0) {
                    currentSegs = body.subtitles;

                    // Check if ALL translatable segments are now translated
                    const stillUntranslated = currentSegs.filter(
                        s => s.original_text != null && s.text === s.original_text
                    );
                    if (stillUntranslated.length === 0) {
                        return { subtitles: currentSegs, noInternet: false, timedOut: false }; // all done!
                    }

                    // Some segments still untranslated — show count and retry
                    showProgress(
                        `${stillUntranslated.length} segment(s) still pending \u2014 retrying\u2026`,
                        Math.round(40 + ((total - stillUntranslated.length) / Math.max(total, 1)) * 50)
                    );
                } else {
                    console.warn(`[retryTranslate] attempt ${attempt}: HTTP ${res.status}, retrying\u2026`);
                }

            } catch (err) {
                console.warn(`[retryTranslate] attempt ${attempt} fetch error:`, err);
                if (!navigator.onLine) {
                    showProgress('\u26a0\ufe0f No internet \u2014 keeping original language', 0);
                    return { subtitles: currentSegs, noInternet: true, timedOut: false };
                }
            }

            await new Promise(r => setTimeout(r, RETRY_DELAY_MS));
        }
    }

    // ── Translation timeout modal ─────────────────────────────────────────────
    // Shows a modal dialog when retryTranslateSegments() times out.
    // Returns a Promise that resolves to 'continue' or 'retry'.
    function showTranslationTimeoutModal({ translatedCount, failedCount, totalCount }) {
        return new Promise((resolve) => {
            const overlay = document.createElement('div');
            overlay.style.cssText = [
                'position:fixed', 'inset:0', 'background:rgba(0,0,0,0.7)',
                'display:flex', 'align-items:center', 'justify-content:center',
                'z-index:9999', 'font-family:inherit',
            ].join(';');

            overlay.innerHTML = `
                <div style="
                    background: var(--color-bg-2, #1e1e2e);
                    color: var(--color-text-1, #fff);
                    border-radius: 16px;
                    padding: 32px;
                    max-width: 420px;
                    width: 90%;
                    text-align: center;
                    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
                ">
                    <div style="font-size:2rem;margin-bottom:12px;">\u23f1\ufe0f</div>
                    <h3 style="margin:0 0 12px;font-size:1.2rem;">
                        Translation Taking Too Long
                    </h3>
                    <p style="margin:0 0 8px;opacity:0.8;font-size:0.95rem;">
                        Translated <strong>${translatedCount}</strong> of
                        <strong>${totalCount}</strong> segments.
                        <strong>${failedCount}</strong> could not be translated
                        due to a slow connection.
                    </p>
                    <p style="margin:0 0 24px;opacity:0.7;font-size:0.85rem;">
                        You can continue and edit the untranslated segments manually,
                        or go back and try again.
                    </p>
                    <div style="display:flex;gap:12px;justify-content:center;">
                        <button id="timeout-retry" style="
                            padding:10px 24px;border-radius:8px;border:none;
                            background:var(--color-bg-3,#2a2a3e);
                            color:var(--color-text-1,#fff);
                            cursor:pointer;font-size:0.95rem;
                        ">Try Again</button>
                        <button id="timeout-continue" style="
                            padding:10px 24px;border-radius:8px;border:none;
                            background:var(--color-accent,#7c6af7);
                            color:#fff;cursor:pointer;font-size:0.95rem;
                            font-weight:600;
                        ">Continue Anyway</button>
                    </div>
                </div>`;

            document.body.appendChild(overlay);

            overlay.querySelector('#timeout-continue').addEventListener('click', () => {
                overlay.remove();
                resolve('continue');
            });
            overlay.querySelector('#timeout-retry').addEventListener('click', () => {
                overlay.remove();
                resolve('retry');
            });
        });
    }

    // Translate button in modal: calls /transcribe with target_lang
    startupTranslateBtn.addEventListener('click', async () => {
        const lang = startupTargetLang.value || null;
        if (!currentVideoFilename) {
            startupProgress.textContent = 'No uploaded file.';
            return;
        }

        startupTranslateBtn.disabled = true;
        setJobInProgress(true);

        // Close modal and show floating progress overlay
        startupModal.style.display = 'none';
        if (startupSpinner) startupSpinner.classList.add('hidden');
        showProgress('Transcribing audio\u2026', 5);

        try {
            const res = await fetch('/transcribe', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: currentVideoFilename, target_lang: lang }),
            });
            const data = await res.json();

            if (data.segments) {
                subtitles = data.segments;
                let noInternet = false;

                showProgress('Processing subtitles\u2026', 35);

                if (lang && subtitles.length > 0) {
                    // Case 1: backend detected no internet during inline translation
                    if (data.no_internet) {
                        const r = await retryTranslateSegments(subtitles, lang);
                        if (r.timedOut) {
                            hideProgress();
                            const failedCount = r.subtitles.filter(
                                s => s.original_text != null && s.text === s.original_text
                            ).length;
                            const totalCount = r.subtitles.length;
                            const translatedCount = totalCount - failedCount;
                            const userChoice = await showTranslationTimeoutModal(
                                { translatedCount, failedCount, totalCount }
                            );
                            if (userChoice === 'retry') {
                                startupTranslateBtn.disabled = false;
                                startupTranslateBtn.textContent = 'Translate & Start';
                                setJobInProgress(false);
                                return;
                            }
                            // 'continue' — fall through with partial results
                        }
                        subtitles = r.subtitles;
                        noInternet = r.noInternet;
                    } else {
                        // Case 2: any segment still has text === original_text
                        const needsTranslation = subtitles.some(
                            s => s.original_text != null && s.text === s.original_text
                        );
                        if (needsTranslation) {
                            const r = await retryTranslateSegments(subtitles, lang);
                            if (r.timedOut) {
                                hideProgress();
                                const failedCount = r.subtitles.filter(
                                    s => s.original_text != null && s.text === s.original_text
                                ).length;
                                const totalCount = r.subtitles.length;
                                const translatedCount = totalCount - failedCount;
                                const userChoice = await showTranslationTimeoutModal(
                                    { translatedCount, failedCount, totalCount }
                                );
                                if (userChoice === 'retry') {
                                    startupTranslateBtn.disabled = false;
                                    startupTranslateBtn.textContent = 'Translate & Start';
                                    setJobInProgress(false);
                                    return;
                                }
                                // 'continue' — fall through with partial results
                            }
                            subtitles = r.subtitles;
                            noInternet = r.noInternet;
                        }
                    }
                }

                showProgress('Done!', 100);
                renderSubtitleList();

                // If translation was blocked by no internet, persist the warning
                if (noInternet && exportText) {
                    exportText.textContent =
                        '\u26a0\ufe0f No internet connection \u2014 subtitles show the original language.';
                }

                // SRT/VTT export is available immediately — no burn needed
                if (currentJobId) {
                    _showExportActions(
                        `/api/v1/export/srt/${currentJobId}`,
                        `/api/v1/export/vtt/${currentJobId}`
                    );
                }

                if (mainNewJobBtn) mainNewJobBtn.classList.remove('hidden');
                try { video.currentTime = 0; video.play(); } catch (e) {}
                setJobInProgress(false);
                setTimeout(hideProgress, 1200);
            } else {
                hideProgress();
                // Re-open modal with the error so user can retry
                startupModal.style.display = 'flex';
                startupProgress.textContent = 'Transcription failed: ' + (data.error || 'Unknown error');
                startupTranslateBtn.disabled = false;
                setJobInProgress(false);
            }
        } catch (err) {
            console.error(err);
            hideProgress();
            startupModal.style.display = 'flex';
            startupProgress.textContent = 'Network error during transcription.';
            startupTranslateBtn.disabled = false;
            setJobInProgress(false);
        }
    });

    // Start New Job handlers (cancel current upload / reset to initial upload state)
    function resetForNewJob(openModal = true) {
        // Clear current data
        currentVideoFilename = null;
        currentJobId = null;
        subtitles = [];
        undoStack.length = 0;
        redoStack.length = 0;
        updateUndoUI();
        renderSubtitleList();
        try { video.pause(); } catch(e){}
        video.src = '';

        // Clear overlay and last rendered text
        if (subtitleOverlay) subtitleOverlay.innerHTML = '';
        lastRenderedText = null;

        // Reset modal inputs and buttons
        if (startupUploadInput) startupUploadInput.value = '';
        if (startupTargetLang) startupTargetLang.value = '';
        const uploadStatus = document.getElementById('startup-upload-status');
        if (uploadStatus) uploadStatus.textContent = 'No file chosen';
        if (startupProgress) startupProgress.textContent = '';

        // Clear editor inputs
        if (startTimeInput) startTimeInput.value = '';
        if (endTimeInput) endTimeInput.value = '';
        if (subTextInput) subTextInput.value = '';

        // Clear export UI
        if (exportText) exportText.textContent = '';
        if (exportSpinner) exportSpinner.classList.add('hidden');
        if (btnBurn) btnBurn.disabled = true;

        if (startupUploadBtn) {
            startupUploadBtn.classList.remove('hidden');
            startupUploadBtn.disabled = true;
        }
        if (startupUploadInput) startupUploadInput.disabled = false;
        if (startupTranslateBtn) {
            startupTranslateBtn.classList.add('hidden');
            startupTranslateBtn.disabled = true;
        }
        if (startupNewJobBtn) startupNewJobBtn.classList.add('hidden');
        if (mainNewJobBtn) mainNewJobBtn.classList.add('hidden');

        // Show modal if requested
        if (startupModal) startupModal.style.display = openModal ? 'flex' : 'none';
    }

    if (startupNewJobBtn) {
        startupNewJobBtn.addEventListener('click', () => {
            if (jobInProgress) {
                if (startupProgress) startupProgress.textContent = 'Cannot start a new job while transcription is running.';
                return;
            }
            // Ask server to clear any stored uploads/exports for a fresh session
            try { fetch('/clear_session', { method: 'POST' }); } catch(e){}
            window.location.href = '/';
        });
    }

    if (mainNewJobBtn) {
        mainNewJobBtn.addEventListener('click', () => {
            if (jobInProgress) {
                if (startupProgress) startupProgress.textContent = 'Cannot start a new job while transcription is running.';
                return;
            }
            // Return to landing page to start a fresh upload
            try { fetch('/clear_session', { method: 'POST' }); } catch(e){}
            window.location.href = '/';
        });
    }

    // Styles panel toggle handlers
    if (openStylesBtn && stylesPanel) {
        openStylesBtn.addEventListener('click', () => {
            const isHidden = stylesPanel.classList.contains('hidden');
            if (isHidden) {
                stylesPanel.classList.remove('hidden');
                stylesPanel.setAttribute('aria-hidden', 'false');
            } else {
                stylesPanel.classList.add('hidden');
                stylesPanel.setAttribute('aria-hidden', 'true');
            }
        });
    }

    if (closeStylesBtn && stylesPanel) {
        closeStylesBtn.addEventListener('click', () => {
            stylesPanel.classList.add('hidden');
            stylesPanel.setAttribute('aria-hidden', 'true');
        });
    }

    // Close styles panel with Escape key; also Ctrl+Z/Shift+Z/S for undo/redo/save
    // Space = play/pause, ← → = ±5s seek
    document.addEventListener('keydown', (e) => {
        const tag = (document.activeElement || {}).tagName || '';
        const inInput = ['INPUT','TEXTAREA','SELECT'].includes(tag);

        if (e.key === 'Escape' && stylesPanel && !stylesPanel.classList.contains('hidden')) {
            stylesPanel.classList.add('hidden');
            stylesPanel.setAttribute('aria-hidden', 'true');
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
        if ((e.ctrlKey || e.metaKey) && e.key === 'z' &&  e.shiftKey) { e.preventDefault(); redo(); }
        if ((e.ctrlKey || e.metaKey) && e.key === 's')               { e.preventDefault(); saveSegments(); }

        // Media controls — only when not typing in an input
        if (!inInput) {
            if (e.key === ' ') {
                e.preventDefault();
                video.paused ? video.play() : video.pause();
            }
            if (e.key === 'ArrowLeft')  { e.preventDefault(); video.currentTime = Math.max(0, video.currentTime - 5); }
            if (e.key === 'ArrowRight') { e.preventDefault(); video.currentTime = Math.min(video.duration || Infinity, video.currentTime + 5); }
        }
    });

    // --- Drag-and-drop on the upload zone ---
    const uploadZone = document.getElementById('upload-zone');
    if (uploadZone) {
        ['dragenter', 'dragover'].forEach(ev => {
            uploadZone.addEventListener(ev, e => {
                e.preventDefault(); e.stopPropagation();
                uploadZone.classList.add('drag-over');
            });
        });
        ['dragleave', 'drop'].forEach(ev => {
            uploadZone.addEventListener(ev, e => {
                e.preventDefault(); e.stopPropagation();
                uploadZone.classList.remove('drag-over');
                if (ev === 'drop') {
                    const file = e.dataTransfer.files[0];
                    if (file) {
                        startupUploadInput.files = e.dataTransfer.files;
                        startupUploadInput.dispatchEvent(new Event('change'));
                    }
                }
            });
        });
    }

    // --- Segment search ---
    const segmentSearch = document.getElementById('segment-search');
    if (segmentSearch) {
        segmentSearch.addEventListener('input', () => {
            const q = segmentSearch.value.trim().toLowerCase();
            document.querySelectorAll('.subtitle-item').forEach(el => {
                const text = el.textContent.toLowerCase();
                el.style.display = (!q || text.includes(q)) ? '' : 'none';
            });
        });
    }

    // --- Make Styles Panel Draggable ---
    const stylesHeader = stylesPanel ? stylesPanel.querySelector('.floating-header') : null;
    if (stylesPanel && stylesHeader) {
        let isDragging = false;
        let dragOffsetX = 0;
        let dragOffsetY = 0;

        // Prevent native touch scrolling while dragging
        stylesHeader.style.touchAction = 'none';

        stylesHeader.addEventListener('pointerdown', (ev) => {
            // don't start drag when clicking interactive elements (close button)
            if (ev.target.closest('button')) return;
            if (stylesPanel.classList.contains('hidden')) return;

            isDragging = true;
            try { stylesHeader.setPointerCapture(ev.pointerId); } catch (err) {}

            const rect = stylesPanel.getBoundingClientRect();
            // switch from right-based positioning to left/top so it can move freely
            stylesPanel.style.left = rect.left + 'px';
            stylesPanel.style.top = rect.top + 'px';
            stylesPanel.style.right = 'auto';

            dragOffsetX = ev.clientX - rect.left;
            dragOffsetY = ev.clientY - rect.top;
            stylesPanel.classList.add('dragging');
            document.body.style.userSelect = 'none';
        });

        document.addEventListener('pointermove', (ev) => {
            if (!isDragging) return;
            ev.preventDefault();
            const panelRect = stylesPanel.getBoundingClientRect();
            let newLeft = ev.clientX - dragOffsetX;
            let newTop = ev.clientY - dragOffsetY;

            // Clamp to viewport
            const maxLeft = window.innerWidth - panelRect.width;
            const maxTop = window.innerHeight - panelRect.height;
            newLeft = Math.max(0, Math.min(newLeft, Math.max(0, maxLeft)));
            newTop = Math.max(0, Math.min(newTop, Math.max(0, maxTop)));

            stylesPanel.style.left = newLeft + 'px';
            stylesPanel.style.top = newTop + 'px';
        });

        const endDrag = (ev) => {
            if (!isDragging) return;
            isDragging = false;
            try { if (ev && ev.pointerId) stylesHeader.releasePointerCapture(ev.pointerId); } catch (err) {}
            stylesPanel.classList.remove('dragging');
            document.body.style.userSelect = '';
        };

        document.addEventListener('pointerup', endDrag);
        document.addEventListener('pointercancel', endDrag);
    }

    // Style Updaters - Live Preview
    function updateOverlayStyles() {
        // Font
        const inputFs = parseFloat(fontSize.value) || 24;
        const previewFs = Math.max(1, Math.round(inputFs * PREVIEW_SCALE));
        subtitleOverlay.style.fontFamily = fontFamily.value;
        subtitleOverlay.style.fontSize = previewFs + 'px';
        subtitleOverlay.style.color = fontColor.value;

        // Position
        const pos = position.value;
        subtitleOverlay.style.justifyContent = 'flex-end'; // Default bottom
        subtitleOverlay.style.paddingBottom = '50px';
        subtitleOverlay.style.paddingTop = '0';
        
        if (pos === 'top') {
            subtitleOverlay.style.justifyContent = 'flex-start';
            subtitleOverlay.style.paddingTop = '50px';
            subtitleOverlay.style.paddingBottom = '0';
        } else if (pos === 'center') {
            subtitleOverlay.style.justifyContent = 'center';
            subtitleOverlay.style.paddingBottom = '0';
        }
        
    }
    
    [fontFamily, fontSize, fontColor, position].forEach(el => {
        el.addEventListener('input', updateOverlayStyles);
    });

    // Subtitle Management
    function renderSubtitleList() {
        subtitleList.innerHTML = '';
        if (!subtitles || subtitles.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.textContent = 'No subtitles added yet.';
            subtitleList.appendChild(empty);
            if (btnBurn) btnBurn.disabled = true;
            return;
        }

        subtitles.sort((a, b) => a.start - b.start).forEach(sub => {
            const di = document.createElement('div');
            const hasArabic = /[\u0600-\u06FF]/.test(sub.text);
            di.className = 'subtitle-item auto-dir';
            if (editingId === sub.id) di.classList.add('active');
            if (hasArabic) {
                di.classList.add('has-arabic');
                di.style.fontFamily = "var(--font-arabic, 'Cairo', sans-serif)";
            }
            
            di.innerHTML = `
                <div class="meta">
                    <span>${formatTime(sub.start)} - ${formatTime(sub.end)}</span>
                    <span onclick="deleteSubtitle(${sub.id}, event)" style="color:red; cursor:pointer;">✖</span>
                </div>
                <div>${sub.text}</div>
            `;
            di.addEventListener('click', () => editSubtitle(sub));
            subtitleList.appendChild(di);
        });
        if (btnBurn) btnBurn.disabled = false;

        // Update segment count + word count badges
        const segCount  = document.getElementById('segment-count');
        const wordCount = document.getElementById('word-count');
        if (segCount)  segCount.textContent  = subtitles.length + ' segments';
        if (wordCount) wordCount.textContent =
            subtitles.reduce((n,s) => n + (s.text ? s.text.trim().split(/\s+/).length : 0), 0)
            + ' words';

        // Re-apply search filter after re-render
        const segSearch = document.getElementById('segment-search');
        if (segSearch && segSearch.value.trim()) {
            const q = segSearch.value.trim().toLowerCase();
            document.querySelectorAll('.subtitle-item').forEach(el => {
                el.style.display = el.textContent.toLowerCase().includes(q) ? '' : 'none';
            });
        }
    }

    window.deleteSubtitle = (id, e) => {
        e.stopPropagation();
        snapshot();
        subtitles = subtitles.filter(s => s.id !== id);
        renderSubtitleList();
        renderOverlay(); // clear if needed
        updateUndoUI();
    };

    function editSubtitle(sub) {
        editingId = sub.id;
        startTimeInput.value = sub.start;
        endTimeInput.value = sub.end;
        subTextInput.value = sub.text;
        
        btnAdd.classList.add('hidden');
        btnUpdate.classList.remove('hidden');
        if (btnSplit) btnSplit.classList.remove('hidden');
        
        // Pause first so the video stays on the clicked segment's frame,
        // then clear the text cache so the overlay re-renders immediately.
        video.pause();
        lastRenderedText = null;
        video.currentTime = parseFloat(sub.start);
        renderSubtitleList();
    }

    btnAdd.addEventListener('click', () => {
        const start = parseFloat(startTimeInput.value);
        const end = parseFloat(endTimeInput.value);
        const text = subTextInput.value;

        if (isNaN(start) || isNaN(end) || !text) return;

        snapshot();
        const newSub = { id: Date.now(), start, end, text };
        subtitles.push(newSub);
        
        // Reset inputs
        subTextInput.value = '';
        renderSubtitleList();
        updateUndoUI();
    });

    btnUpdate.addEventListener('click', () => {
        const start = parseFloat(startTimeInput.value);
        const end = parseFloat(endTimeInput.value);
        const text = subTextInput.value;

        if (isNaN(start) || isNaN(end) || !text) return;

        const idx = subtitles.findIndex(s => s.id === editingId);
        if (idx !== -1) {
            snapshot();
            subtitles[idx] = { ...subtitles[idx], start, end, text };
        }

        editingId = null;
        btnAdd.classList.remove('hidden');
        btnUpdate.classList.add('hidden');
        if (btnSplit) btnSplit.classList.add('hidden');
        subTextInput.value = '';
        renderSubtitleList();
        updateUndoUI();
    });

    // Timing Buttons
    btnSetStart.addEventListener('click', () => {
        startTimeInput.value = video.currentTime.toFixed(2);
    });
    
    btnSetEnd.addEventListener('click', () => {
        endTimeInput.value = video.currentTime.toFixed(2);
    });

    // Split Segment button
    if (btnSplit) {
        btnSplit.addEventListener('click', () => {
            if (editingId == null) return;
            const idx = subtitles.findIndex(s => s.id === editingId);
            if (idx === -1) return;

            const seg = subtitles[idx];
            const text = subTextInput.value;
            const cursor = subTextInput.selectionStart;

            // Determine split point in the text
            let textA, textB;
            if (cursor > 0 && cursor < text.length) {
                textA = text.substring(0, cursor).trim();
                textB = text.substring(cursor).trim();
            } else {
                // No cursor selection — split at midpoint of words
                const words = text.trim().split(/\s+/);
                const mid = Math.max(1, Math.ceil(words.length / 2));
                textA = words.slice(0, mid).join(' ');
                textB = words.slice(mid).join(' ');
            }

            if (!textA || !textB) {
                showToast('Cannot split: one half is empty. Place cursor between words.', 'warning');
                return;
            }

            const midTime = parseFloat(((parseFloat(seg.start) + parseFloat(seg.end)) / 2).toFixed(3));
            const newSeg1 = { id: Date.now(),     start: seg.start, end: midTime,   text: textA };
            const newSeg2 = { id: Date.now() + 1, start: midTime,   end: seg.end,   text: textB };

            snapshot();
            subtitles.splice(idx, 1, newSeg1, newSeg2);

            // Exit edit mode
            editingId = null;
            btnAdd.classList.remove('hidden');
            btnUpdate.classList.add('hidden');
            btnSplit.classList.add('hidden');
            subTextInput.value = '';
            startTimeInput.value = '';
            endTimeInput.value = '';

            renderSubtitleList();
            updateUndoUI();
            showToast('Segment split into two.', 'success');
        });
    }

    // Editor-level translation controls removed; translation happens from startup modal


    // --- Core Loop : Render Overlay ---
    // lastRenderedText is declared earlier so we can reset it from other functions

    function renderOverlay() {
        const now = video.currentTime;
        // Find visible subtitle
        const activeSub = subtitles.find(s => now >= s.start && now <= s.end);

        if (activeSub) {
            if (lastRenderedText !== activeSub.text) {
                // New subtitle appearing
                subtitleOverlay.innerHTML = `<div class="subtitle-line ${getAnimClass()}">${activeSub.text}</div>`;
                lastRenderedText = activeSub.text;
            }
        } else {
            subtitleOverlay.innerHTML = '';
            lastRenderedText = null;
        }

        currentTimeDisplay.innerText = formatTime(now);
        if(!isNaN(video.duration)) durationDisplay.innerText = formatTime(video.duration);
        
        requestAnimationFrame(renderOverlay);
    }
    
    function getAnimClass() {
        const type = animationType.value;
        if (type === 'fade') return 'anim-fade';
        if (type === 'slide-up') return 'anim-slide-up';
        if (type === 'scale') return 'anim-scale';
        return '';
    }

    renderOverlay(); // Start loop


    // --- Burn/Export ---
    btnBurn.addEventListener('click', async () => {
        if (!currentVideoFilename) {
            alert("Please upload a video first.");
            return;
        }
        if (subtitles.length === 0) {
            alert("No subtitles to burn.");
            return;
        }

        // Compute scale between intrinsic video height and displayed (preview) height
        const displayHeight = video.clientHeight || video.getBoundingClientRect().height || video.videoHeight || 720;
        const intrinsicHeight = video.videoHeight || displayHeight;
        const scale = (intrinsicHeight && displayHeight) ? (intrinsicHeight / displayHeight) : 1;

        // Read all style fields from the shared style getter (same source as canvas preview)
        const uiStyle = (typeof window.getSubtitleStyle === 'function')
            ? window.getSubtitleStyle()
            : {};

        // Use canvas dimensions as the ASS PlayRes reference so libass does the
        // scaling from display coords to native video resolution automatically,
        // eliminating any manual-scaling mismatch.
        const canvasEl = document.getElementById('subtitle-canvas');
        const playResW = (canvasEl && canvasEl.width  > 0) ? canvasEl.width  : video.videoWidth;
        const playResH = (canvasEl && canvasEl.height > 0) ? canvasEl.height : video.videoHeight;

        // Font size: use the raw CSS-pixel value shown in the preview — NO manual scaling.
        // libass auto-scales from PlayRes coords to native video resolution.
        const inputFontSize = uiStyle.size || parseInt(fontSize.value) || 24;

        const payload = {
            filename: currentVideoFilename,
            subtitles: subtitles,
            videoWidth:    video.videoWidth,
            videoHeight:   video.videoHeight,
            playResWidth:  playResW,
            playResHeight: playResH,
            styles: {
                fontFamily:  uiStyle.font     || fontFamily.value,
                fontSize:    inputFontSize,
                color:       uiStyle.color    || fontColor.value,
                position:    uiStyle.position || position.value,
                animation:   animationType.value,
                // Stroke / outline
                strokeColor:  uiStyle.stroke      || '#000000',
                strokeWidth:  uiStyle.strokeWidth  != null ? uiStyle.strokeWidth : 1.5,
                // Background box
                bg:           uiStyle.bg           || false,
                bgOpacity:    uiStyle.bgOpacity     != null ? uiStyle.bgOpacity : 70,
                bgColor:      uiStyle.bgColor       || '#000000',
            }
        };

        // Pause video if playing
        const videoPlayer = document.getElementById('main-video');
        if (videoPlayer && !videoPlayer.paused) {
            videoPlayer.pause();
        }

        // Close style panel when burn starts
        if (stylesPanel && !stylesPanel.classList.contains('hidden')) {
            stylesPanel.classList.add('hidden');
            stylesPanel.setAttribute('aria-hidden', 'true');
        }

        // Show floating progress overlay and disable burn button while processing
        showProgress('Saving segments\u2026', 5);
        btnBurn.disabled = true;

        if (currentJobId) {
            // --- Polling-based burn via /api/v1/burn ---
            // Auto-save current segments first
            try { await saveSegments(); } catch (_) {}

            let burnPollTimer = null;
            let fakePct = 15; // incremented while poll returns 'burning'

            function stopBurnPoll() {
                if (burnPollTimer) { clearInterval(burnPollTimer); burnPollTimer = null; }
            }

            try {
                showProgress('Sending to server\u2026', 10);
                const res = await fetch('/api/v1/burn', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ job_id: currentJobId, style: payload.styles,
                        playResWidth: payload.playResWidth, playResHeight: payload.playResHeight })
                });
                if (!res.ok) {
                    const errBody = await res.json().catch(() => ({}));
                    throw new Error(errBody.error || 'Burn request failed: ' + res.status);
                }
                const init = await res.json();
                if (!init.ok) throw new Error(init.error || 'Burn failed to start');

                showProgress('Burning subtitles into video\u2026', 20);

                // Poll job status every 2 s until burned or failed
                burnPollTimer = setInterval(async () => {
                    try {
                        const sr = await fetch(`/api/v1/job/${currentJobId}/status`);
                        const sd = await sr.json();

                        if (sd.status === 'burned') {
                            stopBurnPoll();
                            showProgress('Complete!', 100);
                            btnBurn.disabled = false;
                            showToast('Burn complete! Video ready to download.', 'success');
                            _showExportActions(sd.srt_url, sd.vtt_url);
                            _triggerDownload(sd.download_url, 'output.mp4');
                            setTimeout(hideProgress, 1500);
                        } else if (sd.status === 'burn_failed') {
                            stopBurnPoll();
                            hideProgress();
                            btnBurn.disabled = false;
                            const msg = sd.error || 'Burn failed.';
                            showToast(msg, 'error');
                        } else if (sd.status === 'burning') {
                            fakePct = Math.min(90, fakePct + 5);
                            showProgress('Burning subtitles into video\u2026', fakePct);
                        }
                    } catch (_) { /* network hiccup — keep polling */ }
                }, 2000);

            } catch (err) {
                stopBurnPoll();
                hideProgress();
                btnBurn.disabled = false;
                showToast('Burn error: ' + err.message, 'error');
            }

        } else {
            // --- Legacy fallback: synchronous /burn endpoint ---
            try {
                showProgress('Burning subtitles into video\u2026', 30);
                const res = await fetch('/burn', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();

                if (data.error) {
                    showToast('Error: ' + (data.error || 'Unknown'), 'error');
                    hideProgress();
                } else if (data.download_url) {
                    showProgress('Complete!', 100);
                    showToast('Burn complete! Downloading\u2026', 'success');
                    _triggerDownload(data.download_url, data.filename || 'output.mp4');
                    _showExportActions(data.srt_url || null, data.vtt_url || null);
                    setTimeout(hideProgress, 1500);
                } else {
                    hideProgress();
                }
            } catch (err) {
                console.error(err);
                showToast('Network error during export.', 'error');
                hideProgress();
            } finally {
                btnBurn.disabled = false;
            }
        }
    });

    // Helper: trigger a file download
    function _triggerDownload(url, filename) {
        const a = document.createElement('a');
        a.href = url;
        a.download = filename || url.split('/').pop() || 'download';
        document.body.appendChild(a);
        a.click();
        a.remove();
    }

    // Helper: reveal SRT/VTT export buttons (now inline in the controls bar)
    function _showExportActions(srtUrl, vttUrl) {
        const panel = document.getElementById('export-actions');
        const btnSrt = document.getElementById('btn-download-srt');
        const btnVtt = document.getElementById('btn-download-vtt');
        if (btnSrt && srtUrl) btnSrt.href = srtUrl;
        if (btnVtt && vttUrl) btnVtt.href = vttUrl;
        if (panel) panel.classList.remove('hidden');
    }

    // Clear only the UI parts that should be reset when a new upload starts
    function clearPreviousUI() {
        subtitles = [];
        undoStack.length = 0;
        redoStack.length = 0;
        updateUndoUI();
        renderSubtitleList();
        if (subtitleOverlay) subtitleOverlay.innerHTML = '';
        lastRenderedText = null;
        if (startTimeInput) startTimeInput.value = '';
        if (endTimeInput) endTimeInput.value = '';
        if (subTextInput) subTextInput.value = '';
        if (exportText) exportText.textContent = '';
        if (exportSpinner) exportSpinner.classList.add('hidden');
        if (btnBurn) btnBurn.disabled = true;
        // Reset export actions panel for the new job
        const exportPanel = document.getElementById('export-actions');
        if (exportPanel) exportPanel.classList.add('hidden');
    }

    // Attempt to notify server to clear any stored files when user closes the tab
    window.addEventListener('beforeunload', () => {
        try {
            const payload = JSON.stringify({ job_id: currentJobId });
            if (currentJobId && navigator.sendBeacon) {
                navigator.sendBeacon('/api/v1/clear_session', new Blob([payload], {type:'application/json'}));
            } else if (!currentJobId && navigator.sendBeacon) {
                navigator.sendBeacon('/clear_session');
            }
        } catch (e) { /* ignore */ }
    });

    // Expose activeJobId for beforeunload beacon
    Object.defineProperty(window, '__activeJobId', { get: () => currentJobId });

    // Helper
    function formatTime(seconds) {
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        const ms = Math.floor((seconds % 1) * 10);
        return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${ms}`;
    }
    
    // Init styles
    updateOverlayStyles();
    // Ensure initial subtitle UI state is correct
    renderSubtitleList();
    // Expose undo/redo/saveSegments globally (called from HTML button onclick)
    window.undo = undo;
    window.redo = redo;
    window.saveSegments = saveSegments;

    // ── Load job from URL ?job_id= (from My Jobs page OR landing page upload) ──
    const _urlParams = new URLSearchParams(window.location.search);
    const _urlJobId  = _urlParams.get('job_id');
    const _urlLang   = _urlParams.get('lang') || null;
    if (_urlJobId) {
        (async () => {
            try {
                showProgress('Loading job\u2026', 10);
                const res = await fetch(`/api/v1/job/${_urlJobId}`);
                if (!res.ok) throw new Error('Job not found');
                const jobData = await res.json();

                // Always set core state and hide the startup modal
                currentJobId = jobData.job_id;
                currentVideoFilename = jobData.input_filename;
                if (jobData.video_url) video.src = jobData.video_url;
                if (startupModal) startupModal.style.display = 'none';
                if (mainNewJobBtn) mainNewJobBtn.classList.remove('hidden');

                // Clean ?job_id and ?lang from the address bar
                history.replaceState({}, '', window.location.pathname);

                if (!jobData.segments || jobData.segments.length === 0) {
                    // Fresh job (just uploaded from landing page).
                    // If a target language was passed, auto-start transcription.
                    if (_urlLang && startupTargetLang) startupTargetLang.value = _urlLang;
                    hideProgress();
                    if (startupTranslateBtn) {
                        startupTranslateBtn.disabled = false;
                        startupTranslateBtn.click();
                    }
                    return;
                }

                // Existing job with segments — restore the editor state
                subtitles = jobData.segments;
                showProgress('Restoring job\u2026', 60);

                renderSubtitleList();
                _showExportActions(jobData.srt_url, jobData.vtt_url);

                if (jobData.download_url) {
                    const btnSrt = document.getElementById('btn-download-srt');
                    if (btnSrt) btnSrt.href = jobData.srt_url;
                }

                if (btnBurn) btnBurn.disabled = false;

                showProgress('Job loaded!', 100);
                setTimeout(hideProgress, 1000);

                try { video.play(); } catch (e) {}
            } catch (err) {
                console.error('Failed to load job from URL:', err);
                hideProgress();
                showToast('Could not load job — please try again.', 'error');
            }
        })();
    }
});

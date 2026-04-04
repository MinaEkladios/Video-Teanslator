import json as _json
import os
import queue
import threading
import time as _time

from flask import Blueprint, jsonify, request, Response, stream_with_context, current_app, send_file
from flask_login import login_required, current_user

from extensions import db, limiter, talisman
from models import Job, UploadStats

api_v1 = Blueprint('api_v1', __name__)

# ── Per-job SSE queues ────────────────────────────────────────────────────────
# Maps job_id (str) → queue.Queue of event dicts.
# Event dict shapes:
#   { "type": "progress", "value": 0-100, "message": "..." }
#   { "type": "segment",  "data": {...} }
#   { "type": "warning",  "message": "..." }
#   { "type": "done" }
#   { "type": "error",    "message": "..." }
_job_queues: dict = {}
_job_queues_lock = threading.Lock()


def _get_or_create_queue(job_id: str) -> queue.Queue:
    with _job_queues_lock:
        if job_id not in _job_queues:
            _job_queues[job_id] = queue.Queue()
        return _job_queues[job_id]


def push_event(job_id: str, data: dict) -> None:
    """Push an SSE event to a job's stream. Safe to call from worker threads."""
    _get_or_create_queue(job_id).put(data)


def _cleanup_queue(job_id: str) -> None:
    with _job_queues_lock:
        _job_queues.pop(job_id, None)


# ── Existing endpoints ────────────────────────────────────────────────────────

@api_v1.route('/status')
@login_required
def status():
    return jsonify({'ok': True, 'version': 'v1'})


@api_v1.route('/me')
@login_required
def me():
    return jsonify({
        'ok': True,
        'user': {
            'id': current_user.id,
            'email': current_user.email,
            'display_name': current_user.display_name,
        },
    })


# ── SSE stream ────────────────────────────────────────────────────────────────

@api_v1.route('/stream/<job_id>')
@talisman(content_security_policy=False)
def stream(job_id):
    """
    GET /api/v1/stream/<job_id>
    Server-Sent Events stream. Subscribe after calling /api/v1/transcribe.
    Closes automatically on type=done or type=error.
    """
    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404

    q = _get_or_create_queue(job_id)

    def generate():
        try:
            while True:
                try:
                    event = q.get(timeout=25)
                    yield f"data: {_json.dumps(event)}\n\n"
                    if event.get('type') in ('done', 'error'):
                        break
                except queue.Empty:
                    # Keepalive comment prevents proxy timeouts
                    yield ": keepalive\n\n"
                    # If the worker died without pushing done, detect via DB
                    try:
                        fresh = db.session.get(Job, job_id)
                        if fresh and fresh.status in ('done', 'failed', 'cancelled'):
                            etype = 'done' if fresh.status == 'done' else 'error'
                            yield f"data: {_json.dumps({'type': etype, 'message': fresh.error_msg or ''})}\n\n"
                            break
                    except Exception:
                        pass
        finally:
            _cleanup_queue(job_id)

    return Response(
        stream_with_context(generate()),
        content_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── Async transcription ───────────────────────────────────────────────────────

@api_v1.route('/transcribe', methods=['POST'])
@login_required
@limiter.limit('5/minute')
def start_transcribe():
    """
    POST /api/v1/transcribe
    Body: { job_id, target_lang }
    Returns immediately; processing runs in a background thread.
    Subscribe to GET /api/v1/stream/<job_id> for real-time progress.

    # TODO: replace threading with Celery for production
    """
    data = request.get_json(silent=True) or {}
    job_id = data.get('job_id')
    target_lang = data.get('target_lang') or None

    if not job_id:
        return jsonify({'ok': False, 'error': 'job_id required'}), 400

    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404

    if job.status not in ('queued',):
        return jsonify({'ok': False, 'error': f'Job already in state: {job.status}'}), 409

    job.status = 'running'
    job.target_lang = target_lang
    db.session.commit()

    def run_pipeline(flask_app, jid, tlang):
        # TODO: replace threading with Celery for production
        from video_processor import transcriber, NoInternetError  # noqa: F401
        with flask_app.app_context():
            j = db.session.get(Job, jid)
            if not j:
                return
            try:
                push_event(jid, {'type': 'progress', 'value': 5, 'message': 'Starting…'})
                result = transcriber.transcribe_and_translate(j.input_path, translate_to=tlang)
                segments = result['segments']
                no_internet = result.get('no_internet', False)

                if no_internet:
                    push_event(jid, {'type': 'warning',
                                     'message': 'No internet — subtitles shown in original language.'})

                push_event(jid, {'type': 'progress', 'value': 90, 'message': 'Saving segments…'})

                j.segments = _json.dumps(segments)
                j.status = 'done'
                j.translation_failed = no_internet
                db.session.commit()

                UploadStats.log_upload(j)

                for seg in segments:
                    push_event(jid, {'type': 'segment', 'data': seg})
                push_event(jid, {'type': 'done'})

            except Exception as exc:
                try:
                    j.status = 'failed'
                    j.error_msg = str(exc)
                    db.session.commit()
                except Exception:
                    pass
                push_event(jid, {'type': 'error', 'message': str(exc)})

    t = threading.Thread(
        target=run_pipeline,
        args=(current_app._get_current_object(), job_id, target_lang),
        daemon=True,
    )
    t.start()
    return jsonify({'ok': True, 'job_id': job_id})


# ── Cancel job ────────────────────────────────────────────────────────────────

@api_v1.route('/cancel/<job_id>', methods=['POST'])
def cancel_job(job_id):
    """POST /api/v1/cancel/<job_id> — mark a job as cancelled."""
    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    if job.status in ('done', 'failed', 'cancelled'):
        return jsonify({'ok': False, 'error': f'Job already {job.status}'}), 409
    job.status = 'cancelled'
    db.session.commit()
    push_event(job_id, {'type': 'error', 'message': 'Cancelled by user.'})
    return jsonify({'ok': True})


# ── Job status poll ───────────────────────────────────────────────────────────

@api_v1.route('/job/<job_id>/status')
def job_status(job_id):
    """GET /api/v1/job/<job_id>/status — lightweight poll for job state."""
    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'status':       job.status,
        'download_url': f'/api/v1/download/{job_id}' if job.burned_path else None,
        'srt_url':      f'/api/v1/export/srt/{job_id}',
        'vtt_url':      f'/api/v1/export/vtt/{job_id}',
        'error':        job.error_msg,
    })


@api_v1.route('/job/<job_id>')
def get_job(job_id):
    """GET /api/v1/job/<job_id> — full job data including segments (for restoring from My Jobs)."""
    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'error': 'not found'}), 404
    if not _soft_owns(job):
        return jsonify({'error': 'Forbidden'}), 403
    segments = _json.loads(job.segments or '[]')
    return jsonify({
        'job_id':         job.id,
        'status':         job.status,
        'segments':       segments,
        'input_filename': job.input_filename,
        'srt_url':        f'/api/v1/export/srt/{job_id}',
        'vtt_url':        f'/api/v1/export/vtt/{job_id}',
        'download_url':   f'/api/v1/download/{job_id}' if job.burned_path else None,
        'video_url':      f'/uploads/{job.input_filename}' if job.input_filename else None,
    })


# ── Persist segment edits ─────────────────────────────────────────────────────

@api_v1.route('/segments/save', methods=['POST'])
def save_segments():
    """POST /api/v1/segments/save — persist user-edited segments to the Job record."""
    data = request.get_json(silent=True) or {}
    job_id  = data.get('job_id')
    segments = data.get('segments')

    if not job_id or segments is None:
        return jsonify({'ok': False, 'error': 'job_id and segments required'}), 400

    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404

    job.segments = _json.dumps(segments)
    db.session.commit()
    return jsonify({'ok': True, 'saved': len(segments)})


# ── SRT / VTT helpers ─────────────────────────────────────────────────────────

def _fmt_srt_time(s: float) -> str:
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    ms = int((s % 1) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def _fmt_vtt_time(s: float) -> str:
    h, rem = divmod(int(s), 3600)
    m, sec = divmod(rem, 60)
    ms = int((s % 1) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"


def _make_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_fmt_srt_time(float(seg['start']))} --> {_fmt_srt_time(float(seg['end']))}")
        lines.append(seg.get('text', ''))
        lines.append('')
    return '\n'.join(lines)


def _make_vtt(segments) -> str:
    lines = ['WEBVTT', '']
    for seg in segments:
        lines.append(f"{_fmt_vtt_time(float(seg['start']))} --> {_fmt_vtt_time(float(seg['end']))}")
        lines.append(seg.get('text', ''))
        lines.append('')
    return '\n'.join(lines)


def _soft_owns(job) -> bool:
    """True if the caller may access this job (no login required for anon jobs)."""
    if job.user_id is None:
        return True
    if current_user.is_authenticated and job.user_id == current_user.id:
        return True
    if current_user.is_authenticated and getattr(current_user, 'is_admin', False):
        return True
    return False


# ── POST /api/v1/burn ─────────────────────────────────────────────────────────

@api_v1.route('/burn', methods=['POST'])
@login_required
@limiter.limit('5/minute')
def burn_job():
    """
    POST /api/v1/burn
    Body: { job_id, style: {...}, playResWidth, playResHeight }
    Starts a background FFmpeg burn; progress arrives via SSE stream.
    """
    data     = request.get_json(silent=True) or {}
    job_id   = data.get('job_id')
    style    = data.get('style') or {}
    play_res_w = data.get('playResWidth')
    play_res_h = data.get('playResHeight')

    if not job_id:
        return jsonify({'ok': False, 'error': 'job_id required'}), 400

    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    if not _soft_owns(job):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403
    if job.status not in ('done', 'burned', 'burn_failed'):
        return jsonify({'ok': False, 'error': f'Job not ready (status: {job.status})'}), 409

    job.status = 'burning'
    db.session.commit()

    def run_burn(flask_app, jid, style_cfg, prw, prh):
        from subtitle_utils import generate_ass_file, get_ffmpeg_exec
        import subprocess as _sp

        with flask_app.app_context():
            j = db.session.get(Job, jid)
            if not j:
                return
            try:
                push_event(jid, {'type': 'burn_progress', 'value': 10,
                                 'message': 'Generating subtitle file…'})

                segments = _json.loads(j.segments or '[]')
                ass_path = j.input_path + '.burn.ass'
                pw = prw or 1280
                ph = prh or 720
                generate_ass_file(segments, style_cfg, pw, ph, ass_path, pw, ph)

                push_event(jid, {'type': 'burn_progress', 'value': 30,
                                 'message': 'Burning subtitles with FFmpeg…'})

                base, ext = os.path.splitext(j.input_path)
                out_path  = base + '_burned' + (ext or '.mp4')
                esc_ass   = ass_path.replace('\\', '/').replace(':', '\\:')

                ffmpeg_exec = get_ffmpeg_exec()
                if not ffmpeg_exec:
                    raise RuntimeError('FFmpeg not found on this server')

                vf_filter = f"ass='{esc_ass}'"

                cmd = [
                    ffmpeg_exec, '-y', '-i', j.input_path,
                    '-vf', vf_filter,
                    '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
                    '-c:a', 'copy', '-movflags', '+faststart',
                    out_path,
                ]

                # Run with one automatic retry
                for attempt in range(2):
                    try:
                        _sp.run(cmd, check=True, stdout=_sp.PIPE, stderr=_sp.PIPE,
                                timeout=3600)
                        break
                    except _sp.CalledProcessError as exc:
                        if attempt == 0:
                            push_event(jid, {'type': 'burn_progress', 'value': 50,
                                             'message': 'Retrying burn…'})
                        else:
                            raise RuntimeError(
                                'FFmpeg failed: ' +
                                exc.stderr.decode('utf-8', errors='ignore')[-500:]
                            ) from exc

                j.status      = 'burned'
                j.burned_path = out_path
                db.session.commit()

                push_event(jid, {
                    'type':         'burn_done',
                    'download_url': f'/api/v1/download/{jid}',
                    'srt_url':      f'/api/v1/export/srt/{jid}',
                    'vtt_url':      f'/api/v1/export/vtt/{jid}',
                })

            except Exception as exc:
                try:
                    j.status    = 'burn_failed'
                    j.error_msg = str(exc)
                    db.session.commit()
                except Exception:
                    pass
                push_event(jid, {'type': 'error', 'message': f'Burn failed: {exc}'})

    threading.Thread(
        target=run_burn,
        args=(current_app._get_current_object(), job_id, style, play_res_w, play_res_h),
        daemon=True,
    ).start()

    return jsonify({'ok': True, 'job_id': job_id})


# ── GET /api/v1/download/<job_id> ─────────────────────────────────────────────

@api_v1.route('/download/<job_id>')
def download_job(job_id):
    """Serve the burned video file as a download attachment."""
    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    if not _soft_owns(job):
        return jsonify({'error': 'Forbidden'}), 403
    if not job.burned_path or not os.path.exists(job.burned_path):
        return jsonify({'error': 'Burned file not available'}), 404

    fname = f"translated_{job.input_filename or 'video.mp4'}"
    return send_file(job.burned_path, as_attachment=True, download_name=fname)


# ── GET /api/v1/export/srt/<job_id> ──────────────────────────────────────────

@api_v1.route('/export/srt/<job_id>')
def export_srt(job_id):
    """Return an SRT subtitle file generated from stored segments."""
    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    if not _soft_owns(job):
        return jsonify({'error': 'Forbidden'}), 403

    segments = _json.loads(job.segments or '[]')
    srt_body = _make_srt(segments)
    return Response(
        srt_body,
        mimetype='text/plain; charset=utf-8',
        headers={'Content-Disposition':
                 f'attachment; filename="subtitles_{job_id}.srt"'},
    )


# ── GET /api/v1/export/vtt/<job_id> ──────────────────────────────────────────

@api_v1.route('/export/vtt/<job_id>')
def export_vtt(job_id):
    """Return a WebVTT subtitle file generated from stored segments."""
    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    if not _soft_owns(job):
        return jsonify({'error': 'Forbidden'}), 403

    segments = _json.loads(job.segments or '[]')
    vtt_body = _make_vtt(segments)
    return Response(
        vtt_body,
        mimetype='text/vtt; charset=utf-8',
        headers={'Content-Disposition':
                 f'attachment; filename="subtitles_{job_id}.vtt"'},
    )


# ── POST /api/v1/clear_session ────────────────────────────────────────────────

@api_v1.route('/clear_session', methods=['POST'])
def clear_session_api():
    """
    Delete temporary files for a job.
    Burned file is only removed when older than 10 minutes.
    """
    data   = request.get_json(silent=True) or {}
    job_id = data.get('job_id')
    if not job_id:
        return jsonify({'ok': False, 'error': 'job_id required'}), 400

    job = db.session.get(Job, job_id)
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    if not _soft_owns(job):
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403

    for attr in ('input_path', 'audio_path', 'output_path'):
        p = getattr(job, attr, None)
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    # Only purge burned file if it's older than 10 minutes
    if job.burned_path and os.path.exists(job.burned_path):
        try:
            age = _time.time() - os.path.getmtime(job.burned_path)
            if age > 600:
                os.remove(job.burned_path)
        except OSError:
            pass

    job.status = 'cleared'
    db.session.commit()
    return jsonify({'ok': True})


import os
import json
import subprocess
import time
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, send_from_directory, jsonify, redirect, send_file
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from video_processor import transcriber, NoInternetError
from subtitle_utils import generate_ass_file, time_to_ass_format, get_ffmpeg_exec


app = Flask(__name__)

# ── Load config (SECRET_KEY, DATABASE_URL, FEATURE_FLAGS, etc.) ──────────────
from config import Config
app.config.from_object(Config)

app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max upload

# Ensure directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ── Extensions ────────────────────────────────────────────────────────────────
from extensions import db, login_manager, limiter, talisman

db.init_app(app)
login_manager.init_app(app)
limiter.init_app(app)

csp = {
    'default-src': "'self'",
    'script-src': ["'self'", "'unsafe-inline'"],
    'style-src': ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com'],
    'font-src': ["'self'", 'https://fonts.gstatic.com'],
    'img-src': ["'self'", 'data:'],
    'media-src': ["'self'"],
    'connect-src': ["'self'"],
}

talisman.init_app(
    app,
    content_security_policy=csp,
    force_https=os.environ.get('FORCE_HTTPS', 'false').lower() == 'true',
    session_cookie_secure=False,
    strict_transport_security=False,
)

from models import User, Job, UploadStats  # noqa: E402 — must come after db.init_app


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ── Blueprints ────────────────────────────────────────────────────────────────
from blueprints.auth import auth as auth_bp
from blueprints.api_v1 import api_v1

app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(api_v1, url_prefix='/api/v1')

# ── Create DB tables (idempotent) ─────────────────────────────────────────────
with app.app_context():
    db.create_all()

# ── Scheduled cleanup (72-hour TTL for old job files) ────────────────────────
try:
    from workers.cleanup import cleanup_old_jobs
    from apscheduler.schedulers.background import BackgroundScheduler
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(cleanup_old_jobs, 'interval', hours=1,
                       args=[app],
                       id='cleanup_old_jobs', replace_existing=True)
    _scheduler.start()
    print('[Scheduler] Cleanup job registered — runs every hour.')
except ImportError:
    print('[Scheduler] APScheduler not installed — skipping cleanup scheduler.')
except Exception as _sched_err:
    print(f'[Scheduler] Could not start: {_sched_err}')

import shutil

# ── Upload validation constants ───────────────────────────────────────────────
ALLOWED_EXTENSIONS = {'mp4', 'mkv', 'avi', 'mov', 'webm'}
MAX_UPLOAD_BYTES   = 500 * 1024 * 1024  # 500 MB


def get_video_duration(filepath):
    import shutil
    # Try system ffprobe first (Linux/production)
    ffprobe = shutil.which('ffprobe')
    # Fallback to local Windows build (development)
    if not ffprobe:
        local = os.path.join(os.getcwd(),
                             'ffmpeg-8.0.1-full_build', 'bin', 'ffprobe.exe')
        if os.path.exists(local):
            ffprobe = local
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe, '-v', 'quiet', '-print_format', 'json',
                '-show_streams', '-select_streams', 'v:0', filepath,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        )
        info = json.loads(result.stdout)
        streams = info.get('streams', [])
        if streams:
            duration = float(streams[0].get('duration', 0))
            return duration if duration > 0 else None
    except Exception:
        pass
    return None


def clear_folder(folder_path):
    """Delete all files in the given folder."""
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            print(f'Failed to delete {file_path}. Reason: {e}')


@app.route('/')
def index():
    return render_template('landing.html')


@app.route('/studio')
@login_required
def studio():
    if not request.args.get('job_id'):
        return redirect('/')
    return render_template('index.html')


# ── My Jobs page ──────────────────────────────────────────────────────────────

@app.route('/jobs')
@login_required
def jobs_page():
    user_jobs = (Job.query
                 .filter_by(user_id=current_user.id)
                 .order_by(Job.created_at.desc())
                 .limit(5)
                 .all())
    return render_template('jobs.html', jobs=user_jobs)


# ── Analytics dashboard ───────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    if not current_user.is_admin:
        from flask import abort
        abort(403)
    return render_template('dashboard.html')


@app.route('/dashboard/stats')
@login_required
def dashboard_stats():
    from sqlalchemy import func

    if not current_user.is_admin:
        from flask import abort
        abort(403)

    uid = current_user.id
    today = date.today()
    week_ago  = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    base_q = UploadStats.query.filter_by(user_id=uid)

    total_uploads     = base_q.count()
    uploads_today     = base_q.filter(UploadStats.date == today).count()
    uploads_this_week = base_q.filter(UploadStats.date >= week_ago).count()

    agg = db.session.query(
        func.sum(UploadStats.duration_seconds),
        func.avg(UploadStats.duration_seconds),
        func.sum(UploadStats.file_size_bytes),
        func.max(UploadStats.file_size_bytes),
    ).filter(UploadStats.user_id == uid).one()

    total_duration_hours = round((agg[0] or 0) / 3600, 2)
    avg_duration_minutes = round((agg[1] or 0) / 60, 2)
    total_size_gb        = round((agg[2] or 0) / 1e9, 3)
    largest_upload_mb    = round((agg[3] or 0) / 1e6, 1)

    success_count    = base_q.filter(UploadStats.status == 'done').count()
    success_rate_pct = round(success_count / total_uploads * 100, 1) if total_uploads else 0.0

    by_day_rows = (db.session.query(UploadStats.date, func.count(UploadStats.id))
                   .filter(UploadStats.user_id == uid, UploadStats.date >= month_ago)
                   .group_by(UploadStats.date)
                   .order_by(UploadStats.date)
                   .all())
    uploads_by_day = [{'date': str(r[0]), 'count': r[1]} for r in by_day_rows]

    tgt_rows = (db.session.query(UploadStats.target_lang, func.count(UploadStats.id))
                .filter(UploadStats.user_id == uid, UploadStats.target_lang.isnot(None))
                .group_by(UploadStats.target_lang)
                .order_by(func.count(UploadStats.id).desc())
                .limit(5).all())
    top_total = sum(r[1] for r in tgt_rows) or 1
    top_target_languages = [
        {'lang': r[0], 'count': r[1], 'pct': round(r[1]/top_total*100, 1)}
        for r in tgt_rows
    ]

    src_rows = (db.session.query(UploadStats.source_lang, func.count(UploadStats.id))
                .filter(UploadStats.user_id == uid, UploadStats.source_lang.isnot(None))
                .group_by(UploadStats.source_lang)
                .order_by(func.count(UploadStats.id).desc())
                .limit(5).all())
    src_total = sum(r[1] for r in src_rows) or 1
    top_source_languages = [
        {'lang': r[0], 'count': r[1], 'pct': round(r[1]/src_total*100, 1)}
        for r in src_rows
    ]

    all_files = db.session.query(UploadStats.filename).filter_by(user_id=uid).all()
    ext_counts: dict = {}
    for (fname,) in all_files:
        if fname and '.' in fname:
            ext = fname.rsplit('.', 1)[-1].lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
    most_common_file_ext = sorted(
        [{'ext': k, 'count': v} for k, v in ext_counts.items()],
        key=lambda x: -x['count'],
    )[:5]

    all_created = (db.session.query(UploadStats.created_at).filter_by(user_id=uid).all())
    hour_counts = [0] * 24
    for (ts,) in all_created:
        if ts:
            hour_counts[ts.hour] += 1
    uploads_by_hour  = [{'hour': h, 'count': hour_counts[h]} for h in range(24)]
    peak_upload_hour = hour_counts.index(max(hour_counts)) if total_uploads else 0

    recent_rows = (base_q.order_by(UploadStats.created_at.desc()).limit(10).all())
    recent_uploads = [
        {
            'filename':         r.filename,
            'date':             r.created_at.isoformat() if r.created_at else None,
            'source_lang':      r.source_lang,
            'target_lang':      r.target_lang,
            'duration_seconds': r.duration_seconds,
            'file_size_bytes':  r.file_size_bytes,
            'status':           r.status,
        }
        for r in recent_rows
    ]

    payload = {
        'total_uploads':        total_uploads,
        'uploads_today':        uploads_today,
        'uploads_this_week':    uploads_this_week,
        'uploads_by_day':       uploads_by_day,
        'total_duration_hours': total_duration_hours,
        'total_size_gb':        total_size_gb,
        'avg_duration_minutes': avg_duration_minutes,
        'success_rate_pct':     success_rate_pct,
        'top_target_languages': top_target_languages,
        'top_source_languages': top_source_languages,
        'most_common_file_ext': most_common_file_ext,
        'largest_upload_mb':    largest_upload_mb,
        'peak_upload_hour':     peak_upload_hour,
        'uploads_by_hour':      uploads_by_hour,
        'recent_uploads':       recent_uploads,
    }

    if current_user.is_admin:
        payload['system_total_uploads'] = UploadStats.query.count()
        payload['system_total_users']   = db.session.query(
            func.count(func.distinct(UploadStats.user_id))).scalar()

    return jsonify(payload)


@app.route('/upload_video', methods=['POST'])
@login_required
@limiter.limit('10/minute')
def upload_video():
    clear_folder(app.config['UPLOAD_FOLDER'])

    if 'video' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['video']
    if not file.filename:
        return jsonify({'error': 'No selected file'}), 400

    filename = secure_filename(file.filename or '')
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({'ok': False, 'error': 'Invalid file type. Allowed: mp4, mkv, avi, mov, webm'}), 400
    content_type = file.content_type or ''
    if content_type and not (
        content_type.startswith('video/') or content_type == 'application/octet-stream'
    ):
        return jsonify({'ok': False, 'error': 'Invalid file type'}), 400

    if (request.content_length or 0) > MAX_UPLOAD_BYTES:
        return jsonify({'ok': False, 'error': 'File too large (max 500 MB)'}), 413

    timestamp = int(time.time())
    unique_filename = f"{timestamp}_{filename}"
    save_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
    file.save(save_path)

    actual_size = os.path.getsize(save_path)
    if actual_size > MAX_UPLOAD_BYTES:
        os.remove(save_path)
        return jsonify({'ok': False, 'error': 'File too large (max 500 MB)'}), 413

    duration = get_video_duration(save_path)

    job = Job(
        user_id=current_user.id if current_user.is_authenticated else None,
        input_filename=unique_filename,
        input_path=save_path,
        file_size_bytes=actual_size,
        duration_seconds=duration,
        status='queued',
    )
    db.session.add(job)
    db.session.commit()

    try:
        UploadStats.log_upload(job, status='pending')
    except Exception:
        pass

    return jsonify({
        'url': f'/uploads/{unique_filename}',
        'filename': unique_filename,
        'job_id': job.id,
        'duration': duration,
        'file_size_bytes': actual_size,
        'thumbnail_url': None,
    })


@app.route('/transcribe', methods=['POST'])
@login_required
def transcribe_video():
    data = request.json
    filename = secure_filename(data.get('filename') or '')
    if not filename:
        return jsonify({'error': 'Invalid filename'}), 400
    video_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.abspath(video_path).startswith(os.path.abspath(app.config['UPLOAD_FOLDER'])):
        return jsonify({'error': 'Access denied'}), 403
    target_lang = data.get('target_lang')

    if not os.path.exists(video_path):
        return jsonify({'error': 'Video file not found'}), 404

    try:
        result = transcriber.transcribe_and_translate(video_path, translate_to=target_lang)
        segments = result['segments']

        try:
            job = Job.query.filter_by(input_filename=filename).first()
            if job:
                job.segments = json.dumps(segments)
                job.status = 'done'
                if result.get('no_internet'):
                    job.translation_failed = True
                db.session.commit()
        except Exception as db_err:
            app.logger.warning(f"Could not update job record after transcription: {db_err}")

        response = {'segments': segments}
        if result.get('no_internet'):
            response['no_internet'] = True
            response['warning'] = 'Translation incomplete: no internet connection was detected.'
        return jsonify(response)
    except Exception as e:
        try:
            job = Job.query.filter_by(input_filename=filename).first()
            if job:
                job.status = 'failed'
                job.error_msg = str(e)
                db.session.commit()
        except Exception:
            pass
        app.logger.error(f"Transcription failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/translate_subtitles', methods=['POST'])
@login_required
def translate_subtitles_route():
    data = request.json
    subtitles = data.get('subtitles')
    target_lang = data.get('target_lang')

    if not subtitles or not target_lang:
        return jsonify({'error': 'Missing subtitles or target language'}), 400

    try:
        translated = transcriber.translate_segments(subtitles, target_lang)
        return jsonify({'subtitles': translated})
    except NoInternetError:
        return jsonify({'error': 'No internet connection.', 'no_internet': True}), 503
    except Exception as e:
        app.logger.error(f"Translation failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/clear_session', methods=['POST'])
@login_required
def clear_session():
    try:
        clear_folder(app.config['UPLOAD_FOLDER'])
        return jsonify({'status': 'ok'})
    except Exception as e:
        app.logger.error(f"Clear session failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    filename = secure_filename(filename)
    if not filename:
        return jsonify({'error': 'Invalid filename'}), 400
    job = Job.query.filter(
        (Job.input_filename == filename) | (Job.burned_path.like(f'%{filename}'))
    ).first()
    if job and job.user_id and job.user_id != current_user.id:
        return jsonify({'error': 'Forbidden'}), 403
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


def generate_srt_content(subtitles):
    lines = []
    for i, sub in enumerate(subtitles, 1):
        start = sub.get('start', 0)
        end   = sub.get('end', start + 2)
        text  = sub.get('text', '')
        def to_srt_time(s):
            h   = int(s // 3600)
            m   = int((s % 3600) // 60)
            sec = int(s % 60)
            ms  = int(round((s % 1) * 1000))
            return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
        lines.append(str(i))
        lines.append(f"{to_srt_time(start)} --> {to_srt_time(end)}")
        lines.append(text)
        lines.append('')
    return '\n'.join(lines)


def generate_vtt_content(subtitles):
    lines = ['WEBVTT', '']
    for sub in subtitles:
        start = sub.get('start', 0)
        end   = sub.get('end', start + 2)
        text  = sub.get('text', '')
        def to_vtt_time(s):
            h   = int(s // 3600)
            m   = int((s % 3600) // 60)
            sec = int(s % 60)
            ms  = int(round((s % 1) * 1000))
            return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"
        lines.append(f"{to_vtt_time(start)} --> {to_vtt_time(end)}")
        lines.append(text)
        lines.append('')
    return '\n'.join(lines)


@app.route('/burn', methods=['POST'])
@login_required
def burn_subtitles():
    output_path = None
    ass_path = None

    data = request.json
    video_filename = secure_filename(data.get('filename') or '')
    if not video_filename:
        return jsonify({'error': 'Invalid filename'}), 400
    input_video_path = os.path.join(app.config['UPLOAD_FOLDER'], video_filename)
    if not os.path.abspath(input_video_path).startswith(os.path.abspath(app.config['UPLOAD_FOLDER'])):
        return jsonify({'error': 'Access denied'}), 403
    subtitles = data.get('subtitles')
    style_config = data.get('styles')
    video_width = data.get('videoWidth', 1280)
    video_height = data.get('videoHeight', 720)

    if not subtitles:
        return jsonify({'error': 'Missing data'}), 400

    if not os.path.exists(input_video_path):
        return jsonify({'error': 'Video file not found'}), 404

    # 1. Generate ASS File
    ass_filename = f"{video_filename}.ass"
    ass_path = os.path.join(app.config['UPLOAD_FOLDER'], ass_filename)
    play_res_w = data.get('playResWidth', video_width)
    play_res_h = data.get('playResHeight', video_height)
    generate_ass_file(subtitles, style_config, video_width, video_height, ass_path, play_res_w, play_res_h)

    # 2. Burn using FFmpeg
    output_filename = f"burned_{video_filename}"
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], output_filename)

    ass_path_filter = ass_path.replace('\\', '/').replace(':', '\\:')

    ffmpeg_exec = get_ffmpeg_exec()
    if not ffmpeg_exec:
        return jsonify({
            'error': 'FFmpeg not found. Checked configured path and system PATH.',
            'note': 'Install FFmpeg or ensure ffmpeg-8.0.1-full_build/bin/ is present.'
        }), 500

    command = [
        ffmpeg_exec, '-y',
        '-i', input_video_path,
        '-vf', f"ass='{ass_path_filter}'",
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
        '-c:a', 'copy',
        output_path
    ]

    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        return jsonify({'error': 'FFmpeg failed', 'details': e.stderr.decode('utf-8', errors='ignore')}), 500
    except FileNotFoundError:
        return jsonify({'error': 'FFmpeg executable not found when attempting to run.', 'checked_path': ffmpeg_exec}), 500

    # Persist segments so /export/srt and /export/vtt can serve them
    try:
        _job = Job.query.filter(Job.input_filename == video_filename).order_by(Job.created_at.desc()).first()
        if _job and subtitles:
            _job.segments = json.dumps(subtitles)
            db.session.commit()
    except Exception:
        pass

    # 3. Stream the file directly as download then clean up
    try:
        return send_file(
            output_path,
            as_attachment=True,
            download_name=f"subtitled_{video_filename}",
            mimetype='video/mp4'
        )
    finally:
        try:
            if output_path and os.path.exists(output_path):
                os.remove(output_path)
            if ass_path and os.path.exists(ass_path):
                os.remove(ass_path)
        except Exception:
            pass


@app.route('/export/srt/<filename>')
def export_srt(filename):
    from flask import Response
    job = Job.query.filter(Job.input_filename == filename).order_by(Job.created_at.desc()).first()
    if not job or not job.segments:
        return jsonify({'error': 'No subtitles found for this file'}), 404
    try:
        segs = json.loads(job.segments) if isinstance(job.segments, str) else job.segments
    except Exception:
        return jsonify({'error': 'Could not parse segments'}), 500
    srt_content = generate_srt_content(segs)
    base = filename.rsplit('.', 1)[0] if '.' in filename else filename
    return Response(
        srt_content,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename="{base}.srt"'}
    )


@app.route('/export/vtt/<filename>')
def export_vtt(filename):
    from flask import Response
    job = Job.query.filter(Job.input_filename == filename).order_by(Job.created_at.desc()).first()
    if not job or not job.segments:
        return jsonify({'error': 'No subtitles found for this file'}), 404
    try:
        segs = json.loads(job.segments) if isinstance(job.segments, str) else job.segments
    except Exception:
        return jsonify({'error': 'Could not parse segments'}), 500
    vtt_content = generate_vtt_content(segs)
    base = filename.rsplit('.', 1)[0] if '.' in filename else filename
    return Response(
        vtt_content,
        mimetype='text/vtt',
        headers={'Content-Disposition': f'attachment; filename="{base}.vtt"'}
    )


from flask_limiter.errors import RateLimitExceeded

@app.errorhandler(RateLimitExceeded)
def handle_rate_limit(e):
    return jsonify({
        'error': 'Too many requests. Please wait and try again.',
        'retry_after': str(e.retry_after)
    }), 429


if __name__ == '__main__':
    app.run(debug=False, host='127.0.0.1', port=int(os.environ.get('PORT', 5000)))
"""
workers/cleanup.py — Periodic cleanup of expired job files.
Registered with APScheduler in app.py to run every hour.
Deletes files and job records older than 1 hour.
"""
import os
from datetime import datetime, timedelta


def cleanup_old_jobs(flask_app):
    """
    Remove files and DB records for jobs older than 1 hour.
    Accepts a Flask app instance so it can push an app context.
    """
    with flask_app.app_context():
        from extensions import db
        from models import Job

        expiry_time = datetime.utcnow() - timedelta(hours=1)
        old_jobs = Job.query.filter(Job.uploaded_at <= expiry_time).all()
        for job in old_jobs:
            # Delete video files from disk
            for path in [job.input_filename, job.output_path, job.burned_path]:
                if path:
                    full_path = os.path.join(flask_app.config['UPLOAD_FOLDER'], path) \
                                if not os.path.isabs(path) else path
                    if os.path.exists(full_path):
                        try:
                            os.remove(full_path)
                        except Exception:
                            pass
            # Delete job record from database
            db.session.delete(job)
        db.session.commit()
        print(f'[Cleanup] Deleted {len(old_jobs)} expired jobs')

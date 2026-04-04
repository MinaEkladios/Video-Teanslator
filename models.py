import uuid
from datetime import datetime, date

from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

from extensions import db


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(100))
    preferred_lang = db.Column(db.String(10), default='en')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email}>'


class Job(db.Model):
    __tablename__ = 'jobs'

    id = db.Column(db.String(36), primary_key=True,
                   default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # Status: queued | running | done | failed | cancelled | burned
    status = db.Column(db.String(20), default='queued', nullable=False)

    input_filename = db.Column(db.String(255))
    input_path = db.Column(db.String(512))
    audio_path = db.Column(db.String(512))
    file_size_bytes = db.Column(db.BigInteger)
    duration_seconds = db.Column(db.Float)
    source_lang = db.Column(db.String(20))
    target_lang = db.Column(db.String(20))
    output_path = db.Column(db.String(512))
    burned_path = db.Column(db.String(512))

    segments = db.Column(db.Text)      # JSON string
    translations = db.Column(db.Text)  # JSON string

    translation_failed = db.Column(db.Boolean, default=False)
    error_msg = db.Column(db.Text)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='jobs')

    def __repr__(self):
        return f'<Job {self.id} status={self.status}>'


class UploadStats(db.Model):
    __tablename__ = 'upload_stats'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    date = db.Column(db.Date, default=date.today)
    filename = db.Column(db.String(255))
    file_size_bytes = db.Column(db.BigInteger)
    duration_seconds = db.Column(db.Float)
    source_lang = db.Column(db.String(20))
    target_lang = db.Column(db.String(20))
    # done | failed
    status = db.Column(db.String(10))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='upload_stats')

    @classmethod
    def log_upload(cls, job, status=None):
        """Safe to call on job completion, failure, or at upload time. Commits its own transaction."""
        try:
            resolved_status = status or ('done' if job.status == 'done' else 'failed')
            stat = cls(
                user_id=job.user_id,
                date=date.today(),
                filename=job.input_filename,
                file_size_bytes=job.file_size_bytes,
                duration_seconds=job.duration_seconds,
                source_lang=job.source_lang,
                target_lang=job.target_lang,
                status=resolved_status,
            )
            db.session.add(stat)
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            # Non-fatal — log but don't propagate
            import logging
            logging.getLogger(__name__).warning(
                'UploadStats.log_upload failed: %s', exc
            )

    def __repr__(self):
        return f'<UploadStats {self.filename} {self.status}>'

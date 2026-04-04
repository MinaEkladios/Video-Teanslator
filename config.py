import os

from dotenv import load_dotenv
load_dotenv()

class Config:
    # Security — must be set via SECRET_KEY env var; no default allowed
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise RuntimeError('SECRET_KEY env var must be set. Run: export SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32)")')

    # Database — defaults to SQLite dev.db; set DATABASE_URL env var to switch to PostgreSQL
    # Example PostgreSQL: postgresql://user:pass@host:5432/dbname
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///dev.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Cookie security
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('FLASK_ENV') == 'production'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = os.environ.get('FLASK_ENV') == 'production'

    # Feature flags — flip to True to enable experimental features
    FEATURE_FLAGS = {
        'BATCH_UPLOAD': False,
        'ADMIN_DASHBOARD': False,
        'S3_STORAGE': False,
    }

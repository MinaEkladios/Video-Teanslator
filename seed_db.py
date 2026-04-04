"""
seed_db.py — Creates all tables and inserts a admin user.
Run once before starting the app for the first time:
    python seed_db.py
"""
import sys
import os

# Ensure the project root is on sys.path when called directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app
from extensions import db
from models import User


ADMIN_EMAIL = 'admin@admin.com'
ADMIN_PASSWORD = 'admin1234'
ADMIN_DISPLAY_NAME = 'admin'


def seed():
    with app.app_context():
        # Create all tables (idempotent)
        db.create_all()
        print('[seed] Tables created (or already exist).')

        # Create admin user only if it doesn't exist
        existing = User.query.filter_by(email=ADMIN_EMAIL).first()
        if existing:
            if not existing.is_admin:
                existing.is_admin = True
                db.session.commit()
                print(f'[seed] Granted admin to existing user: {ADMIN_EMAIL}')
            else:
                print(f'[seed] admin user already exists: {ADMIN_EMAIL}')
        else:
            user = User(
                email=ADMIN_EMAIL,
                display_name=ADMIN_DISPLAY_NAME,
                is_admin=True,
            )
            user.set_password(ADMIN_PASSWORD)
            db.session.add(user)
            db.session.commit()
            print(f'[seed] admin user created: {ADMIN_EMAIL} / {ADMIN_PASSWORD}')

        # ── Add more users below ──────────────────────────────────────────
        USERS = [
            # (email, password, display_name)
            ('user2@example.com', 'password123', 'User Two'),
        ]
        for email, password, display_name in USERS:
            if not User.query.filter_by(email=email).first():
                u = User(email=email, display_name=display_name)
                u.set_password(password)
                db.session.add(u)
                db.session.commit()
                print(f'[seed] Created: {email} / {password}')
            else:
                print(f'[seed] Already exists: {email}')

        print('[seed] Done.')


if __name__ == '__main__':
    seed()

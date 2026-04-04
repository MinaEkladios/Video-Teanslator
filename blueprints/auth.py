from flask import Blueprint, request, jsonify, render_template, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user

from extensions import db, limiter
from models import User

auth = Blueprint('auth', __name__)


@auth.route('/register', methods=['GET', 'POST'])
@limiter.limit('5/minute', methods=['POST'])
def register():
    if request.method == 'GET':
        if current_user.is_authenticated:
            return redirect('/')
        return render_template('auth/register.html')

    # POST — support both JSON (API) and HTML form submission
    is_json = request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    data = request.get_json(silent=True) if is_json else request.form

    email = (data.get('email') or '').strip().lower()
    password = data.get('password', '')
    display_name = (data.get('display_name') or '').strip()

    def _error(msg, code=400):
        if is_json:
            return jsonify({'ok': False, 'message': msg}), code
        flash(msg, 'error')
        return render_template('auth/register.html')

    if not email or not password:
        return _error('Email and password are required.')
    if len(password) < 8:
        return _error('Password must be at least 8 characters.')
    if User.query.filter_by(email=email).first():
        return _error('Email already registered.', 409)

    user = User(
        email=email,
        display_name=display_name or email.split('@')[0],
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    login_user(user)

    if is_json:
        return jsonify({'ok': True, 'message': 'Registered successfully.'})
    return redirect('/')


@auth.route('/login', methods=['GET', 'POST'])
@limiter.limit('10/minute', methods=['POST'])
def login():
    if request.method == 'GET':
        if current_user.is_authenticated:
            return redirect('/')
        next_url = request.args.get('next', '')
        return render_template('auth/login.html', next=next_url)

    # POST — support both JSON (API) and HTML form submission
    is_json = request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    data = request.get_json(silent=True) if is_json else request.form

    email = (data.get('email') or '').strip().lower()
    password = data.get('password', '')
    remember = bool(data.get('remember'))
    next_url = data.get('next') or request.args.get('next') or '/'
    # Security: only allow relative redirects
    from urllib.parse import urlparse
    _parsed = urlparse(next_url)
    if _parsed.netloc or _parsed.scheme or not next_url.startswith('/'):
        next_url = '/'

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        if is_json:
            return jsonify({'ok': False, 'message': 'Invalid email or password.'}), 401
        flash('Invalid email or password', 'error')
        return render_template('auth/login.html', next=next_url)

    login_user(user, remember=remember)

    if is_json:
        return jsonify({'ok': True, 'message': 'Logged in.'})
    return redirect(next_url)


@auth.route('/logout', methods=['GET'])
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


@auth.route('/me', methods=['GET'])
def me():
    if current_user.is_authenticated:
        return jsonify({
            'ok': True,
            'user': {
                'id': current_user.id,
                'email': current_user.email,
                'display_name': current_user.display_name,
                'preferred_lang': current_user.preferred_lang,
                'is_admin': current_user.is_admin,
            },
        })
    return jsonify({'ok': False, 'message': 'Not authenticated.'}), 401

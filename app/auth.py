"""User authentication and profile management for patient emergency tracking."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any
from uuid import uuid4

PBKDF2_ITERATIONS = 100_000


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """Hash password using PBKDF2-HMAC-SHA256."""
    salt = salt or secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITERATIONS)
    return key.hex(), salt.hex()


def verify_password(password: str, stored_hash: str, salt_hex: str) -> bool:
    """Verify password matches stored PBKDF2 hash."""
    try:
        salt = bytes.fromhex(salt_hex)
        key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITERATIONS)
        return hmac.compare_digest(key.hex(), stored_hash)
    except Exception:
        return False


def register_user(store, data: dict[str, Any]) -> dict[str, Any]:
    """Register a new patient user."""
    email = data['email'].strip().lower()
    if store.get(f'user_email:{email}'):
        raise ValueError('An account with this email address already exists.')

    user_id = 'user:' + str(uuid4())
    pw_hash, salt_hex = hash_password(data['password'])

    user = {
        'id': user_id,
        'email': email,
        'name': data['name'].strip(),
        'phone': str(data.get('phone', '')).strip(),
        'age': data.get('age'),
        'gender': str(data.get('gender', '')).strip(),
        'emergency_contact': str(data.get('emergency_contact', '')).strip(),
        'emergency_phone': str(data.get('emergency_phone', '')).strip(),
        'password_hash': pw_hash,
        'salt': salt_hex,
        'created_at': time.time(),
    }

    store.put(user_id, 'user', user, permanent=True)
    store.put(f'user_email:{email}', 'user_email', {'user_id': user_id}, permanent=True)

    return {k: v for k, v in user.items() if k not in ('password_hash', 'salt')}


def authenticate_user(store, email: str, password: str) -> tuple[str, dict[str, Any]] | None:
    """Authenticate email and password, issuing an auth token."""
    email = email.strip().lower()
    email_rec = store.get(f'user_email:{email}')
    if not email_rec:
        return None

    user = store.get(email_rec['user_id'])
    if not user:
        return None

    if not verify_password(password, user['password_hash'], user['salt']):
        return None

    token = secrets.token_urlsafe(32)
    auth_key = f'auth_token:{token}'
    expires = time.time() + (30 * 86400)  # 30 days
    store.put(auth_key, 'auth_token', {'user_id': user['id'], 'expires': expires}, expires=expires)

    safe_user = {k: v for k, v in user.items() if k not in ('password_hash', 'salt')}
    return token, safe_user


def get_user_from_token(store, token: str | None) -> dict[str, Any] | None:
    """Resolve an authenticated user from a token."""
    if not token:
        return None
    auth_key = f'auth_token:{token}'
    record = store.get(auth_key)
    if not record:
        return None
    user = store.get(record['user_id'])
    if not user:
        return None
    return {k: v for k, v in user.items() if k not in ('password_hash', 'salt')}


def logout_user(store, token: str | None):
    """Invalidate user session token."""
    if token:
        store.remove(f'auth_token:{token}')

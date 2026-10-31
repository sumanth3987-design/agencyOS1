from functools import wraps
from flask import session, jsonify, abort
from app.models import User

def roles_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_id = session.get('user_id')
            if not user_id:
                return jsonify({'error': 'Authentication required', 'code': 'UNAUTHORIZED'}), 401

            user = User.query.get(user_id)
            if not user or not user.is_active:
                return jsonify({'error': 'Account inactive or not found', 'code': 'FORBIDDEN'}), 403

            if user.role not in allowed_roles:
                return jsonify({'error': 'You do not have permission to access this resource', 'code': 'ACCESS_DENIED'}), 403

            return f(*args, **kwargs)
        return decorated_function
    return decorator
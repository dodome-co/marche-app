from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def role_required(*roles):
    """指定したロールのユーザーだけが開けるようにする。

    未ログインの扱いは login_required に任せる（ログイン画面へ移る）。
    ログインしていてもロールが違えば 403。
    """
    if not roles:
        raise ValueError("role_required にはロールを1つ以上渡してください")

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator

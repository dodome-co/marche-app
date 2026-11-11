"""Stripe を呼ぶコードは、このファイルだけに書く。

画面（views）は current_app.extensions["payment"] 経由で呼ぶ。テストでは create_app() の設定
PAYMENT_SERVICE にフェイクを渡して差し替えるので、本物の Stripe は呼ばない。
"""

import logging
import time

import stripe
from flask import current_app, url_for

logger = logging.getLogger(__name__)

# Checkout Session の有効期限。Stripe の最短は30分なので、時計のずれを見込んで31分にする
SESSION_EXPIRES_MINUTES = 31


class PaymentError(Exception):
    """決済で失敗したとき。str(error) は、画面にそのまま出せる日本語のメッセージ"""


def _client() -> stripe.StripeClient:
    """キーを持たせた StripeClient を返す。stripe.api_key のような全体の設定は使わない"""
    key = current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        raise PaymentError("決済の設定がされていません")
    return stripe.StripeClient(key)


def _log(operation: str, error: stripe.StripeError) -> None:
    # Stripe のメッセージ（詳細）は残さず、種類だけをログに残す
    logger.warning(
        "Stripe の%sに失敗しました: %s (status=%s, code=%s)",
        operation,
        type(error).__name__,
        error.http_status,
        error.code,
    )


def _failure(operation: str, error: stripe.StripeError, message: str) -> PaymentError:
    # 利用者に出すのは固定の日本語だけ。Stripe のメッセージは画面に出さない
    _log(operation, error)
    return PaymentError(message)


def create_checkout_session(order) -> tuple[str, str]:
    """注文から Checkout Session を作り、(session_id, 決済ページの URL) を返す。

    金額は注文の明細（DB）から取る。JPY は小数の無い通貨なので、円のまま渡す（100倍しない）。
    """
    success_url = (
        url_for("orders.complete", order_id=order.id, _external=True)
        # {CHECKOUT_SESSION_ID} は Stripe が置き換える。url_for に渡すと { } が変わってしまう
        + "?session_id={CHECKOUT_SESSION_ID}"
    )
    params = {
        "mode": "payment",
        "line_items": [
            {
                "quantity": item.quantity,
                "price_data": {
                    "currency": "jpy",
                    "unit_amount": item.unit_price,
                    "product_data": {"name": item.product_name},
                },
            }
            for item in order.items
        ],
        "customer_email": order.user.email,
        "metadata": {"order_id": str(order.id)},
        "success_url": success_url,
        "cancel_url": url_for("orders.cancel", order_id=order.id, _external=True),
        "expires_at": int(time.time()) + SESSION_EXPIRES_MINUTES * 60,
    }
    try:
        session = _client().v1.checkout.sessions.create(params)
    except stripe.StripeError as error:
        raise _failure(
            "決済ページの作成",
            error,
            "決済ページを作れませんでした。時間をおいてもう一度お試しください",
        ) from error
    if not session.url:
        raise PaymentError(
            "決済ページを作れませんでした。時間をおいてもう一度お試しください"
        )
    return session.id, session.url


def retrieve_session(session_id: str):
    """Checkout Session を取得して返す（支払いの状況の確認に使う）"""
    try:
        return _client().v1.checkout.sessions.retrieve(session_id)
    except stripe.StripeError as error:
        raise _failure(
            "支払い状況の確認",
            error,
            "お支払いの状況を確認できませんでした。時間をおいてもう一度お試しください",
        ) from error


def expire_session(session_id: str) -> None:
    """Checkout Session を期限切れにする（注文を取り消すときに使う）。

    Stripe は、有効（open）でない Session を期限切れにしようとするとエラーを返す。
    すでに期限切れ（31分たった、など）なら、もう払えないので成功として扱う。
    支払い済み（complete）など、それ以外のときは PaymentError にする。
    """
    try:
        _client().v1.checkout.sessions.expire(session_id)
        return
    except stripe.StripeError as expire_error:
        failure = _failure(
            "決済ページの終了",
            expire_error,
            "決済ページを閉じられなかったため、注文はキャンセルしていません。"
            "お支払いが済んでいないか、注文の状態をご確認ください",
        )
        cause = expire_error
    # 期限切れにできなかった。理由を確かめる（すでに期限切れなら、取り消してよい）
    try:
        session = _client().v1.checkout.sessions.retrieve(session_id)
    except stripe.StripeError as retrieve_error:
        _log("決済ページの状態の確認", retrieve_error)
        raise failure from cause
    if session.status == "expired":
        return
    raise failure from cause

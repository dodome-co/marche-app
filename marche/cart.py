from flask import session

from marche.services.inventory import MAX_QUANTITY

# カートはセッションの "cart" に {"商品ID": 数量} で持つ（金額は持たない）
CART_KEY = "cart"


def get_cart() -> dict[int, int]:
    """セッションのカートを {商品ID(int): 数量(int)} にして返す。

    セッションは JSON で保存されるので、キーは文字列で入っている。
    壊れた値は捨てる: 辞書でない、数字でないキー、同じ商品が2回（"3" と "03" など。
    先に出てきたほうを残す）、整数でない数量、1未満や MAX_QUANTITY を超える数量。
    """
    raw = session.get(CART_KEY)
    if not isinstance(raw, dict):
        return {}
    cart: dict[int, int] = {}
    for key, quantity in raw.items():
        try:
            product_id = int(key)
        except TypeError, ValueError:
            continue
        if product_id in cart:
            continue
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            continue
        if not 1 <= quantity <= MAX_QUANTITY:
            continue
        cart[product_id] = quantity
    return cart


def to_session(cart: dict[int, int]) -> dict[str, int]:
    """セッションに保存する形（キーは文字列）にする"""
    return {str(product_id): quantity for product_id, quantity in cart.items()}


def save_cart(cart: dict[int, int]) -> None:
    if cart:
        session[CART_KEY] = to_session(cart)
    else:
        clear_cart()


def clear_cart() -> None:
    session.pop(CART_KEY, None)

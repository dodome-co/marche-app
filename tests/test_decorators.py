import pytest

from marche.decorators import role_required


def test_role_requiredにロールを渡さないとエラーになる():
    with pytest.raises(ValueError):
        role_required()

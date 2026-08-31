import pytest
from cryptography.fernet import Fernet

from app.security import TokenCipher


def test_token_round_trip() -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    encrypted = cipher.encrypt("secret-token")

    assert encrypted != "secret-token"
    assert cipher.decrypt(encrypted) == "secret-token"


def test_invalid_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="Fernet"):
        TokenCipher("not-a-key")

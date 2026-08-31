import hmac

from app.auth import session_token


def test_session_token_is_stable_and_password_dependent() -> None:
    token = session_token("cfdns", "encryption-key")

    assert hmac.compare_digest(token, session_token("cfdns", "encryption-key"))
    assert not hmac.compare_digest(token, session_token("different", "encryption-key"))
    assert not hmac.compare_digest(token, session_token("cfdns", "different-key"))

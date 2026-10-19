"""凭证加解密测试。"""
import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.core import crypto


def test_encrypt_decrypt_roundtrip():
    plaintext = "sk-test-1234567890"
    token = crypto.encrypt(plaintext)
    assert token != plaintext
    assert crypto.decrypt(token) == plaintext


def test_ciphertext_hides_plaintext():
    plaintext = "sk-secret-abcdef"
    token = crypto.encrypt(plaintext)
    assert plaintext not in token
    assert "sk-secret" not in token


def test_different_inputs_different_tokens():
    assert crypto.encrypt("key-one") != crypto.encrypt("key-two")


def test_same_plaintext_two_encryptions_differ():
    # Fernet 带随机 IV/时间戳，同一明文每次密文应不同
    assert crypto.encrypt("same") != crypto.encrypt("same")


def test_decrypt_invalid_token_raises():
    with pytest.raises(InvalidToken):
        crypto.decrypt("not-a-valid-token")


def test_derive_key_is_valid_fernet():
    f = Fernet(crypto._derive_key("some-secret"))
    token = f.encrypt(b"x")
    assert f.decrypt(token) == b"x"


def test_derive_key_deterministic():
    assert crypto._derive_key("abc") == crypto._derive_key("abc")
    assert crypto._derive_key("abc") != crypto._derive_key("abd")


def test_encrypt_empty_raises():
    with pytest.raises(ValueError):
        crypto.encrypt("")
    with pytest.raises(ValueError):
        crypto.decrypt("")

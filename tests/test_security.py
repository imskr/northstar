from northstar.security import hash_password, verify_password


def test_password_hash_roundtrip(monkeypatch):
    monkeypatch.setenv("PASSWORD_PEPPER", "pepper")
    encoded = hash_password("a sufficiently long password")
    assert encoded.startswith("scrypt$")
    assert verify_password("a sufficiently long password", encoded)
    assert not verify_password("wrong password", encoded)

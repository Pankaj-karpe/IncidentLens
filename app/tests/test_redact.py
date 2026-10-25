from app.ai.redact import REDACTED, redact_obj, redact_text


def test_url_credentials_are_removed_but_host_stays():
    out = redact_text("db postgresql://admin:S3cretPw@db:5432/x failed")
    assert "S3cretPw" not in out and "admin" not in out
    assert "postgresql://" in out and "db:5432" in out


def test_bearer_token_password_and_email_are_removed():
    out = redact_text("Authorization: Bearer abcdefghijklmnop1234 password=hunter22 mail me@example.com")
    assert "abcdefghijklmnop1234" not in out
    assert "hunter22" not in out
    assert "me@example.com" not in out


def test_jwt_known_token_and_private_key_are_removed():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123def456"
    gh = "ghp_" + "a" * 36
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    out = redact_text(f"{jwt} {gh} {pem}")
    assert jwt not in out and gh not in out and "MIIabc" not in out


def test_redaction_is_idempotent():
    text = "postgresql://u:p4ssw0rdX@h/db Authorization: Bearer abcdefghijklmnop1234 token=abc12345"
    once = redact_text(text)
    assert redact_text(once) == once


def test_redact_obj_hides_sensitive_keys_and_keeps_the_original():
    original = {"db_password": "hunter22", "n": 5, "nested": [{"line": "api_key=zzzz1234"}]}
    out = redact_obj(original)
    assert out["db_password"] == REDACTED
    assert out["n"] == 5
    assert "zzzz1234" not in str(out)
    assert original["db_password"] == "hunter22"     # the stored data is never modified
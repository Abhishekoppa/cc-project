import base64
import hashlib
import hmac
import os


_SCRYPT_N = 1 << 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LENGTH = 32
_MAX_MEMORY = 64 * 1024 * 1024


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LENGTH,
        maxmem=_MAX_MEMORY,
    )
    return "$".join((
        "scrypt",
        str(_SCRYPT_N),
        str(_SCRYPT_R),
        str(_SCRYPT_P),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    ))


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n_text, r_text, p_text, salt_text, digest_text = encoded.split("$")
        n, r, p = int(n_text), int(r_text), int(p_text)
        if algorithm != "scrypt" or n not in {1 << 14, 1 << 15, 1 << 16} or r != 8 or p != 1:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
            dklen=len(expected), maxmem=_MAX_MEMORY,
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False

"""Fernet-based symmetric encryption for secrets stored in the database."""

from cryptography.fernet import Fernet, InvalidToken
from app.config import get_settings


def _get_fernet() -> Fernet:
    settings = get_settings()
    key = settings.fernet_key
    if not key:
        # Dev fallback — generate once and warn
        import warnings
        warnings.warn("FERNET_KEY not set — generating ephemeral key. Set it in .env for persistence.")
        key = Fernet.generate_key().decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string. Returns base64-encoded ciphertext."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet ciphertext string. Returns original plaintext."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Decryption failed: invalid token or wrong key")

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class ConfigurationError(ValueError):
    pass


def _cipher():
    key = hashlib.sha256(f"containerlab-studio:{settings.SECRET_KEY}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_configuration(content: str) -> bytes:
    if not isinstance(content, str):
        raise ConfigurationError("Configuration content must be text")
    encoded = content.encode("utf-8")
    if len(encoded) > 1024 * 1024:
        raise ConfigurationError("Configuration exceeds the 1 MiB limit")
    return _cipher().encrypt(encoded)


def decrypt_configuration(content: bytes) -> str:
    try:
        return _cipher().decrypt(bytes(content)).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise ConfigurationError("Configuration cannot be decrypted with the active key") from exc


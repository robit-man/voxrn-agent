"""Installation-local Hugging Face credentials for managed downloads."""

import os
import re
import stat
import tempfile

TOKEN_FILENAME = "huggingface.token"
TOKEN_PATTERN = re.compile(r"hf_[A-Za-z0-9_-]{20,257}")


def read_hub_token(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, encoding="ascii") as handle:
        metadata = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
        ):
            raise OSError("Unsafe permissions on Hugging Face credential.")
        try:
            token = handle.read(261)
        except UnicodeError:
            raise OSError("Invalid stored Hugging Face credential.") from None
        if not TOKEN_PATTERN.fullmatch(token):
            raise OSError("Invalid stored Hugging Face credential.")
        return token


def save_hub_token(path, token):
    fd, temporary = tempfile.mkstemp(prefix=".huggingface-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(token)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

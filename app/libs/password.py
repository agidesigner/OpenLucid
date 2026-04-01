import bcrypt


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def validate_password(plain: str) -> bool:
    """Minimum: 8+ chars with at least one letter and one digit.
    Compatible with Chrome-generated secure passwords."""
    return (
        len(plain) >= 8
        and any(c.isalpha() for c in plain)
        and any(c.isdigit() for c in plain)
    )

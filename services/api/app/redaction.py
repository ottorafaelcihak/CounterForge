"""Small, explicit redaction for human review rationale kept in the audit log."""

import re


def review_text(value: str, *secrets: str) -> str:
    text = value.strip()
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"https?://\S+", "[redacted URL]", text, flags=re.IGNORECASE)
    text = re.sub(r"\bBearer\s+\S+", "Bearer [redacted]", text, flags=re.IGNORECASE)
    return re.sub(
        r"""\b([\w-]*(?:token|password|secret|key|authorization|credential)s?)(["']?)\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;}]+)""",
        r"\1\2=[redacted]",
        text,
        flags=re.IGNORECASE,
    )

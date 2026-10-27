import json
import re


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_count(raw_text: str) -> int | None:
    """Accept only unambiguous integer output or an exact {count: integer} object."""
    text = raw_text.strip()
    if re.fullmatch(r"-?(?:0|[1-9][0-9]{0,9})", text):
        return int(text)
    try:
        parsed: object = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return None
    if isinstance(parsed, dict) and set(parsed) == {"count"} and type(parsed["count"]) is int:
        return int(parsed["count"])
    return None

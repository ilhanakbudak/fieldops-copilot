"""Turning a tool's JSON into something a model and a person can read.

Every tool this repository wrote returns prose. `crm.py` says why: a model handed
JSON tends to reproduce its shape in the answer, and "the customer's equipment
array contains two objects" is not something to read out to somebody on the
phone.

MCP tools are not this repository's to write, and a great many of them return a
JSON document as their text content — the official time server does:

    {"timezone": "America/New_York", "datetime": "2026-08-29T05:37:20-04:00",
     "day_of_week": "Saturday", "is_dst": true}

Handing that straight to the model gives up the property the built-in tools were
careful to keep, and handing it straight to the interface puts a code block in
the middle of a page that has no other code on it. So the adapter parses it once
and produces both halves — prose for the model, structure for the screen.

**This is deliberately generic.** There is no branch here that knows what a
timezone is. The whole argument for speaking MCP rather than writing bespoke
integrations collapses the moment the client needs a special case per server, so
what follows is a set of shape heuristics — a key is a label, an ISO timestamp
is a date, a nested object is a section — and nothing about any particular tool.

The heuristics are guesses and are allowed to be wrong: a value this module
cannot improve on is passed through exactly as it arrived. Being unhelpful is an
acceptable failure. Being wrong about what a value means is not.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

# An ISO-8601 instant, which is the one value shape common enough across tool
# servers to be worth recognising. Deliberately strict: a loose pattern that
# matched part numbers or version strings would reformat them into nonsense.
_ISO = re.compile(r"\A\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?\Z")

# How deep to walk before giving up and printing the raw value. A tool that
# returns four levels of nesting is returning a document, not a result, and
# flattening it produces something less readable than the JSON was.
MAX_DEPTH = 3


def parse_json(text: str) -> Any | None:
    """The document this text encodes, or `None` if it is already prose.

    Only objects and arrays count. A bare `"42"` or `"true"` is valid JSON and
    is far more likely to be a tool that answered in one word than a tool that
    returned a document.
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict | list) else None


def humanise(value: Any, *, depth: int = 0) -> str:
    """A JSON document as lines a model can quote from.

    Objects become `Label: value` lines. A nested object becomes a heading and
    an indented block, because the alternative — `source.timezone` — is a path
    rather than a sentence, and a model asked to relay it will relay the path.
    """
    if isinstance(value, dict):
        return _object(value, depth)
    if isinstance(value, list):
        return _array(value, depth)
    return _scalar(value)


def _object(value: dict[str, Any], depth: int) -> str:
    if depth >= MAX_DEPTH:
        return json.dumps(value)

    lines: list[str] = []
    for key, item in value.items():
        label = humanise_key(key)
        body = humanise(item, depth=depth + 1)
        # A nested object always gets a heading and an indent, even a
        # single-key one: `Source: Timezone: UTC` has two colons and reads as
        # neither. A list does not, unless it rendered to more than one line —
        # a list of sizes is a phrase, and `Sizes:` above an indented
        # `10 in, 12 in` is a heading over a fragment.
        if (isinstance(item, dict) and item) or "\n" in body:
            lines.append(f"{label}:")
            lines.extend(f"  {line}" for line in body.splitlines())
        else:
            lines.append(f"{label}: {body}")
    return "\n".join(lines)


def _array(value: list[Any], depth: int) -> str:
    if depth >= MAX_DEPTH:
        return json.dumps(value)

    # A list of scalars reads as a sentence; a list of objects does not.
    if all(not isinstance(item, dict | list) for item in value):
        return ", ".join(_scalar(item) for item in value) or "none"

    blocks: list[str] = []
    for item in value:
        body = humanise(item, depth=depth + 1)
        first, *rest = body.splitlines() or [""]
        blocks.append("\n".join([f"- {first}", *(f"  {line}" for line in rest)]))
    return "\n".join(blocks)


def _scalar(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        # `is_dst: true` is a fact about daylight saving. `True` is a Python
        # repr, and a model relaying it writes "True" into a sentence.
        return "yes" if value else "no"
    if isinstance(value, str):
        return _maybe_datetime(value)
    return str(value)


def _maybe_datetime(value: str) -> str:
    """An ISO instant as a person would say it, or the string untouched."""
    if not _ISO.match(value):
        return value
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

    # No timezone name here on purpose: the offset is what the payload actually
    # said, and inventing "EDT" from it would be a guess about a place.
    stamp = parsed.strftime("%A %-d %B %Y at %H:%M")
    offset = parsed.strftime("%z")
    return f"{stamp} (UTC{offset[:3]}:{offset[3:]})" if offset else stamp


# Words that are acronyms rather than words. This is a fact about English, not
# about any particular tool server, which is why it is allowed to be a list.
_ACRONYMS = frozenset(
    {
        "api",
        "cpu",
        "css",
        "csv",
        "dst",
        "eta",
        "gps",
        "html",
        "http",
        "https",
        "id",
        "ip",
        "json",
        "pdf",
        "ppm",
        "sku",
        "sla",
        "sql",
        "ssn",
        "uri",
        "url",
        "utc",
        "uuid",
        "vat",
        "xml",
    }
)

# A boolean's key usually already reads as a question. `is_dst: yes` says the
# same thing as `Is dst: yes` and says it the way a person would.
_BOOLEAN_PREFIX = re.compile(r"\A(is|has|was|can|should)[ _-]")


def humanise_key(key: str) -> str:
    """`day_of_week` → `Day of week`. `sourceTimezone` → `Source timezone`.
    `is_dst` → `DST`."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).replace("_", " ").replace("-", " ")
    cleaned = " ".join(spaced.split())
    # Only when something is left: a key that is *only* `is` stays as it is.
    trimmed = _BOOLEAN_PREFIX.sub("", cleaned, count=1)
    cleaned = trimmed or cleaned
    if not cleaned:
        return key

    # Lowercased first, so a camelCase split does not leave `Source Timezone`
    # capitalised mid-label, and only then re-cased.
    words = [
        word.upper() if word.lower() in _ACRONYMS else word.lower() for word in cleaned.split(" ")
    ]
    first, *rest = words
    if first.lower() not in _ACRONYMS:
        first = first[:1].upper() + first[1:]
    return " ".join([first, *rest])


def fields(value: Any) -> list[dict[str, Any]]:
    """The document flattened into labelled rows, for the interface.

    One level of nesting is kept as a `group`, which is as much structure as a
    trail entry in a chat transcript can carry before it stops being a trail.
    Anything deeper is rendered by `humanise` into the row's value, so nothing
    is silently dropped.
    """
    if isinstance(value, list):
        value = {"items": value}
    if not isinstance(value, dict):
        return [{"label": "Result", "value": _scalar(value)}]

    rows: list[dict[str, Any]] = []
    for key, item in value.items():
        if _is_flat_object(item):
            rows.append(
                {
                    "label": humanise_key(key),
                    "group": [
                        {"label": humanise_key(inner), "value": _scalar(value_)}
                        for inner, value_ in item.items()
                    ],
                }
            )
        elif isinstance(item, dict | list):
            rows.append({"label": humanise_key(key), "value": humanise(item, depth=1)})
        else:
            rows.append({"label": humanise_key(key), "value": _scalar(item)})
    return rows


def _is_flat_object(item: Any) -> bool:
    """A one-level object, which is the deepest a trail row can usefully show."""
    return (
        isinstance(item, dict)
        and bool(item)
        and all(not isinstance(inner, dict | list) for inner in item.values())
    )

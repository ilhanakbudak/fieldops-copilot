"""Phone numbers.

Only ever compared, never displayed from the normalised form. A caller-ID
webhook delivers `+12075550142`, the CRM holds `(207) 555-0142`, and an employee
types `207-555-0142`; all three have to resolve to the same customer.

The last ten digits, because a North American number is unambiguous at that
length and the country code is present or absent depending on which system the
number came from. Documented rather than clever: a system that serves other
countries would need a real library here, and this is the line that says so.
"""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\D+")


def normalise_phone(value: str) -> str:
    digits = _DIGITS.sub("", value or "")
    return digits[-10:] if len(digits) >= 10 else ""

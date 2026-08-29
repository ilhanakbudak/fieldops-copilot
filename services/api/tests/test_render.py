"""Turning a tool server's JSON into something readable.

Generic on purpose — see `app/agent/render.py`. Nothing here knows what a
timezone is, and neither does the module under test; these assert the shape
heuristics, and one of them asserts that a heuristic keeps its hands off a value
it cannot improve.
"""

from __future__ import annotations

import pytest

from app.agent.render import fields, humanise, humanise_key, parse_json

CLOCK = {
    "timezone": "America/New_York",
    "datetime": "2026-08-29T05:37:20-04:00",
    "day_of_week": "Saturday",
    "is_dst": True,
}


class TestParsing:
    def test_a_json_document_is_recognised(self) -> None:
        assert parse_json('{"a": 1}') == {"a": 1}
        assert parse_json("[1, 2]") == [1, 2]

    def test_prose_is_left_alone(self) -> None:
        """Some servers answer in a sentence. That needs nothing from us and
        must not be mangled by trying."""
        assert parse_json("The current time is 5:37am.") is None
        assert parse_json("") is None

    @pytest.mark.parametrize("text", ['"just a string"', "42", "true", "null"])
    def test_a_bare_json_scalar_is_not_a_document(self, text: str) -> None:
        """Valid JSON, and far more likely to be a tool that answered in one
        word than one that returned a document."""
        assert parse_json(text) is None

    def test_malformed_json_is_prose(self) -> None:
        assert parse_json('{"unclosed": ') is None


class TestProseForTheModel:
    def test_an_object_becomes_labelled_lines(self) -> None:
        rendered = humanise(CLOCK)

        assert "Timezone: America/New_York" in rendered
        assert "Day of week: Saturday" in rendered
        assert "{" not in rendered

    def test_a_boolean_is_a_word_rather_than_a_python_repr(self) -> None:
        """A model relaying `True` writes "True" into a sentence."""
        assert "DST: yes" in humanise(CLOCK)
        assert "DST: no" in humanise({"is_dst": False})

    def test_an_iso_timestamp_is_said_the_way_a_person_would(self) -> None:
        assert "Saturday 29 August 2026 at 05:37 (UTC-04:00)" in humanise(CLOCK)

    def test_a_nested_object_becomes_a_heading_and_a_block(self) -> None:
        """Rather than `source.timezone` — that is a path, not a sentence, and a
        model asked to relay it relays the path."""
        rendered = humanise({"source": {"timezone": "UTC"}, "difference": "+7.0h"})

        assert rendered.splitlines() == ["Source:", "  Timezone: UTC", "Difference: +7.0h"]

    def test_a_list_of_scalars_reads_as_a_sentence(self) -> None:
        assert humanise({"sizes": ["10 in", "12 in"]}) == "Sizes: 10 in, 12 in"

    def test_a_list_of_objects_becomes_bullets(self) -> None:
        rendered = humanise({"parts": [{"sku": "A-1"}, {"sku": "B-2"}]})

        assert rendered.splitlines() == ["Parts:", "  - SKU: A-1", "  - SKU: B-2"]

    def test_something_too_deep_to_flatten_is_left_as_json(self) -> None:
        """Being unhelpful is an acceptable failure. Flattening four levels
        produces something less readable than the JSON was."""
        deep = {"a": {"b": {"c": {"d": {"e": 1}}}}}

        assert "{" in humanise(deep)

    def test_an_empty_object_says_so_rather_than_printing_nothing(self) -> None:
        assert humanise({"matches": {}}) == "Matches: "


class TestLabels:
    @pytest.mark.parametrize(
        ("key", "label"),
        [
            ("day_of_week", "Day of week"),
            ("sourceTimezone", "Source timezone"),
            ("time_difference", "Time difference"),
            # An acronym is a fact about English, not about a tool server,
            # which is why the module is allowed a list of them.
            ("sku", "SKU"),
            ("customer_id", "Customer ID"),
            ("apiKey", "API key"),
            # A boolean's key already reads as a question; the value is yes/no.
            ("is_dst", "DST"),
            ("has_warranty", "Warranty"),
        ],
    )
    def test_a_key_becomes_a_label(self, key: str, label: str) -> None:
        assert humanise_key(key) == label

    def test_a_key_that_is_only_a_boolean_prefix_keeps_it(self) -> None:
        """Stripping it would leave an empty label, which is worse than an odd
        one."""
        assert humanise_key("is") == "Is"


class TestRowsForTheInterface:
    def test_scalars_become_rows(self) -> None:
        rows = fields(CLOCK)

        assert {row["label"] for row in rows} == {"Timezone", "Datetime", "Day of week", "DST"}
        assert next(row for row in rows if row["label"] == "DST")["value"] == "yes"

    def test_one_level_of_nesting_survives_as_a_group(self) -> None:
        rows = fields({"source": {"timezone": "UTC", "is_dst": False}, "difference": "+7.0h"})
        group = next(row for row in rows if row["label"] == "Source")

        assert [item["label"] for item in group["group"]] == ["Timezone", "DST"]
        assert "value" not in group

    def test_anything_deeper_is_rendered_into_the_row_rather_than_dropped(self) -> None:
        rows = fields({"nested": {"inner": {"deep": 1}}})

        assert "Deep: 1" in rows[0]["value"]

    def test_a_top_level_array_is_still_rows(self) -> None:
        """A server may answer with a list. The interface renders rows and
        should not have to care."""
        rows = fields([{"sku": "A-1"}])

        assert rows[0]["label"] == "Items"


class TestValuesItCannotImprove:
    @pytest.mark.parametrize(
        "value",
        [
            # A part number, a version, a serial. A looser date pattern would
            # reformat every one of these into nonsense.
            "NG-4200",
            "E-04",
            "1.2.3",
            "2026",
            "2026-08",
            "NG42-01771",
            "+7.0h",
        ],
    )
    def test_they_are_passed_through_exactly(self, value: str) -> None:
        assert humanise({"x": value}) == f"X: {value}"

    def test_an_impossible_date_is_not_forced_into_one(self) -> None:
        """It matches the shape and is not a date. Passing it through is right;
        raising in the middle of a customer call is not."""
        assert humanise({"x": "2026-02-31T10:00:00"}) == "X: 2026-02-31T10:00:00"

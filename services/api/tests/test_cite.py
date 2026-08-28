"""Citation resolution.

The load-bearing behaviour: a marker the model invented is removed, and the
sentence it was attached to survives without it.
"""

from __future__ import annotations

from app.rag.cite import render_sources, resolve
from app.rag.search.pipeline import Passage


def _passage(marker: str, title: str = "NG-4200 Service Manual", page: int = 3) -> Passage:
    return Passage(
        marker=marker,
        chunk_id=f"chunk-{marker}",
        document_id=f"doc-{title}",
        document_title=title,
        content="The brine draw cycle completed without the expected drop in level.",
        page=page,
        section="E-04 Brine Valve Fault",
        score=1.0,
    )


def test_a_marker_resolves_to_the_passage_it_was_rendered_from() -> None:
    answer = resolve("Check for a salt bridge [S1].", [_passage("S1")])

    assert len(answer.citations) == 1
    citation = answer.citations[0]
    assert citation.document_title == "NG-4200 Service Manual"
    assert citation.page == 3
    assert citation.section == "E-04 Brine Valve Fault"


def test_an_invented_marker_is_removed_and_the_sentence_survives() -> None:
    """Asking a model to name its sources produces citations that look right and
    are not. This is why resolution happens server-side against what was
    actually retrieved."""
    answer = resolve("Check the injector [S9].", [_passage("S1")])

    assert answer.text == "Check the injector."
    assert answer.citations == []
    assert answer.dropped == ["S9"]


def test_a_group_keeps_the_real_markers_and_drops_the_invented_ones() -> None:
    answer = resolve("Both apply [S1, S7].", [_passage("S1"), _passage("S2")])

    assert "[S1]" in answer.text
    assert "S7" not in answer.text
    assert [citation.marker for citation in answer.citations] == ["S1"]


def test_the_same_marker_cited_twice_produces_one_citation() -> None:
    answer = resolve("First [S1]. Second [S1].", [_passage("S1")])

    assert len(answer.citations) == 1
    assert answer.text.count("[S1]") == 2


def test_citations_come_back_in_marker_order() -> None:
    """S2 before S10, not lexicographically."""
    passages = [_passage(f"S{n}") for n in range(1, 11)]
    answer = resolve("A [S10]. B [S2]. C [S1].", passages)

    assert [citation.marker for citation in answer.citations] == ["S1", "S2", "S10"]


def test_removing_a_marker_does_not_leave_a_gap_before_the_full_stop() -> None:
    answer = resolve("The valve is fine [S4] .", [_passage("S1")])

    assert "  " not in answer.text
    assert answer.text.endswith(".")


def test_rendered_sources_carry_the_location_the_model_can_refer_to() -> None:
    """So the model can write "the service manual says" rather than "[S1] says",
    while the marker stays the only thing it may cite."""
    rendered = render_sources([_passage("S1")])

    assert "[S1] NG-4200 Service Manual · E-04 Brine Valve Fault · page 3" in rendered


def test_a_passage_without_a_page_renders_without_one() -> None:
    passage = Passage(
        marker="S1",
        chunk_id="c",
        document_id="d",
        document_title="Company FAQ",
        content="Anything.",
        page=None,
        section=None,
        score=1.0,
    )

    assert render_sources([passage]).startswith("[S1] Company FAQ\n")

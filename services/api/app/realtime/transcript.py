"""Assembling a live transcript into things worth acting on.

Streaming transcription does not produce sentences. It produces a rising tide of
guesses:

    "so the water"
    "so the water has been"
    "so the water has been warm"
    "so the water's been warm ever since"          ← final
    "since you put the radon system in"            ← final

Running retrieval on each of those is expensive, and it is worse than expensive:
the answers flicker, because each partial is a different question. AD-7 in the
build notes settles it — wait for a boundary, then decide.

This module is the *waiting*. It holds no opinion about what is worth answering;
that is `assist.py`. Splitting them is what makes each testable, and the split is
along the line where the cheap model enters.

Three things it does.

**Partials replace, finals accumulate.** A partial is the current guess at the
utterance in progress and always supersedes the last one. A final is settled
text and joins the transcript. Only finals can start the clock.

**A boundary is a pause, not a full stop.** People speak in fragments —
"the water's been warm" / "since the radon system went in" — and those are one
thought arriving as two utterances. So the trigger is silence after a final, not
the final itself, and any new final resets it. The window is short enough not to
be felt and long enough to catch a breath.

**Context is the last few utterances, not one.** "What about that one?" is
unanswerable alone and obvious after the two lines before it. The window is
capped rather than unbounded so a twenty-minute call does not send a
twenty-minute prompt.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

Speaker = Literal["caller", "agent"]


@dataclass(frozen=True, slots=True)
class Utterance:
    speaker: Speaker
    text: str
    at: float

    def render(self) -> str:
        who = "Caller" if self.speaker == "caller" else "Employee"
        return f"{who}: {self.text}"


@dataclass
class TranscriptBuffer:
    """The call so far, and whether it has paused.

    Deliberately not async and holding no timer of its own: it is asked whether
    a boundary has settled rather than announcing one. A dataclass with a clock
    argument is a dataclass a test can drive without sleeping, and a suite that
    sleeps for its debounce is a suite nobody runs.
    """

    # How long a pause after a final utterance counts as a boundary.
    settle_seconds: float = 0.7
    # How many utterances of context a suggestion is allowed to see.
    window: int = 6
    # How many characters of that window reach the model, newest kept.
    char_budget: int = 1200

    utterances: list[Utterance] = field(default_factory=list)
    partial: str = ""
    partial_speaker: Speaker = "caller"
    _last_final_at: float | None = None
    _consumed: int = 0

    def add_partial(self, speaker: Speaker, text: str) -> None:
        """The current guess at what is being said. Replaces the last one."""
        self.partial_speaker = speaker
        self.partial = text.strip()

    def add_final(
        self, speaker: Speaker, text: str, *, now: float | None = None
    ) -> Utterance | None:
        """Settled text. Joins the transcript and restarts the pause clock."""
        cleaned = text.strip()
        self.partial = ""
        if not cleaned:
            return None

        utterance = Utterance(
            speaker=speaker, text=cleaned, at=now if now is not None else time.monotonic()
        )
        self.utterances.append(utterance)
        self._last_final_at = utterance.at
        return utterance

    def settled(self, *, now: float | None = None) -> bool:
        """Has the speaker paused long enough for this to be a whole thought?

        False while a partial is in flight: somebody who has started the next
        sentence has not finished the last one, whatever the pause looked like.
        """
        if self._last_final_at is None or self.partial:
            return False
        if len(self.utterances) <= self._consumed:
            return False
        moment = now if now is not None else time.monotonic()
        return moment - self._last_final_at >= self.settle_seconds

    def take(self) -> list[Utterance]:
        """The context for one suggestion, and a mark that it was taken.

        Marking matters: without it a caller who says nothing further would have
        the same utterance classified on every tick, which is a bill that grows
        while nobody is talking.
        """
        self._consumed = len(self.utterances)
        return self.utterances[-self.window :]

    def pending(self) -> list[Utterance]:
        """What has arrived since the last `take`. The new part of the thought."""
        return self.utterances[self._consumed :]

    def context(self) -> str:
        """The window as a prompt fragment, newest-first under the budget."""
        kept: list[str] = []
        remaining = self.char_budget
        for utterance in reversed(self.utterances[-self.window :]):
            line = utterance.render()
            if len(line) > remaining:
                break
            remaining -= len(line)
            kept.append(line)
        return "\n".join(reversed(kept))

    def latest(self) -> str:
        """Only what has arrived since the last decision.

        Kept separate from `context` because the two answer different
        questions. *Is there something to look up here* is about the new lines:
        deciding it from the whole window means a caller who says "thanks" after
        a problem has their problem classified a second time, and paid for
        twice. *What exactly are they asking* is about the whole window,
        because "is that meant to happen?" has no subject of its own.
        """
        return "\n".join(utterance.render() for utterance in self.pending())

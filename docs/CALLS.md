# Caller lookup

The phone rings, and by the time somebody picks it up the caller's record is on
the screen: their address, what was installed, when the warranty runs out, and
what the last technician wrote down.

That is the whole feature. Most of the work in it is about the three ways it can
be wrong.

---

## The shape

```
RingCentral ──POST──▶ /calls/incoming ──▶ verify ──▶ parse ──▶ normalise
                                                                   │
                                                             CRM find_by_phone
                                                                   │
                            office browsers ◀──WebSocket── fan-out ─┘
```

Four boundaries, and each one is somewhere a different thing can go wrong:

| | |
|---|---|
| `verify` | Is this delivery from the phone system, or from anybody at all? |
| `parse`  | Is this a ringing call, or one of the dozen other events on a call? |
| `normalise` | `+12075550142`, `(207) 555-0142` and `207-555-0142` are one customer |
| `find_by_phone` | Zero, one, or several accounts — three different screens |

---

## The webhook is the only route a stranger is meant to reach

Everywhere else in this application, a signed-in employee decides to go and
fetch something. Here a telephone system POSTs to us at a moment nobody chose,
with a body that is attacker-controlled until the connector proves otherwise.
Three things follow.

### It answers 204 to everything

A verified call, a bad token, a body that is not JSON, a number nobody in the
CRM has — all the same empty 204.

This is not laziness. A 401 for a bad token and a 204 for a good one is an
oracle for guessing the token. A 404 for an unrecognised number and a 204 for a
recognised one is a way to ask this business, without ever signing in and at
whatever rate you like, which of ten thousand phone numbers belong to its
customers. The screen pop travels to the office over a different connection
entirely, and the webhook's reply carries none of it.

What that costs is worth saying too: a misconfigured phone system gets 204 as
well, and looks healthy while delivering nothing. That is what the log line and
the `call.webhook` audit record are for. The endpoint is silent to its caller,
not to its operator.

### Verification is the connector's job, and it has no off switch

Vendors prove themselves differently. RingCentral echoes a token you gave it
when the subscription was created; another platform signs the body with HMAC. A
route that unwrapped a "signature" header itself would be a route that changes
per vendor, which is the opposite of what a protocol is for.

`verify(headers, body)` is on the protocol with no default implementation, so an
adapter cannot satisfy it by omission — and **the mock verifies too**, against a
token it generates per process. A mock that accepted anything would mean the
credential-free path, which is the path a reviewer actually runs, exercised a
branch production does not have.

RingCentral's scheme is a shared token rather than a signature over the body.
That is weaker than an HMAC — it proves the sender knows a secret, not that the
body is untampered — and it is written down here rather than left for the word
"verified" to carry more weight than it earns. It is also why nothing from the
payload is trusted with anything but a lookup: the number is normalised and used
to search, never written to the database, never passed to a model. The worst a
forged delivery can do is put a plausible call on the office's screen.

### Guessing the token is throttled

The comparison is constant-time, and a 204 either way means an attacker cannot
tell when they are right — but they can still try. The same per-IP throttle the
login route uses applies here. A real phone system never fails verification, so
the only caller this affects is one that should not be there.

---

## `find_by_phone` returns a list

This is the design decision worth the most in practice.

A phone number is not a key in any CRM that has been in use for a while. A
couple keeps two accounts for two properties. A landlord's office line covers a
dozen. A business number outlives the business. The fixtures include two
accounts under the same name on one number, on purpose, because the case has to
be reproducible or it stops being tested.

So the screen renders three different things:

**One match.** The record opens. Equipment, recent jobs, open estimates, the
technician's notes verbatim. This is the ordinary case and the whole feature.

**Several matches.** A list, and **nothing is opened**. Reading the wrong
household's address to somebody on the phone is worse than reading none, and the
reason is not that it is more embarrassing — it is that a wrong record does not
*look* uncertain. The person answering has no signal that they should check. An
explicit "two accounts share this line, ask which" gives them one.

**No match.** The pop still appears, with the number. It is how a new customer
rings for the first time, and it is not an error.

Pulling a full history for a customer that turns out to be the wrong one is also
the reason the CRM is exposed to the agent as two tools rather than one — same
argument, different surface. See [AGENT.md](AGENT.md).

---

## The fan-out

The office end is a WebSocket, held open, authenticated by the same session
cookie as everything else and gated on `calls:assist` — which office staff and
administrators hold, and technicians and salespeople do not. A technician on a
driveway has no reason to be shown who is ringing the office, and the phone
numbers and addresses in a screen pop should not travel further than they need
to.

Four things about `CallHub` that are not obvious:

**Subscribers are principals, not sockets.** A connection joins by handing over
the employee it belongs to and the hub refuses anybody without the permission.
The gate is on the hub as well as on the route because the hub is the object
that does the sending, and a broadcast helper that will send to whatever is in
its list is one careless `subscribe()` from a leak. There is a test that asserts
the hub itself refuses a technician.

**A slow socket cannot hold up the others.** Each subscriber has a bounded queue
and `publish` uses `put_nowait`; a subscriber that is not reading gets dropped
messages and nobody else waits. A screen pop is worth having in the first second
and worthless in the thirtieth, so dropping is the correct failure.

**A refusal is a close code, not a status.** A WebSocket handshake has to be
accepted before the server can say no, so "your role may not watch calls"
arrives as 1008 on a socket that briefly opened. The browser treats that as
terminal and does not reconnect; every other close backs off and retries.

**The socket is same-origin, and that is load-bearing.** It connects to
`/api/calls/stream` through the same Next.js rewrite as every other call, so the
browser sends the session cookie. Pointed straight at the API on another port it
would be a cross-site request, `SameSite=Lax` would strip the cookie, and the
server would close it as unauthenticated — which looks exactly like a bug in the
server. Verified end to end through the dev proxy rather than assumed.

---

## What is deliberately not here

**It is in-process.** Two API instances behind a load balancer would each hold
half the office's sockets, and a webhook arriving at one would pop only that
half's screens. The fix is a Redis or Postgres `LISTEN/NOTIFY` fan-out behind
the same `publish`/`subscribe` interface. It is not written because a
single-instance deployment is the honest scope of this repository, and a
distributed fan-out nobody has run is worse than a documented limit.

**No call control.** `TelephonyConnector` has two methods and neither does
anything to a telephone. There is no transfer, no hangup, no click-to-dial —
read-only by construction, like the CRM and inventory connectors, and asserted
structurally rather than promised.

**No transcription.** The socket takes text, not audio. A transcription
service — Deepgram, AssemblyAI, RingCentral's own — sits in front of it and
sends fragments. Which one is a deployment decision, and putting a vendor SDK in
this repository would have added a credential and a dependency without adding a
demonstrable idea.

---

## During the call

`GET /calls/assist` is the second socket: transcript in, suggestions out, for
the duration of one call. Same cookie, same `calls:assist` gate.

### Most of what it is sent is deliberately ignored

Streaming transcription does not produce sentences. It produces a rising tide of
guesses:

```
"so the water"
"so the water has been"
"so the water has been warm"
"so the water's been warm ever since"        ← final
"since you put the radon system in"          ← final
```

Running retrieval on each of those is expensive, and worse than expensive: the
answers flicker, because each partial is a different question. So partials
update the transcript and never trigger anything, and **a boundary is a pause
after a final, not the final itself** — people describe a problem across a
breath, and answering the first half retrieves nothing useful. Any new final
resets the clock.

`TranscriptBuffer` takes a clock rather than owning a timer, which is why the
tests for it do not sleep. A suite that waits out its own debounce is one nobody
runs twice.

### Then a cheap model decides whether to spend anything

Most of a service call is not a question. It is hello, it is a postcode read out
twice, it is "bear with me". Retrieval on all of it costs roughly a chat turn
per sentence, and it fills the employee's screen with suggestions to ignore —
which is how a live assistant becomes a thing people turn off.

So the classifier answers one question: is there a problem or a question here
that the company's documents could answer? It sees the window for resolving what
was meant and **decides on the newest lines only**. Deciding from the whole
window means a caller who says "thanks" after a problem has that problem
classified, retrieved and generated a second time.

It cannot fail the call. A timeout or a malformed reply falls back to a keyword
rule — worse, and cheap. The failure is a few unnecessary lookups rather than an
assistant that stops mid-call, and the degradation is surfaced rather than
silent.

Refusals are shown. An assistant that says nothing for a minute is
indistinguishable from one that has crashed, and the employee has no way to
check while they are talking to a customer.

### The transcript never widens what may be searched

This matters more here than anywhere else in this application, because half the
words arriving are spoken by a member of the public. The principal comes from
the session cookie that opened the socket and is passed to `retrieve` unchanged.
Nothing in the transcript reaches that argument.

The consequence is visible in the demo, and worth stating rather than hiding:
the NG-RN service manual has the fullest explanation of warm water after a radon
installation, and it is tagged `technician`. Office staff answer the phone, so
they cannot read it and it never enters the candidate set. What they get instead
is the installation SOP's customer-handover note — written for exactly this
call, and in the words you would use to a customer.

That is the corpus being right rather than the system being lucky. Without that
note the honest outcome would be "nothing you can read answers that", and the
fix would be a document tag rather than a change to any of this code.

### And there is a ceiling

Twenty-five suggestions per call, said out loud when it is reached. A hold tone
transcribed as speech, or a caller reading a manual aloud, would otherwise be an
unbounded bill attached to one phone call.

### What the screen does with it

Two columns and a divider the reader can move. There is no split that suits both
ways of using this — one person watches the transcript and glances at
suggestions, the other reads a suggestion aloud and glances at the transcript —
so there is no point choosing one for them.

The divider is a real `role="separator"`: focusable, moved with the arrow keys,
Home and End snap to the bounds, double-click resets, and the position is kept
in `localStorage`. A resizable layout nobody can resize without a mouse is not
one.

---

## Trying it

In demo mode the Live call page has two controls, and neither is a shortcut.

**Ring from** is not a shortcut into the lookup. It fetches the mock connector's verification token
from a signed-in, permission-gated endpoint, and then the *browser* posts a real
webhook — through verification, parsing, normalisation, the CRM lookup and the
fan-out. The path a reviewer clicks is the path a phone system takes.

The token is a demo secret and it is still a secret: anybody holding it can
forge a screen pop, so the endpoint that hands it out requires `calls:assist`
and refuses outside demo mode.

**Start a call** replays a scripted transcript — `fixtures/calls/warm-water.json`
— from the browser, at the delays written in the file, over the same socket a
transcription service would use. The pause detector, the classifier, retrieval
and generation all run for real. Only the microphone is fake.

The script is written to exercise the decisions rather than to flatter them.
It opens with a greeting and an address, which the classifier should refuse and
the screen should say so. The problem arrives across two utterances with a
breath between them. The follow-up is a pronoun with no antecedent of its own,
answerable only from the lines above it.

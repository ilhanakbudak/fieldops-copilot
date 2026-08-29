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

**No transcription yet.** Live call assistance — transcript in, suggestions out
— is milestone 8. The screen pop is what happens before the call is answered;
that is what happens during it.

---

## Trying it

In demo mode the Live call page has a **Ring from** control, and it is not a
shortcut into the lookup. It fetches the mock connector's verification token
from a signed-in, permission-gated endpoint, and then the *browser* posts a real
webhook — through verification, parsing, normalisation, the CRM lookup and the
fan-out. The path a reviewer clicks is the path a phone system takes.

The token is a demo secret and it is still a secret: anybody holding it can
forge a screen pop, so the endpoint that hands it out requires `calls:assist`
and refuses outside demo mode.

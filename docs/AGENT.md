# The agent

Milestone 3 built a retrieval pipeline: every question ran hybrid search, got
passages, and generated an answer over them. That is a good search box and a bad
assistant. Asked *"what is today's date"* it searched a corpus of
water-treatment manuals for a calendar, found nothing relevant, and said so.

Milestone 4 makes retrieval one of the things the assistant can *choose* to do.

```
model → tool calls → results → model → … → answer
```

---

## What it can reach

| Tool | Where it comes from | Gated on |
|---|---|---|
| `search_knowledge_base` | The whole of milestone 3, behind one function | `documents:read` |
| `find_customer` | The CRM connector | `customers:read` |
| `get_customer_detail` | The CRM connector | `customers:read` |
| `mcp_time_get_current_time` | **`mcp-server-time`**, over MCP | — |
| `mcp_time_convert_time` | **`mcp-server-time`**, over MCP | — |

### The clock is not a tool I wrote

It is [`mcp-server-time`](https://github.com/modelcontextprotocol/servers), one
of the official Model Context Protocol reference servers, installed as a
dependency and launched as a subprocess speaking MCP over stdio. The client
discovers whatever it advertises and hands those tools to the agent alongside
the built-in ones.

Which is worth explaining, because "what is the date" is four lines of Python
and this is not four lines.

The four lines are not the point. A service business will want the assistant to
reach a dozen systems that already exist, and the choice is between writing and
maintaining a dozen bespoke integrations or speaking the protocol those systems
increasingly already speak. Demonstrating the second on a trivial tool costs
almost nothing and is the same code path as demonstrating it on a hard one.

It also settles the routing question concretely: asked the date, the model calls
the clock, and the retrieval pipeline never runs.

**Failure is not fatal.** A server that will not start, or hangs on handshake,
costs the assistant those tools and nothing else. An assistant that could not
answer questions about its own manuals because an unrelated subprocess died
would be a poor trade. `tests/test_mcp.py` asserts that, and also launches the
real server — a client tested only against a stub has never spoken the protocol.

Adding another is configuration, not code:

```bash
MCP_SERVERS='[{"name":"time","command":"python","args":["-m","mcp_server_time"]},
              {"name":"docs","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/srv/docs"]}]'
```

### What comes back is translated, not forwarded

Every tool in this repository returns prose for the model and structure for the
interface, deliberately: a model handed JSON reproduces its shape in the answer,
and "the customer's equipment array contains two objects" is not something to
read out to somebody on the phone.

MCP servers do not know about that split. A great many of them — the official
time server included — return a JSON document as their text content:

```json
{"timezone": "America/New_York", "datetime": "2026-08-29T05:37:20-04:00",
 "day_of_week": "Saturday", "is_dst": true}
```

Forwarding that hands the model a payload to paraphrase and the interface a code
block to render, which is the one thing the built-in tools were careful not to
do. So the client parses the response once and produces both halves from it —
prose for the model, labelled rows for the screen:

```
Timezone: America/New_York
Datetime: Saturday 29 August 2026 at 05:37 (UTC-04:00)
Day of week: Saturday
DST: yes
```

**This is deliberately generic.** There is no branch in the client that knows
what a timezone is. The whole argument for speaking a protocol rather than
writing bespoke integrations collapses the moment it needs a special case per
server, so `app/agent/render.py` is a set of shape heuristics — a key is a
label, an ISO timestamp is a date, a nested object is a section — and nothing
about any particular tool. `structuredContent` is preferred where a server sends
it; text is parsed where one does not, which is most of the ecosystem today.

The heuristics are guesses and are allowed to be wrong, so they keep their hands
off anything they cannot improve. `NG-4200`, `1.2.3`, `2026-08` and `+7.0h` pass
through exactly as they arrived — a looser date pattern would reformat every
part number in the corpus into nonsense. Being unhelpful is an acceptable
failure; being wrong about what a value means is not.

---

## Four constraints on the loop

**Tools are role-gated, like documents.** The toolset is built per request from
the caller's principal. A technician's model is never told a pricing lookup
exists — which is stronger than refusing the call, because a tool the model
cannot see is one it cannot be talked into using, and a prompt injection has
nothing to work with.

**A step ceiling.** Four by default. Without one, a model that keeps calling the
same tool because it dislikes the answer is an unbounded bill. On the final step
the tools are *withdrawn* rather than the loop simply stopping, so the model has
to answer with what it has — a better failure than an empty response.

**Tools run concurrently within a step.** A model that asks for a customer
lookup and a manual search at once waits for the slower, not the sum.

**A failing tool returns a result, not an exception.** "The CRM did not answer"
goes into the transcript for the model to relay. The alternative is a 500 that
loses the conversation and tells the user nothing about why. The one thing it
must never do is look like an empty answer — *no customer found* and *the lookup
failed* lead to completely different next actions.

---

## What the interface sees

The stream carries the decisions, not just the text:

```
start      the conversation id
tool       a tool is about to run
tool_done  what it returned, with a structured payload
sources    retrieved passages, when the knowledge tool was one of them
delta      answer text
done       resolved citations, tokens, cost
```

A reader watching *"Looking up the customer — Found 1 customer"* then *"Reading
the customer record — Pulled Priya Raman (NG-0876)"* can see what the assistant
decided to do. When the answer is wrong, that trail is the difference between a
bad decision and a bad execution.

**Structured results never reach the model.** A tool returns prose for the model
and a payload for the interface. Keeping them separate is what stops an
interface change from silently altering what the model is told.

A trail entry that came back with data expands to show it — the same
chip-then-body shape as a citation, because it answers the same question:
*where did that come from*. A second interaction idiom for the same question is
one the reader has to learn twice.

**Every tool call is audited**, in `_invoke` — the one function every call
passes through, built-in, MCP, and the ones the model invents. That placement is
the point: a tool added later cannot forget to audit itself, because it was
never the tool's job. See [SECURITY.md](SECURITY.md).

---

## The demo provider

`LLM_PROVIDER=mock` routes tools by matching the question against a keyword list
per tool — a crude imitation of what a real model does with a tool description.
Crude, and enough to demonstrate the thing that matters: asked the date it calls
the clock, asked about a fault code it searches the manuals, asked who a
customer is it queries the CRM, and given exactly one match it chains into
reading that record.

The routing lives in `_route` in `app/llm/mock.py` and is deliberately legible,
because a reviewer should be able to see exactly how much of the demo is real.
Set `LLM_PROVIDER=openai` with a key and the same loop runs with a model
deciding instead.

---

## Cost

Two tiers, and the split is the largest single lever on what this costs to run:

| | Model | Used for |
|---|---|---|
| Cheap | `CHEAP_MODEL` | Query analysis — rewrite, term extraction, intent |
| Good | `CHAT_MODEL` | The answer, and every tool-calling decision |

Every call is priced into `usage_events` at write time. Prices change; what a
call cost on the day it ran does not.

The prompt is assembled stable-prefix-first — system rules, then passages, then
the conversation, then the question — because prompt caching keys on an exact
prefix match. Reordering for readability silently disables the discount.

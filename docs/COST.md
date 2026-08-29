# Cost

A naive RAG system's bill is mostly things it did not need to do: the expensive
model rewriting a query, the same question answered from scratch for the fortieth
time, a whole manual sent as context because nobody capped it.

Five mechanisms, and one screen that says whether they are working.

---

## 1. Two models, and the split is visible

Query analysis, live-call triage and intent routing are short, structured and
forgiving. Generation is not. So the cheap model does the first three and the
expensive one only writes the answer.

That is easy to claim and easy to get wrong in the accounting: an earlier version
of this summed the analysis call into the chat total and priced the whole thing at
the chat model's rate. The dashboard then said the opposite of what the design
does, which is worse than not having one.

Every model call is now its own `usage_events` row, at its own model:

| Feature | Model | What it is |
|---|---|---|
| `query_analysis` | cheap | Rewrite, extract exact terms, guess document type |
| `call_triage` | cheap | Is this utterance worth answering? |
| `chat` | good | The answer an employee reads |
| `call_assist` | good | The suggestion read aloud on a call |

Measured on the corpus in this repository, against a real key: analysis is about
**9%** of a turn's spend. The point is not the number — it is that the number
exists and is on a screen.

---

## 2. Prompt caching, and the ordering that earns it

Providers discount input tokens they have seen before, keyed on an **exact
prefix**. So the parts that do not vary are assembled first — system rules, then
retrieved passages, then the conversation, then the question.

Reordering those for readability silently disables the discount. Nothing breaks,
the answers are identical, and the bill goes up: the only signal is
`cachedInputTokens` sitting at zero, which is why that figure is on the
dashboard and why `tests/test_costs.py` asserts the ordering structurally rather
than trusting a comment.

Against a real key it works out at roughly **1,500–1,900 cached input tokens on a
second question in a conversation**, out of about 2,600.

---

## 3. A retrieval budget

`CONTEXT_CHAR_BUDGET` is a hard cap on the characters of retrieved context that
reach the prompt. Without one, a question that happens to match a long
troubleshooting table quietly costs ten times what a normal question does — and
it is the same question to the person asking it.

Parent-document expansion makes this matter more, not less: retrieval matches a
900-character chunk and the prompt receives the section around it. The budget is
what stops that being unbounded.

---

## 4. Local embeddings

`EMBEDDING_PROVIDER=local` runs bge-small as ONNX in-process. Ingesting a
400-page manual then costs nothing at all, which is the difference between "we
can index everything" and "we index what we can justify".

The OpenAI provider is there for deployments that would rather not run a model,
and both are configured to 384 dimensions so the vector column does not change
when a deployment switches. Embedding spend is not itemised in the dashboard:
with the local provider it is zero, and with OpenAI it is dominated by ingestion,
which is a one-off per document rather than a per-question cost.

---

## 5. The semantic cache — and why it is not just a threshold

Forty people share one manual. "What's the warranty on the radon system" and
"how long is the radon system under warranty" are the same question, and paying
for both is paying for wording.

So an answer is stored against the embedding of the question that produced it. A
hit costs one embedding call and nothing else.

Three things make it safe, and the third was a surprise.

### The caller's audience is part of the key

A cached answer was assembled from the passages the *first* asker could see.
Serving it to somebody else serves them a summary of documents they may have no
right to — with citations, looking exactly as trustworthy as an answer they were
entitled to. A salesperson's answer and a technician's answer to the same words
are two different rows. That costs hit rate and is not negotiable.

### Only some answers may be cached at all

A cached answer has to be a function of the question. Anything the clock, the CRM
or the inventory touched is a current value, and a stale one read aloud on the
phone is worse than a slow one. Anything with conversation history behind it
means something different in every conversation. What is left — a first turn
answered purely out of the corpus — is both the safe case and the one that
repeats.

### Similarity alone would have reintroduced the E-04 bug

Measured against real `text-embedding-3-small`:

| | cosine |
|---|---|
| "What does error code E-04 mean?" ↔ "what is error code E-04" | 0.944 |
| "What does error code E-04 mean?" ↔ "E-04 — what does that mean?" | 0.734 |
| "…warranty on a radon water system?" ↔ "How long is the radon system under warranty?" | 0.874 |
| **"…E-04 mean?" ↔ "…E-14 mean?"** | **0.824** |
| **"…regeneration on the NG-4200?" ↔ "…on the NG-6800?"** | **0.905** |

The distributions overlap. A *different fault code* scores higher than two
genuine rewordings; the two softener manuals score higher than all but one. No
threshold admits rewordings and excludes different questions.

Which is [AD-2](RAG.md) arriving somewhere new — embeddings collapse exactly the
surface differences that a part number consists of. The retriever answers that
with a keyword leg. The cache answers it by putting the extracted codes in the
key: `E-04` and `E-14` are different rows before their vectors are ever compared.

With that guard the threshold is an ordinary 0.92 rather than a near-identity
0.98, and what it catches is *rewordings* rather than paraphrases. Conservative
on purpose: a miss costs one model call, and a wrong hit is a confident wrong
answer with a citation attached.

It is not a correctness mechanism. Entries expire, because the corpus changes
underneath them — re-tagging a document's audience does not reach in and
invalidate anything.

---

## The screen

`/admin/costs`, gated on `cost:read`, which is an administrator's permission: it
says in aggregate what each employee has been asking the assistant about, and
that is a management view rather than a colleague's.

Three breakdowns, because "which feature is expensive", "who is asking" and "is
it going up" are three questions with three different answers. Grouped in SQL —
this is the one screen reading a table that grows without bound, and pulling a
month of rows into the process to sum them is the shape of a page that works
until it does not.

**Every figure is summed from `cost_usd` as it was written, never recomputed
against today's price list.** Prices change; what a call cost on the day it ran
does not, and a dashboard that silently rewrites history is worse than none.

An unknown model prices at zero rather than raising. Refusing to answer an
employee's question because a price list is out of date would be a poor trade,
and a zero on the dashboard is visible enough to get the table updated.

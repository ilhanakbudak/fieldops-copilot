# Retrieval

How a manual becomes something the assistant can answer from, and why each step
is the way it is. The short version: almost every decision here exists because
the obvious implementation produces answers that are confidently wrong rather
than obviously broken — which is the expensive kind.

```
bytes → pages → blocks → units → chunks → vectors → rows
        ↑                                            ↓
     extractor                                  vector store
   (per page, OCR                          (role filter in the
    only where needed)                       same query)
```

---

## 1. Extraction

`TextExtractor` is a protocol with four implementations. **The unit is a page,
not a document** — which is what lets a scanned insert inside an otherwise
digital manual fall through to OCR without the whole file taking the slow path,
and what makes "which pages needed OCR" answerable afterwards.

| Extractor | When |
|---|---|
| `PdfPlumberExtractor` | Default for PDF. MIT. |
| `PyMuPdfExtractor` | Several times faster. **AGPL-3.0**, so it is an optional extra rather than a dependency this MIT repository imposes: `uv sync --extra pymupdf`, then `PDF_EXTRACTOR=pymupdf`. |
| `MarkdownExtractor` | SOPs and FAQs that were never PDFs, and the fixture corpus. |
| `PaddleOcrExtractor` | Pages with no text layer. Optional extra. |

Two details that are easy to get wrong:

**Columns.** `extract_text(layout=False)` — manuals are frequently two-column,
and letting the extractor interleave them produces text that embeds as noise.
That is worse than no text at all, because it looks like a successful
extraction.

**Hyphenation.** PDF text arrives with words split across line breaks and
paragraphs hard-wrapped. Left alone, a chunk boundary lands mid-word and the
embedding is of something nobody wrote.

### Why PaddleOCR, and why not the benchmark leader

The current top of OmniDocBench is **PaddleOCR-VL**, and it is **GPU-only — no
CPU, no ARM build**. Making it the default would mean this repository no longer
runs on the machine of the person reviewing it, in exchange for accuracy on
scanned pages that a synthetic corpus does not contain.

So: **PP-OCRv5 on CPU** by default — Apache-2.0, and comfortably the strongest
of the engines that run without a GPU. PaddleOCR-VL sits behind the same
protocol for deployments that have one. Surya (via Marker) is the credible
alternative and the one to benchmark against if scan quality turns out poor.

The engine matters less than *when it runs*. A page is sent to OCR only when its
text layer is missing or too thin to trust — and "too thin" is a character
threshold rather than zero, because a scanned page usually still carries a
running header, a page number and a stamped revision code. OCR-ing a 400-page
manual to recover three scanned diagrams costs a hundred times what the diagrams
are worth.

A page that yields nothing and has no OCR to fall through to is **counted and
reported on the document**, not silently ingested as blank. "Ready, 412 chunks,
14 pages had no text" is a true status. "Ready" alone is not.

---

## 2. Chunking

Three ideas, each because the naive version fails in a way that is hard to see.

**Split on structure, not on a character count.** A fixed-width window cuts
through the middle of a troubleshooting table and embeds half a symptom with
half an unrelated remedy.

**Merge blocks, or headings become chunks.** Splitting on blank lines leaves
`### E-04 Brine Valve Fault` as a block of its own. Emitted as a chunk, it
embeds beautifully against a query about that heading and contains no answer —
a confident, useless result. Consecutive blocks are merged up to the budget and
a heading is merged *forward* into the text it introduces. A heading immediately
followed by a subheading absorbs it too, and the unit takes the more specific
section name, because that is what a citation should say.

**Small chunks for matching, large sections for reading.** A 900-character chunk
is about one thing, so its vector means one thing. But handing only that to the
model loses the sentence before it that said which valve was under discussion.
Every chunk records a `parent_index`; retrieval matches the chunk and the prompt
receives the reassembled parent.

The parent is not stored twice — it is reassembled from its members, stitched on
their overlap so the model does not read the same sentence twice and treat the
repetition as emphasis. Which also means re-tuning the parent size later does
not require re-ingesting the corpus.

| Setting | Default | |
|---|---|---|
| `CHUNK_CHARS` | 900 | What gets embedded |
| `CHUNK_OVERLAP_CHARS` | 150 | So a fact split across a boundary is retrievable from either side |
| `PARENT_CHARS` | 3200 | What the model reads |

---

## 3. Embeddings

`EmbeddingProvider`, three implementations, **all producing 384 dimensions.**

That last part is deliberate. `bge-small-en-v1.5` is natively 384; OpenAI's
`text-embedding-3-small` is asked for 384 through its `dimensions` parameter,
which its Matryoshka training makes safe. One width means the vector column does
not change when a deployment switches provider — or falls back during an outage.

| Provider | |
|---|---|
| `local` | FastEmbed / ONNX, no GPU, no API key. Ingesting a 400-page manual costs nothing and reveals nothing to a third party — which for a corpus of internal procedures and pricing is the more important half. Downloads ~130 MB on first use. |
| `openai` | `text-embedding-3-small` at 384 dimensions. |
| `hashing` | A deterministic stand-in for the test suite. **Not a semantic model.** It keeps the suite hermetic and one second long instead of downloading weights on every clean CI run — at the cost that CI never exercises the real embedder, which is stated here rather than left to be discovered. |

`bge` wants an instruction prefix on queries and none on passages. Embedding both
sides symmetrically measurably loses recall, and the failure is invisible: the
results are merely a bit worse, forever. Hence two methods on the protocol rather
than one.

---

## 4. Storage and search

```python
async def search(
    query_vector: list[float],
    *,
    roles: frozenset[Role],   # no default, no overload without it
    limit: int = 20,
    ...
) -> list[SearchHit]
```

**`roles` has no default.** An implementation cannot satisfy the protocol while
ignoring it and a caller cannot forget to pass it — which is the difference
between a role filter and a role convention. The reasoning is in
[SECURITY.md](SECURITY.md); the short version is that a chunk filtered out
*after* retrieval has already been read, and is one careless refactor away from
reaching the model's context.

| Store | |
|---|---|
| `PgVectorStore` | Cosine through `<=>` against an HNSW index. The role predicate sits in the same `WHERE` clause as the distance ordering, on the `allowed_roles` array denormalised onto `chunks` — so the planner filters and ranks in one pass, and there is no arrangement of the query where the filter is skipped but the ranking still happens. |
| `SqliteVectorStore` | Exact brute-force cosine, no index. The credential-free path. Its limits are the point: at a few thousand chunks it is fast and returns the *true* nearest neighbours, which makes it a useful oracle to check an approximate index against. At a few hundred thousand it would be hopeless — which is the work HNSW does in production. |

A Weaviate or Qdrant adapter is this interface and nothing else. That is the
honest answer to "which vector database": the recommendation is Postgres, and
the recommendation is a judgement rather than the only thing that was ever wired
up.

### Why Postgres rather than a dedicated vector store

Retrieval here is not pure vector search. Every real query is similarity
*filtered by* role and document type, and half of them are keyword lookups on a
part number or an error code — `E-04` and `E-14` are nearly identical vectors and
completely different answers.

In Postgres that is one query with a `WHERE` clause and a `tsvector` join. In a
dedicated vector database it is a metadata filter, a separate keyword system,
and a second store to secure, back up and pay for. At thousands of documents
rather than billions, that buys nothing — and it gives up row-level security,
which is the only layer that still holds when application code forgets.

---

## 5. Re-running it

Ingestion is deliberately **not one transaction**. The document row is committed
as `processing` before any work starts, and the outcome — `ready` or `failed`,
with the reason — is committed separately. A 400-page manual takes minutes to
index and holding a write transaction open for that blocks every other writer;
and if it does fail, the row has to survive the caller's rollback or the failure
vanishes and the operator is left guessing.

- **Idempotent.** Keyed on the SHA-256 of the bytes. A corpus holding the same
  manual twice does not merely waste space — it doubles that document's weight
  in every result list.
- **Re-indexable.** `POST /documents/{id}/reindex` rebuilds the chunks. That is
  what you need after changing the chunk size, fixing an extractor, or switching
  embedding provider — none of which should require deleting and re-uploading a
  manual somebody spent an afternoon collecting.
- **Re-taggable.** Changing a document's audience rewrites `allowed_roles` on
  every one of its chunks in the same transaction. The retrieval query filters
  on the chunk copy, so a document and its chunks disagreeing about their
  audience would be a leak with a plausible-looking admin screen above it.

---

---

# Querying

> Since milestone 4 the whole of this section runs behind a tool the model may
> choose to call, rather than on every question. Asked the date, the assistant
> calls a clock and none of this executes. See [AGENT.md](AGENT.md).

## 6. Query analysis

Employees do not type search queries. They type *"the E-04 thing again on the
Hendersons' unit — what do I check first?"*, and embedding that verbatim buries
the two characters that matter under context the corpus has never seen.

A cheap model rewrites the question, extracts exact terms, guesses relevant
document types and classifies the intent. Three constraints on it:

**It runs on the cheap model.** Short, structured, forgiving work. Paying the
answer model to do it is most of a naive RAG system's bill.

**It cannot fail the request.** A timeout, a malformed response, a provider
outage — all fall back to the question as written, which is what an unanalysed
pipeline would have done anyway. A regex still recovers any error code, because
that is the part worth being certain about. Retrieval degrades; it does not stop.

**Its guesses do not remove documents.** An earlier version passed the guessed
document type into both search legs as a hard filter. Asked *"why is the water
warm since the radon system was installed"*, the cheap model saw "installed",
guessed `sop`, and the service manual that actually answers the question was
excluded before ranking ever ran. A test caught it. Only the caller's role
removes a document; a heuristic adjusts ranking.

## 7. Two searches, fused on rank

Embeddings place `E-04` and `E-14` almost on top of each other — two characters
apart in a space built to collapse surface differences, which is what it is for
and exactly wrong for a part number. Lexical search has the opposite bias: it
cannot tell "warm water" from "elevated temperature" and tells `E-04` from
`E-14` perfectly.

| | |
|---|---|
| Postgres | `websearch_to_tsquery` against the generated `tsvector` column, ranked by `ts_rank_cd`. `websearch_` rather than `to_tsquery` because the latter raises on the first question mark anybody types. |
| SQLite | An FTS5 table kept in step by triggers, ranked by BM25, with `tokenize = "unicode61 tokenchars '-_.'"` so `E-04` and `NG-4200` survive as single terms. |

Terms are OR-ed, not AND-ed. A whole sentence rarely has every word in one
passage, and requiring that returns nothing — which reads as a broken index
rather than a strict one. Ranking sorts out relevance; the query's job is
candidates.

The two lists are then fused with **reciprocal rank fusion**:

```
score(d) = Σ  1 / (60 + rank(d, list))
```

Scores are thrown away and only ranks are kept, because a cosine similarity and
a BM25 value share no scale and normalising them requires knowing distributions
that change with the corpus, the query and the dialect. Agreement between the
two legs is the strongest signal available and neither can fake it alone.

## 8. Rerank, then exact terms

Fusion never reads the passages. A cross-encoder reads the question and one
passage *together* and scores that pair — far more accurate, far too expensive
to run over a corpus, so it runs over the forty candidates fusion produced.
`ms-marco-MiniLM-L-6-v2` through FastEmbed: local, CPU, ~90 MB once.

Then one correction on top. A cross-encoder is a general relevance model and
does not know that `E-04` and `E-02` are different faults rather than
near-synonyms — asked about one it will happily rank the other above it. A
passage containing an extracted code verbatim sorts ahead of every passage that
does not. Applied as a separate sort key rather than an additive bonus, because
the reranker emits unbounded logits and any constant decisive for one model is
wrong for the next.

## 9. Expand, then budget

Retrieval matched a precise 900-character chunk. The model reads the **parent
section**, reassembled from its sibling chunks and stitched on their overlap, so
it has the sentence that said which valve was under discussion. Passages are
de-duplicated by parent — two chunks from one section produce one passage, or
the model reads the same text twice and the context budget pays for it.

Then a hard character cap. Without one, a question that happens to match a long
table quietly costs ten times what a normal question does.

## 10. Citations, resolved rather than requested

Asking a model to name its sources produces citations that look right and are
not: a plausible document title, a page number that reads like a page number,
both invented, with no way for a reader to tell.

So the model is never asked to name anything:

```
prompt      [S3] NG-4200 Water Softener — Service Manual · page 3
model       "…break the salt bridge with a broom handle [S3]."
resolved    S3 → document 8f2c…, page 3, "E-04 Brine Valve Fault"
```

A marker the model invented — `[S9]` when six passages were retrieved — resolves
to nothing and is **stripped from the text**. The sentence survives; the false
attribution does not. That is why this is a server-side resolution step and not
a rendering concern in the browser.

## 11. The prompt, and what it costs

Assembled in a fixed order: **system rules → passages → conversation →
question**. Prompt caching keys on an exact prefix match, so the stable parts go
first and a follow-up re-reads a cached prefix at a fraction of the input price.
Reordering those for readability would silently disable the discount, which is
why the assembly lives in one function and says so.

Every call is priced at write time into `usage_events`. Prices change; what a
call cost on the day it ran does not.

---

## Does it work?

`scripts/evaluate_retrieval.py` scores 23 hand-written cases against the
synthetic corpus. `--compare` ablates each stage, which is how these numbers
were arrived at rather than assumed:

```
23 cases, top-k 2

fusion only            recall@k  83%   MRR 0.778   role isolation 100%
+ exact-term boost     recall@k  83%   MRR 0.806   role isolation 100%
+ cross-encoder        recall@k  94%   MRR 0.917   role isolation 100%
+ both                 recall@k  94%   MRR 0.944   role isolation 100%
```

At `top-k 6` recall saturates at 100% and MRR runs 0.817 → 0.958. The corpus is
six documents, so these are not impressive numbers in absolute terms and are not
offered as such — what they are for is telling whether the next change to
chunking or ranking made things better or worse.

**Role isolation** is the row that would matter most if it moved. Those cases
name documents a role must never retrieve. Note that this is not the same as
expecting an empty result: sales cannot read the service manual but may
perfectly well retrieve the warranty policy for the same question.

### What the test suite asserts, and what it does not

The suite runs with a deterministic hashing embedder and a lexical reranker, so
a failure means the pipeline broke rather than that a model had an opinion. That
costs the ability to assert ranking quality, which is a property of the real
models — so ranking quality is *measured* by the script above and *not asserted*
in tests.

One limit worth stating, because it is the honest shape of the headline claim:
the keyword leg alone cannot separate `E-04` from `E-14`. The E-14 passage says
"Distinct from E-04 despite the similar code", so it contains the exact term a
search for `E-04` is looking for, and BM25 then ranks the two within a rounding
error and prefers whichever is shorter. It is the cross-encoder and the exact-
term ordering together that separate them. There is a test pinning that limit
down, rather than an assertion pretending it does not exist.

## What is not here yet

- **A semantic cache.** Near-duplicate questions should hit a cached answer
  keyed by query embedding rather than re-running the pipeline.
- **Streaming citation resolution.** Markers resolve once, at the end. Resolving
  as tokens arrive means parsing a marker that may still be half-written.
- **Query decomposition.** A two-part question is retrieved as one query.

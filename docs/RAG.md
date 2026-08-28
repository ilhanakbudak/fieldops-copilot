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

## What is not here yet

Milestone 2 is dense vector retrieval. The pipeline it plugs into is designed
around what comes next, which is why `chunks` already carries a generated
`tsvector` column and a GIN index on Postgres:

1. **Query analysis** — rewrite, extract filters, classify intent, on a cheap model
2. **Hybrid search** — vector *and* full-text, fused with reciprocal rank fusion
3. **Cross-encoder rerank** of the fused top-N
4. **Parent expansion** — already stored, not yet used at query time
5. **Structural citations** — markers resolved server-side to document, page and
   section, with unresolvable markers dropped rather than displayed

Step 2 is the one that makes `E-04` work reliably, and it is why the answer to
"which vector database" was Postgres.

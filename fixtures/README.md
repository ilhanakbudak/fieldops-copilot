# Fixtures

A synthetic corpus for a fictional water-treatment company, **Northgate Water
Systems**. Every fact in here is invented. No manufacturer's manual, no real
part number, no real price.

It is stored as Markdown rather than PDF on purpose: a PDF is opaque in a pull
request, and this corpus is something to review and argue with rather than
merely load. The ingestion pipeline reads Markdown natively, and
`scripts/build_fixture_pdfs.py` renders the same files to PDF when the PDF path
needs exercising.

Each file's front matter declares the audience the document is written for. That
is what `allowed_roles` is set from at seed time, and it is what the retrieval
query filters on — so this directory is also where the access-control demo comes
from:

| Document | Audience |
|---|---|
| `10-softener-service-manual.md` | technician |
| `15-softener-6800-service-manual.md` | technician |
| `20-radon-system-manual.md` | technician |
| `30-installation-sop.md` | technician, office |
| `40-warranty-policy.md` | technician, office, sales |
| `50-dealer-pricing.md` | sales |
| `60-water-problem-guide.md` | technician, office, sales |

Sign in as a technician and ask about dealer cost: nothing comes back, because
the pricing chunks were never in the candidate set. That is the whole security
argument, demonstrable in one query.

## The two softener manuals are near-copies on purpose

`10-` and `15-` document two models of the same product line, and they are the
hardest thing in this corpus to retrieve correctly. Both have a "Regeneration
Cycle" section and a "Resin Replacement" section. The procedures read almost
identically. The part numbers and the durations are different, and an answer
that quotes the wrong one is confidently, specifically wrong.

That is where retrieval actually fails in the field, and a corpus of six
documents about six unrelated subjects cannot show it — which is why the
evaluation scored 94% before the second manual existed and 83% after. See the
"Does it work?" section of [../docs/RAG.md](../docs/RAG.md).

## The customer book

`crm/customers.json` has three shapes in it that exist to make specific failures
reproducible:

- **Two households sharing a surname** (`NG-1042`, `NG-1119`), so a name search
  has to disambiguate on the town rather than guess.
- **Two accounts sharing a phone number** (`NG-1203`, `NG-1288` — one owner, two
  properties, the same line on both), so the caller-lookup screen pop has to
  offer a choice rather than open the first match. They carry the same name too,
  so the address is the only thing that tells them apart. See
  [../docs/CALLS.md](../docs/CALLS.md).
- **An account on hold with an overdue balance**, so the record a screen pop
  shows can carry something the person answering needs to know before they
  promise anything.

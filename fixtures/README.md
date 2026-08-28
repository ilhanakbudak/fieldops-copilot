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
| `20-radon-system-manual.md` | technician |
| `30-installation-sop.md` | technician, office |
| `40-warranty-policy.md` | technician, office, sales |
| `50-dealer-pricing.md` | sales |
| `60-water-problem-guide.md` | technician, office, sales |

Sign in as a technician and ask about dealer cost: nothing comes back, because
the pricing chunks were never in the candidate set. That is the whole security
argument, demonstrable in one query.

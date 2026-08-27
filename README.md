# FieldOps Copilot

A private AI assistant for service businesses — the office staff, installers, and
technicians who need an answer from a 400-page manual while a customer is on the
phone.

Retrieval-augmented chat over internal documents, live CRM and inventory lookups,
and a real-time call assistant that suggests answers to an agent mid-conversation.

> 🚧 **Under active development.** Built after
> [whatsapp-guest-concierge](https://github.com/ilhanakbudak/whatsapp-guest-concierge);
> see `docs/` as sections land.

---

## What it does

| Section | |
|---|---|
| **Company AI** | Ask questions against manuals, SOPs, warranty terms, and troubleshooting guides. Every answer cites the document and page it came from. |
| **Customer Search** | Live read-only lookups against the CRM — service history, installed equipment, job notes, estimates, invoices. |
| **Inventory** | Where a part is, how many are left, which supplier, which bin. |
| **Live Call Assistant** | Streams the call transcript, works out what the customer is actually asking, and puts the answer plus that customer's history on the agent's screen while they're still talking. |

## Stack

Next.js 15 · TypeScript · Python 3.13 · FastAPI · PostgreSQL + pgvector ·
hybrid retrieval with rank fusion and reranking · WebSockets · OpenAI

## Planned documentation

- `docs/RAG.md` — the retrieval pipeline and how it's evaluated
- `docs/SECURITY.md` — authentication, RBAC, and why role filtering happens inside the retrieval query
- `docs/COST.md` — how the AI bill is kept down
- `docs/DEPLOYMENT.md` — running it on your own infrastructure

## License

MIT

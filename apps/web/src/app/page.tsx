import type { HealthResponse } from "@fieldops/shared";

/**
 * Placeholder shell. The chat, customer search, inventory and live-call views
 * arrive with their milestones; this exists so the workspace builds and CI has
 * something real to verify.
 */
export default function Home() {
  const sections: Array<{ title: string; body: string; status: string }> = [
    {
      title: "Company AI",
      body: "Ask questions across manuals, SOPs and troubleshooting guides. Every answer cites the document and page it came from.",
      status: "Milestone 1",
    },
    {
      title: "Customer Search",
      body: "Read-only lookups against the CRM — service history, installed equipment, job notes, estimates.",
      status: "Milestone 2",
    },
    {
      title: "Inventory",
      body: "Where a part is, how many are left, which supplier, which bin.",
      status: "Milestone 3",
    },
    {
      title: "Live Call Assistant",
      body: "Streams the call transcript, works out what the customer is asking, and puts the answer plus their history on the agent's screen.",
      status: "Milestones 4–5",
    },
  ];

  return (
    <main style={{ maxWidth: 880, margin: "0 auto", padding: "48px 24px" }}>
      <h1 style={{ fontSize: 32, letterSpacing: "-0.02em", margin: 0 }}>FieldOps Copilot</h1>
      <p style={{ color: "var(--text-2)", fontSize: 17, marginTop: 8 }}>
        Private AI assistant for service businesses.
      </p>

      <div style={{ display: "grid", gap: 16, marginTop: 40 }}>
        {sections.map((section) => (
          <section
            key={section.title}
            style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              padding: 20,
            }}
          >
            <div style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
              <h2 style={{ fontSize: 17, margin: 0 }}>{section.title}</h2>
              <span style={{ fontSize: 12, color: "var(--text-2)" }}>{section.status}</span>
            </div>
            <p style={{ color: "var(--text-2)", margin: "8px 0 0", fontSize: 15 }}>
              {section.body}
            </p>
          </section>
        ))}
      </div>
    </main>
  );
}

export type { HealthResponse };

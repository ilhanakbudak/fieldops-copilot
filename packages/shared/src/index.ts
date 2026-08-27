/**
 * Types shared between the Next.js app and the FastAPI service.
 *
 * Hand-written for now. Once the API surface settles these are generated from
 * the FastAPI OpenAPI schema, so the two halves cannot drift.
 */

export type Role = "admin" | "office" | "sales" | "technician";

/** A retrieved passage, with enough provenance to render a real citation. */
export interface Citation {
  id: string;
  documentId: string;
  documentTitle: string;
  /** 1-indexed, as a reader would count. */
  page: number | null;
  section: string | null;
  snippet: string;
  score: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  createdAt: string;
}

export interface AskRequest {
  question: string;
  conversationId?: string;
}

export interface AskResponse {
  conversationId: string;
  message: ChatMessage;
  usage: TokenUsage;
}

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  cachedInputTokens: number;
  /** Computed server-side so the client never has to know model pricing. */
  estimatedCostUsd: number;
}

export interface HealthResponse {
  status: "ok";
  demoMode: boolean;
  vectorStore: "pgvector" | "sqlite-vec";
  embeddings: string;
  documents: number;
}

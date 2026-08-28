/**
 * Types shared between the Next.js app and the FastAPI service.
 *
 * Hand-written for now. Once the API surface settles these are generated from
 * the FastAPI OpenAPI schema, so the two halves cannot drift.
 */

export type Role = "admin" | "office" | "sales" | "technician";

/**
 * Verbs, not screens — the same list the API authorises against.
 *
 * The web app uses these to decide what to render. That is a convenience, not a
 * control: every one of them is enforced server-side, and hiding a button has
 * never stopped anyone from calling the endpoint behind it.
 */
export type Permission =
  | "documents:read"
  | "documents:manage"
  | "customers:read"
  | "inventory:read"
  | "pricing:read"
  | "calls:assist"
  | "users:manage"
  | "audit:read"
  | "cost:read";

export interface UserSummary {
  id: string;
  email: string;
  fullName: string;
  role: Role;
  isActive: boolean;
  lastLoginAt: string | null;
  createdAt: string | null;
}

export interface MeResponse {
  user: UserSummary;
  permissions: Permission[];
  /**
   * The document audiences this employee may read. Surfaced so the UI can say
   * "there are documents you cannot see" rather than leaving someone to
   * conclude the search is broken.
   */
  documentRoles: Role[];
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface CreateUserRequest {
  email: string;
  fullName: string;
  password: string;
  role: Role;
}

export interface UpdateUserRequest {
  role?: Role;
  isActive?: boolean;
}

/** Append-only. Nothing in the API edits or deletes one. */
export interface AuditEvent {
  id: string;
  occurredAt: string;
  actorEmail: string | null;
  actorRole: string | null;
  /** Dotted: `auth.login`, `authz.denied`, `connector.crm.get_customer`. */
  action: string;
  outcome: "success" | "denied" | "error";
  resourceType: string | null;
  resourceId: string | null;
  /** Ties this to every other record written while serving one request. */
  requestId: string | null;
  ip: string | null;
  detail: string | null;
}

export interface AuditPage {
  events: AuditEvent[];
  total: number;
}

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

/** The API's error envelope. Every failure has this shape. */
export interface ApiErrorBody {
  error: {
    code:
      | "bad_request"
      | "unauthenticated"
      | "forbidden"
      | "not_found"
      | "conflict"
      | "rate_limited";
    message: string;
    retryAfterSeconds?: number;
  };
}

export interface HealthResponse {
  status: "ok";
  demoMode: boolean;
  vectorStore: "pgvector" | "sqlite-vec";
  embeddings: string;
  documents: number;
}

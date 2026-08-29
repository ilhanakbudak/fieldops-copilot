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

/* --- Knowledge base ------------------------------------------------------ */

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface DocumentSummary {
  id: string;
  title: string;
  docType: string;
  sourceFilename: string;
  pageCount: number | null;
  chunkCount: number;
  /**
   * The employee audiences this document is written for.
   *
   * Also the filter the retrieval query runs on — the same list is denormalised
   * onto every chunk, so changing it here rewrites them.
   */
  allowedRoles: Role[];
  status: DocumentStatus;
  error: string | null;
  ingestedAt: string | null;
  createdAt: string | null;
}

export interface IngestResponse {
  document: DocumentSummary;
  chunks: number;
  pages: number;
  /** Pages that yielded no text and had no OCR to fall through to. */
  pagesWithoutText: number[];
  durationMs: number;
}

export interface SearchHit {
  chunkId: string;
  documentId: string;
  documentTitle: string;
  docType: string;
  content: string;
  page: number | null;
  section: string | null;
  /** Cosine similarity, −1 to 1. Higher is nearer. */
  score: number;
}

export interface SearchResponse {
  hits: SearchHit[];
  /** What the caller was permitted to search, so a thin result set is legibly
   *  a permission boundary rather than a broken index. */
  searchedRoles: Role[];
}

/* --- Chat ---------------------------------------------------------------- */

/**
 * A resolved citation.
 *
 * `marker` is what appears in the answer text — `[S1]`. It is assigned when the
 * passage is retrieved and resolved server-side before the response leaves the
 * API, so a marker reaching the browser always corresponds to a real passage. A
 * marker the model invented was removed from the text before it was sent.
 */
export interface Citation {
  marker: string;
  chunkId: string;
  documentId: string;
  documentTitle: string;
  /** 1-indexed, as a reader would count. */
  page: number | null;
  section: string | null;
  snippet: string;
}

/** A passage that was retrieved, whether or not the answer ended up citing it. */
export interface RetrievedSource {
  marker: string;
  documentId: string;
  documentTitle: string;
  page: number | null;
  section: string | null;
  snippet: string;
  score: number;
  /** Where each leg of the hybrid search ranked it. Empty for a leg that missed it. */
  ranks: Partial<Record<"vector" | "keyword", number>>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  createdAt: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  messages: ChatMessage[];
}

export interface TokenUsage {
  inputTokens: number;
  outputTokens: number;
  cachedInputTokens: number;
  /** Computed server-side so the client never has to know model pricing. */
  estimatedCostUsd: number;
}

/** One tool the agent decided to run, and what came back. */
export interface ToolRun {
  id: string;
  name: string;
  summary: string;
  ok: boolean;
  durationMs: number;
  /** Structured payload for the interface. The model never sees this. */
  data?: {
    customers?: CustomerSummary[];
    customer?: CustomerDetail;
    query?: string;
    candidates?: number;
    retrievalMs?: number;
    reranker?: string;
  };
}

/* --- Customers ------------------------------------------------------------ */

export interface CustomerSummary {
  id: string;
  name: string;
  phone: string;
  email: string;
  address: string;
  since: string;
  notes?: string;
}

export interface CustomerEquipment {
  id: string;
  model: string;
  serial: string;
  installedOn: string;
  location: string;
  warrantyUntil: string | null;
}

export interface CustomerJob {
  id: string;
  date: string;
  kind: string;
  summary: string;
  technician: string;
  notes: string;
  status: string;
}

export interface CustomerEstimate {
  id: string;
  date: string;
  summary: string;
  amountUsd: number;
  status: string;
}

export interface CustomerInvoice {
  id: string;
  date: string;
  amountUsd: number;
  balanceUsd: number;
  status: string;
}

export interface CustomerDetail extends CustomerSummary {
  equipment: CustomerEquipment[];
  jobs: CustomerJob[];
  estimates: CustomerEstimate[];
  invoices: CustomerInvoice[];
  outstandingUsd: number;
}

/** The full record, as the customers API returns it. */
export interface CustomerRecord {
  customer: CustomerSummary;
  equipment: CustomerEquipment[];
  jobs: CustomerJob[];
  estimates: CustomerEstimate[];
  invoices: CustomerInvoice[];
  outstandingUsd: number;
}

/* --- Caller lookup -------------------------------------------------------- */

export interface InboundCall {
  callId: string;
  /** As the phone system sent it, because it is what gets read back to the
   *  caller. The normalised form is a matching key and never a display one. */
  fromNumber: string;
  toNumber: string;
  receivedAt: string;
}

/**
 * What lands on the screen when the phone rings.
 *
 * Three shapes in one message, and telling them apart is the whole job of the
 * page that renders it:
 *
 *   `matches` empty — nobody in the CRM has this number. A new customer, or a
 *   withheld one. The pop still appears; the number is the useful part.
 *
 *   one match with `detail` — the ordinary case. Equipment, history and notes
 *   are on screen before the handset reaches an ear.
 *
 *   more than one match, `detail` null — two accounts share this line. Nothing
 *   is opened, because a wrong record never looks uncertain while somebody is
 *   reading it aloud.
 */
export interface ScreenPop {
  call: InboundCall;
  matches: CustomerSummary[];
  detail: CustomerRecord | null;
}

export interface CallDemoNumber {
  number: string;
  label: string;
}

/** Demo mode only: what the browser needs to post itself a webhook. */
export interface CallDemo {
  token: string;
  header: string;
  numbers: CallDemoNumber[];
}

/* --- Inventory ------------------------------------------------------------ */

export interface StockLocation {
  warehouse: string;
  aisle: string;
  row: string;
  bin: string;
  onHand: number;
  /** Reserved against scheduled jobs. */
  committed: number;
  available: number;
}

export interface MaterialPricing {
  supplier: string;
  supplierSku: string;
  leadTimeDays: number;
  costUsd: number;
  listUsd: number;
}

export interface Material {
  id: string;
  sku: string;
  name: string;
  category: string;
  unit: string;
  available: number;
  reorderPoint: number;
  belowReorder: boolean;
  stock: StockLocation[];
  /**
   * `null` for a caller without `pricing:read` — withheld, not zero. A UI that
   * renders a missing price as $0.00 is worse than one that renders nothing.
   */
  pricing: MaterialPricing | null;
}

/** The `sources` event: what retrieval found, sent before any answer text. */
export interface SourcesEvent {
  sources: RetrievedSource[];
}

/** The `done` event: the finished answer, its citations and what it cost. */
export interface DoneEvent {
  conversationId: string;
  messageId: string;
  text: string;
  citations: Citation[];
  tools: Array<Pick<ToolRun, "name" | "summary" | "ok" | "durationMs">>;
  usage: TokenUsage;
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
  vectorStore: "pgvector" | "sqlite";
  embeddings: string;
  documents: number;
}

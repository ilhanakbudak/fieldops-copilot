import type {
  ApiErrorBody,
  CallDemo,
  CostReport,
  CreateUserRequest,
  CustomerRecord,
  CustomerSummary,
  Material,
  ConversationDetail,
  ConversationSummary,
  DocumentSummary,
  IngestResponse,
  MeResponse,
  Role,
  SearchResponse,
  UpdateUserRequest,
  UserSummary,
} from "@fieldops/shared";

/**
 * Every call goes to `/api/*` on this origin, which `next.config.ts` proxies to
 * the FastAPI service.
 *
 * Same-origin is the point rather than a convenience: the session cookie is
 * HttpOnly, so no script can attach it by hand, and it only travels if the
 * request looks like it belongs to this site. A cross-origin API would mean
 * CORS with credentials and a cookie that can no longer be SameSite=Lax — which
 * is trading a real defence for a deployment preference.
 */
export class ApiError extends Error {
  constructor(
    readonly code: ApiErrorBody["error"]["code"] | "network",
    message: string,
    readonly status: number,
    readonly retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers:
        // FormData sets its own multipart boundary. Setting a content-type by
        // hand here would produce a header without one and the upload fails
        // with a confusing 422.
        init?.body instanceof FormData
          ? init.headers
          : { "content-type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError("network", "Cannot reach the API. Is it running on port 8000?", 0);
  }

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    const error = (body as ApiErrorBody | null)?.error;
    throw new ApiError(
      error?.code ?? "bad_request",
      error?.message ?? `Request failed (${response.status}).`,
      response.status,
      error?.retryAfterSeconds,
    );
  }

  return body as T;
}

export const api = {
  me: () => request<MeResponse>("/auth/me"),

  login: (email: string, password: string) =>
    request<MeResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  logout: () => request<void>("/auth/logout", { method: "POST" }),

  documents: () => request<DocumentSummary[]>("/documents"),

  upload: (input: { file: File; title: string; docType: string; roles: Role[] }) => {
    const form = new FormData();
    form.append("file", input.file);
    form.append("title", input.title);
    form.append("doc_type", input.docType);
    // Repeated field rather than a JSON array: the endpoint takes a form, and
    // repetition is how a form expresses a list.
    for (const role of input.roles) form.append("allowed_roles", role);
    return request<IngestResponse>("/documents", { method: "POST", body: form });
  },

  retag: (id: string, roles: Role[]) =>
    request<DocumentSummary>(`/documents/${id}/roles`, {
      method: "PATCH",
      body: JSON.stringify({ allowedRoles: roles }),
    }),

  remove: (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" }),

  conversations: () => request<ConversationSummary[]>("/chat/conversations"),

  conversation: (id: string) => request<ConversationDetail>(`/chat/conversations/${id}`),

  deleteConversation: (id: string) =>
    request<void>(`/chat/conversations/${id}`, { method: "DELETE" }),

  clearConversations: () => request<void>("/chat/conversations", { method: "DELETE" }),

  findCustomers: (query: string) =>
    request<CustomerSummary[]>(`/customers?q=${encodeURIComponent(query)}`),

  customer: (id: string) => request<CustomerRecord>(`/customers/${id}`),

  findMaterials: (query: string) =>
    request<Material[]>(`/inventory?q=${encodeURIComponent(query)}`),

  /** Demo mode only, and signed in: the token forges a screen pop for anybody
   *  who holds it. See services/api/app/api/routes/calls.py. */
  callDemo: () => request<CallDemo>("/calls/demo"),

  costs: (days: number) => request<CostReport>(`/admin/costs?days=${days}`),

  users: () => request<UserSummary[]>("/admin/users"),

  createUser: (input: CreateUserRequest) =>
    request<UserSummary>("/admin/users", { method: "POST", body: JSON.stringify(input) }),

  updateUser: (id: string, patch: UpdateUserRequest) =>
    request<UserSummary>(`/admin/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  search: (query: string, limit = 10) =>
    request<SearchResponse>(
      `/documents/search?q=${encodeURIComponent(query)}&limit=${limit}`,
    ),
};

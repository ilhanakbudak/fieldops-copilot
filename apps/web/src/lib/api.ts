import type { ApiErrorBody, MeResponse } from "@fieldops/shared";

/**
 * Calls go to `/api/*` on this origin, which `next.config.ts` proxies to the
 * FastAPI service. Same-origin is the point: the session cookie is HttpOnly, so
 * it has to travel by itself, and a cross-origin call would need CORS plus
 * credentials plus a cookie that is no longer SameSite=Lax.
 */
export class ApiError extends Error {
  constructor(
    readonly code: ApiErrorBody["error"]["code"] | "network",
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError("network", "The API is not reachable. Is it running on :8000?", 0);
  }

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (body as ApiErrorBody | null)?.error;
    throw new ApiError(error?.code ?? "bad_request", error?.message ?? "Request failed.", response.status);
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
};

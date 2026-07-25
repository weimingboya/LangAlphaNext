import type { AuthSession, PublicConfig } from "../../domain/types";

const SESSION_KEY = "langalpha.session.v1";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function readStoredSession(): AuthSession | null {
  try {
    const value = window.localStorage.getItem(SESSION_KEY);
    return value ? (JSON.parse(value) as AuthSession) : null;
  } catch {
    window.localStorage.removeItem(SESSION_KEY);
    return null;
  }
}

function writeAccessCookie(accessToken: string | null): void {
  if (!accessToken) {
    document.cookie = "langalpha_access_token=; Path=/; Max-Age=0; SameSite=Lax";
    return;
  }
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `langalpha_access_token=${encodeURIComponent(
    accessToken,
  )}; Path=/; SameSite=Lax${secure}`;
}

export function persistSession(session: AuthSession | null): AuthSession | null {
  if (!session) {
    window.localStorage.removeItem(SESSION_KEY);
    writeAccessCookie(null);
    return null;
  }
  const normalized = {
    ...session,
    expires_at:
      session.expires_at ||
      Math.floor(Date.now() / 1000) + Number(session.expires_in || 3600),
  };
  window.localStorage.setItem(SESSION_KEY, JSON.stringify(normalized));
  writeAccessCookie(normalized.access_token);
  return normalized;
}

export async function fetchPublicConfig(): Promise<PublicConfig> {
  const response = await fetch("/api/config");
  if (!response.ok) throw new Error("Public configuration is unavailable");
  return (await response.json()) as PublicConfig;
}

export async function requestSupabaseSession(
  config: PublicConfig,
  path: string,
  body: Record<string, string>,
): Promise<AuthSession> {
  const response = await fetch(`${config.supabase_url}/auth/v1/${path}`, {
    method: "POST",
    headers: {
      apikey: config.supabase_publishable_key,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const payload = (await response.json()) as Record<string, unknown>;
  if (!response.ok) {
    const message =
      payload.msg ||
      payload.message ||
      payload.error_description ||
      payload.error ||
      "Sign in failed";
    throw new ApiError(String(message), response.status);
  }
  return payload as unknown as AuthSession;
}

export class ApiClient {
  private refreshPromise: Promise<string | null> | null = null;

  constructor(
    private readonly config: PublicConfig,
    private readonly getSession: () => AuthSession | null,
    private readonly updateSession: (session: AuthSession | null) => void,
  ) {}

  private async accessToken(): Promise<string | null> {
    const session = this.getSession();
    if (!session?.access_token) return null;
    if (Number(session.expires_at || 0) * 1000 > Date.now() + 60_000) {
      return session.access_token;
    }
    if (!session.refresh_token) return null;
    if (!this.refreshPromise) {
      this.refreshPromise = requestSupabaseSession(
        this.config,
        "token?grant_type=refresh_token",
        { refresh_token: session.refresh_token },
      )
        .then((refreshed) => {
          this.updateSession(refreshed);
          return refreshed.access_token;
        })
        .catch((error: unknown) => {
          this.updateSession(null);
          throw error;
        })
        .finally(() => {
          this.refreshPromise = null;
        });
    }
    return this.refreshPromise;
  }

  async request<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
    const token = await this.accessToken();
    const headers = new Headers(options.headers || {});
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(path, { ...options, headers });
    if (response.status === 401 && retry && this.getSession()?.refresh_token) {
      const session = this.getSession();
      if (session) this.updateSession({ ...session, expires_at: 0 });
      await this.accessToken();
      return this.request<T>(path, options, false);
    }
    if (!response.ok) {
      const detail = await response.text();
      throw new ApiError(detail || `Request failed (${response.status})`, response.status);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }
}

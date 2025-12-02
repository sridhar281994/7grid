function resolveApiBase(): string {
  const envBase = import.meta.env.VITE_API_BASE;
  if (envBase && envBase.trim().length > 0) {
    return envBase.replace(/\/$/, "");
  }
  if (typeof window !== "undefined") {
    const { protocol, hostname, port } = window.location;
    const guessedHost = hostname.startsWith("wallet.")
      ? hostname.replace("wallet.", "api.")
      : hostname;
    const portPart = port ? `:${port}` : "";
    return `${protocol}//${guessedHost}${portPart}`;
  }
  return "";
}

const API_BASE = resolveApiBase();
let refreshPromise: Promise<string | null> | null = null;

async function apiFetch<T = any>(
  path: string,
  options: RequestInit = {},
  allowRefresh = true
): Promise<T> {
  const token = localStorage.getItem("wallet_jwt");
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include"
  });

  if (res.status === 401 && allowRefresh) {
    await res.text().catch(() => undefined);
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return apiFetch<T>(path, options, false);
    }
  }

  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function refreshAccessToken(): Promise<string | null> {
  if (!API_BASE) return null;
  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const res = await fetch(`${API_BASE}/wallet-portal/sessions/refresh`, {
          method: "POST",
          credentials: "include"
        });
        if (!res.ok) {
          await res.text().catch(() => undefined);
          localStorage.removeItem("wallet_jwt");
          return null;
        }
        const data: BridgeSessionResponse = await res.json();
        if (data?.access_token) {
          localStorage.setItem("wallet_jwt", data.access_token);
          return data.access_token;
        }
        localStorage.removeItem("wallet_jwt");
        return null;
      } catch {
        return null;
      } finally {
        refreshPromise = null;
      }
    })();
  }
  return refreshPromise;
}

type BridgeSessionResponse = {
  ok: boolean;
  user_id: number;
  access_token: string;
};

export async function bridgeSession(linkToken: string) {
  const res = await apiFetch<BridgeSessionResponse>("/wallet-portal/sessions/bridge", {
    method: "POST",
    body: JSON.stringify({ token: linkToken })
  });
  if (!res?.access_token) {
    throw new Error("Wallet session created without access token.");
  }
  localStorage.setItem("wallet_jwt", res.access_token);
  return res;
}

export default apiFetch;

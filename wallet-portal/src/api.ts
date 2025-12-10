const DEFAULT_API_BASE = "https://api.srtech.co.in";
const TOKEN_STORAGE_KEY = "wallet_jwt";

function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

function setStoredToken(token: string) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    // Ignore storage errors (e.g., privacy mode)
  }
}

export function clearStoredToken() {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // Ignore storage errors
  }
}

function resolveApiBase(): string {
  const envBase = import.meta.env.VITE_API_BASE;
  if (envBase && envBase.trim().length > 0) {
    return envBase.replace(/\/$/, "");
  }

  if (typeof window !== "undefined") {
    const { protocol, hostname, port } = window.location;
    const portPart = port ? `:${port}` : "";
    const currentOrigin = `${protocol}//${hostname}${portPart}`.replace(/\/$/, "");

    if (hostname.startsWith("wallet.")) {
      const guessedHost = hostname.replace("wallet.", "api.");
      return `${protocol}//${guessedHost}${portPart}`.replace(/\/$/, "");
    }

    return currentOrigin;
  }

  return DEFAULT_API_BASE;
}

const API_BASE = resolveApiBase();
let refreshPromise: Promise<string | null> | null = null;

async function apiFetch<T = any>(
  path: string,
  options: RequestInit = {},
  allowRefresh = true
): Promise<T> {
  const token = getStoredToken();
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include"
  });

  let consumedBody: string | null = null;
  async function readBodyText() {
    if (consumedBody !== null) return consumedBody;
    try {
      consumedBody = await res.text();
    } catch {
      consumedBody = "";
    }
    return consumedBody;
  }

  if (res.status === 401 && allowRefresh) {
    await readBodyText();
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return apiFetch<T>(path, options, false);
    }
  }

  if (!res.ok) {
    const bodyText = await readBodyText();
    let message = bodyText;
    try {
      const parsed = JSON.parse(bodyText);
      const detail = parsed?.detail;
      if (Array.isArray(detail)) {
        message =
          detail
            .map((item: any) => item?.msg || item?.detail || JSON.stringify(item))
            .join(" • ") || bodyText;
      } else {
        const firstError =
          Array.isArray(parsed?.errors) && parsed.errors.length > 0
            ? parsed.errors[0]
            : parsed?.errors;
        message =
          detail ||
          parsed?.message ||
          parsed?.error ||
          firstError ||
          bodyText;
      }
    } catch {
      // Ignore JSON parse errors
    }
    throw new Error(message || `Request failed (${res.status})`);
  }
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
          clearStoredToken();
          return null;
        }
        const data: BridgeSessionResponse = await res.json();
        if (data?.access_token) {
          setStoredToken(data.access_token);
          return data.access_token;
        }
        clearStoredToken();
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
  setStoredToken(res.access_token);
  return res;
}

export default apiFetch;

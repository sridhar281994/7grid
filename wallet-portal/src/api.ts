const API_BASE = import.meta.env.VITE_API_BASE ?? "https://api.example.com";

async function apiFetch<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem("wallet_jwt");
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include"
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
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

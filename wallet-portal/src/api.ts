const API_BASE = import.meta.env.VITE_API_BASE ?? "https://api.example.com";

async function apiFetch(path: string, options: RequestInit = {}) {
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

export async function bridgeSession(linkToken: string) {
  await apiFetch("/wallet-portal/sessions/bridge", {
    method: "POST",
    body: JSON.stringify({ token: linkToken })
  });
  // TODO: fetch JWT or rely on backend cookie; we’ll extend later.
}

export default apiFetch;

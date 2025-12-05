import { ReactNode, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { bridgeSession, clearStoredToken } from "../api";

type Props = {
  children: ReactNode;
};

type Status = "ready" | "linking" | "error" | "checking";

type RouterLocation = {
  pathname: string;
  search: string;
  hash: string;
};

type LinkTokenData = {
  token: string;
  cleanedSearch: string;
  cleanedHash: string;
  targetPathname?: string;
};

function normalizePath(pathname: string | undefined): string | undefined {
  if (!pathname) return undefined;
  return pathname.startsWith("/") ? pathname : `/${pathname}`;
}

function extractLinkToken(location: RouterLocation): LinkTokenData | null {
  const searchParams = new URLSearchParams(location.search);
  const searchToken = searchParams.get("token");
  if (searchToken) {
    searchParams.delete("token");
    return {
      token: searchToken,
      cleanedSearch: searchParams.toString(),
      cleanedHash: location.hash,
      targetPathname: location.pathname,
    };
  }

  const rawHash = location.hash.startsWith("#")
    ? location.hash.slice(1)
    : location.hash;
  if (!rawHash) return null;

  const [hashPathPart, hashQueryPart = ""] = rawHash.split("?");
  const hashParams = new URLSearchParams(hashQueryPart);
  const hashToken = hashParams.get("token");
  if (!hashToken) return null;

  hashParams.delete("token");
  const cleanedHashParams = hashParams.toString();
  const cleanedHashPath = hashPathPart || "";
  const cleanedHash =
    cleanedHashPath || cleanedHashParams
      ? `#${cleanedHashPath}${cleanedHashParams ? `?${cleanedHashParams}` : ""}`
      : "";

  return {
    token: hashToken,
    cleanedSearch: location.search.replace(/^\?/, ""),
    cleanedHash,
    targetPathname: normalizePath(cleanedHashPath) || location.pathname,
  };
}

/**
 * Intercepts any `?token=...` query parameter (from the native app deep link)
 * and ensures we exchange it for a wallet session before rendering the portal.
 */
const NO_SESSION_MESSAGE =
  "Missing wallet session. Open the portal from the SR Tech app or use a valid wallet link.";

export default function WalletSessionGate({ children }: Props) {
  const location = useLocation();
  const navigate = useNavigate();
  const [status, setStatus] = useState<Status>("checking");
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const [sessionReady, setSessionReady] = useState(false);

  const linkTokenData = useMemo(() => extractLinkToken(location), [location]);

  useEffect(() => {
    if (!linkTokenData) return;

    let cancelled = false;
    setStatus("linking");
    setError(null);

    bridgeSession(linkTokenData.token)
      .then(() => {
        if (cancelled) return;
        setSessionReady(true);
        setStatus("ready");
        navigate(
          {
            pathname: linkTokenData.targetPathname ?? location.pathname,
            search: linkTokenData.cleanedSearch
              ? `?${linkTokenData.cleanedSearch}`
              : "",
            hash: linkTokenData.cleanedHash,
          },
          { replace: true }
        );
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message || "Failed to create wallet session.");
        setStatus("error");
      });

    return () => {
      cancelled = true;
    };
    // re-run when user requests a retry
  }, [location, navigate, retryKey, linkTokenData]);

  useEffect(() => {
    if (linkTokenData) return;
    if (sessionReady) {
      setStatus("ready");
      setError(null);
      return;
    }
    clearStoredToken();
    setError(NO_SESSION_MESSAGE);
    setStatus("error");
  }, [linkTokenData, retryKey, sessionReady]);

  if (status === "linking" || status === "checking") {
    return (
      <div className="page-card">
        <h1>Wallet</h1>
        <p>
          {status === "linking"
            ? "Connecting to your wallet…"
            : "Preparing your wallet session…"}
        </p>
      </div>
    );
  }

  if (status === "error") {
    return (
      <div className="page-card">
        <h1>Wallet</h1>
        <p>Error: {error}</p>
        <button type="button" onClick={() => setRetryKey((key) => key + 1)}>
          Try again
        </button>
      </div>
    );
  }

  return <>{children}</>;
}

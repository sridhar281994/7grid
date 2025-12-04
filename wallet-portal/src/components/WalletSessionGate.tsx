import { ReactNode, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { bridgeSession } from "../api";

type Props = {
  children: ReactNode;
};

type Status = "ready" | "linking" | "error";

/**
 * Intercepts any `?token=...` query parameter (from the native app deep link)
 * and ensures we exchange it for a wallet session before rendering the portal.
 */
export default function WalletSessionGate({ children }: Props) {
  const location = useLocation();
  const navigate = useNavigate();
  const [status, setStatus] = useState<Status>("ready");
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const linkToken = params.get("token");
    if (!linkToken) {
      setStatus("ready");
      setError(null);
      return;
    }

    let cancelled = false;
    setStatus("linking");
    setError(null);

    bridgeSession(linkToken)
      .then(() => {
        if (cancelled) return;
        params.delete("token");
        const nextSearch = params.toString();
        navigate(
          { pathname: location.pathname, search: nextSearch ? `?${nextSearch}` : "" },
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
  }, [location, navigate, retryKey]);

  if (status === "linking") {
    return (
      <div className="page-card">
        <h1>Wallet</h1>
        <p>Connecting to your wallet…</p>
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

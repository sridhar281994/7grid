import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { bridgeSession } from "../api";

export default function LinkPage() {
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("token");
    if (!token) {
      setError("Missing wallet link token.");
      return;
    }
    bridgeSession(token)
      .then(() => navigate("/wallet/dashboard"))
      .catch((err) =>
        setError(err.message || "Failed to create wallet session.")
      );
  }, [navigate]);

  if (error) return <div>{error}</div>;
  return <div>Connecting to wallet...</div>;
}

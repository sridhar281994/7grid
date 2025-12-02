import { useEffect, useState } from "react";
import apiFetch from "../api";

export default function Dashboard() {
  const [balance, setBalance] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch("/wallet/balance")
      .then((res) => setBalance(res.balance))
      .catch((err) => setError(err.message || "Failed to load balance."));
  }, []);

  if (error) return <div>Error: {error}</div>;
  if (balance === null) return <div>Loading wallet...</div>;

  return (
    <div style={{ padding: "24px" }}>
      <h1>Wallet</h1>
      <p>Balance: {balance.toFixed(2)} coins</p>
      <p>
        <a href="/wallet/recharge">Recharge</a> ·{" "}
        <a href="/wallet/withdraw">Withdraw</a> ·{" "}
        <a href="/wallet/history">History</a>
      </p>
    </div>
  );
}

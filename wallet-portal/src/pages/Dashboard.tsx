import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import apiFetch from "../api";

type BalanceResponse = { balance: number };

export default function Dashboard() {
  const [balance, setBalance] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch("/wallet/balance")
      .then((res: BalanceResponse) => setBalance(res.balance))
      .catch((err) => setError(err.message || "Failed to load balance."));
  }, []);

  if (error) return <PageCard title="Wallet" body={<p>Error: {error}</p>} />;
  if (balance === null) return <PageCard title="Wallet" body={<p>Loading...</p>} />;

  return (
    <PageCard
      title="Wallet"
      body={
        <>
          <p className="wallet-subtitle">Manage your SR Tech coins with confidence.</p>
          <div className="wallet-balance">
            <span className="wallet-balance-amount">{balance.toFixed(2)}</span>
            <span className="wallet-balance-label">coins</span>
          </div>
          <div className="wallet-actions">
            <Link className="wallet-action primary" to="/wallet/recharge">
              <span className="wallet-action-title">Recharge</span>
              <span className="wallet-action-copy">Add coins instantly via UPI</span>
            </Link>
            <Link className="wallet-action secondary" to="/wallet/withdraw">
              <span className="wallet-action-title">Withdraw</span>
              <span className="wallet-action-copy">Send winnings to your bank</span>
            </Link>
            <Link className="wallet-action ghost" to="/wallet/history">
              <span className="wallet-action-title">History</span>
              <span className="wallet-action-copy">Track every transaction</span>
            </Link>
          </div>
        </>
      }
    />
  );
}

function PageCard({ title, body }: { title: string; body: ReactNode }) {
  return (
    <div className="page-card">
      <h1>{title}</h1>
      {body}
    </div>
  );
}

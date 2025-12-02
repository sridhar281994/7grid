import { useEffect, useState } from "react";
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
          <p className="balance">{balance.toFixed(2)} coins</p>
          <div className="actions">
            <Link to="/wallet/recharge">Recharge</Link>
            <Link to="/wallet/withdraw">Withdraw</Link>
            <Link to="/wallet/history">History</Link>
          </div>
        </>
      }
    />
  );
}

function PageCard({ title, body }: { title: string; body: React.ReactNode }) {
  return (
    <div className="page-card">
      <h1>{title}</h1>
      {body}
    </div>
  );
}

import { useEffect, useState } from "react";
import apiFetch from "../api";

type Tx = {
  id: number;
  amount: number;
  type: string;
  status: string;
  timestamp?: string;
  note?: string;
};

export default function History() {
  const [rows, setRows] = useState<Tx[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch("/wallet/history?limit=20")
      .then((res) => setRows(res))
      .catch((err) => setError(err.message || "Failed to load history."));
  }, []);

  if (error) return <div className="page-card">Error: {error}</div>;

  return (
    <div className="page-card">
      <h1>Wallet History</h1>
      {rows.length === 0 ? (
        <p>No transactions yet.</p>
      ) : (
        <table className="history-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Type</th>
              <th>Amount</th>
              <th>Status</th>
              <th>Note</th>
              <th>Timestamp</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((tx) => (
              <tr key={tx.id}>
                <td>{tx.id}</td>
                <td>{tx.type}</td>
                <td>{tx.amount}</td>
                <td>{tx.status}</td>
                <td>{tx.note || "-"}</td>
                <td>{tx.timestamp ? tx.timestamp.slice(0, 16) : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

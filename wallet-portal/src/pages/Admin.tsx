import { useEffect, useState } from "react";
import apiFetch from "../api";

type Withdrawal = {
  id: number;
  user_id: number;
  amount: number;
  method: string;
  account: string;
  status: string;
  created_at?: string;
};

export default function Admin() {
  const [rows, setRows] = useState<Withdrawal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function refresh() {
    try {
      const res = await apiFetch("/admin/wallet/withdrawals/pending");
      setRows(res.withdrawals || []);
    } catch (err: any) {
      setError(err.message || "Failed to load queue.");
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function approve(id: number) {
    try {
      await apiFetch(`/admin/wallet/withdrawals/${id}/approve`, { method: "POST" });
      setMessage(`Withdrawal ${id} approved.`);
      setRows((rows) => rows.filter((row) => row.id !== id));
    } catch (err: any) {
      setMessage(err.message || "Approve failed.");
    }
  }

  async function reject(id: number) {
    const reason = prompt("Reason for rejection?", "Rejected by admin");
    if (!reason) return;
    try {
      await apiFetch(`/admin/wallet/withdrawals/${id}/reject`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      setMessage(`Withdrawal ${id} rejected.`);
      setRows((rows) => rows.filter((row) => row.id !== id));
    } catch (err: any) {
      setMessage(err.message || "Reject failed.");
    }
  }

  if (error) return <div className="page-card">Error: {error}</div>;

  return (
    <div className="page-card">
      <h1>Admin — Pending Withdrawals</h1>
      {message && <p>{message}</p>}
      {rows.length === 0 ? (
        <p>No pending withdrawals.</p>
      ) : (
        <table className="history-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>User</th>
              <th>Amount</th>
              <th>Method</th>
              <th>Account</th>
              <th>Created</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.id}</td>
                <td>{row.user_id}</td>
                <td>{row.amount}</td>
                <td>{row.method}</td>
                <td>{row.account}</td>
                <td>{row.created_at ? row.created_at.slice(0, 16) : "-"}</td>
                <td>
                  <button onClick={() => approve(row.id)}>Approve</button>
                  <button onClick={() => reject(row.id)}>Reject</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

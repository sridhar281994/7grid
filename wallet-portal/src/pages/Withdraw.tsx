import { useState } from "react";
import apiFetch from "../api";

type Method = "upi" | "paypal";

export default function Withdraw() {
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<Method>("upi");
  const [account, setAccount] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("Submitting request...");
    try {
      const payload = {
        amount: Number(amount),
        method,
        account,
      };
      const res = await apiFetch("/wallet-portal/withdraw", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setStatus(`Request queued (ID ${res.withdrawal_id}). Admin will process soon.`);
    } catch (err: any) {
      setStatus(err.message || "Withdrawal failed.");
    }
  }

  return (
    <div className="page-card">
      <h1>Withdraw</h1>
      <form onSubmit={submit}>
        <label>
          Amount (coins)
          <input
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
            type="number"
            min="1"
          />
        </label>
        <label>
          Method
          <select value={method} onChange={(e) => setMethod(e.target.value as Method)}>
            <option value="upi">UPI</option>
            <option value="paypal">PayPal</option>
          </select>
        </label>
        <label>
          Account (UPI ID or PayPal email)
          <input
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            required
          />
        </label>
        <button type="submit">Request Withdrawal</button>
      </form>
      {status && <p>{status}</p>}
    </div>
  );
}

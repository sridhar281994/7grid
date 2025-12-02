import { useState } from "react";
import apiFetch from "../api";

export default function Recharge() {
  const [amount, setAmount] = useState("");
  const [status, setStatus] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("Creating payment link...");
    try {
      const payload = { amount: Number(amount) };
      const res = await apiFetch("/wallet-portal/recharge", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const shortUrl = res.short_url || res.payment_link || res.payment_link_id;
      if (!shortUrl) {
        setStatus("No payment link returned. Contact support.");
        return;
      }
      window.open(shortUrl, "_blank");
      setStatus("Payment link opened. Complete payment to credit coins.");
    } catch (err: any) {
      setStatus(err.message || "Failed to create recharge.");
    }
  }

  return (
    <div className="page-card">
      <h1>Recharge</h1>
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
        <button type="submit">Create Payment Link</button>
      </form>
      {status && <p>{status}</p>}
    </div>
  );
}

import { useMemo, useState } from "react";
import apiFetch from "../api";
import usePortalProfile from "../hooks/usePortalProfile";

export default function Recharge() {
  const [amount, setAmount] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const { profile, loading, error } = usePortalProfile();
  const minRecharge = useMemo(() => profile?.limits.recharge_inr_min ?? 5, [profile]);
  const numericAmount = Number(amount);
  const meetsMin = !Number.isNaN(numericAmount) && numericAmount >= minRecharge;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!meetsMin) {
      setStatus(`Minimum recharge is ₹${minRecharge.toFixed(2)}.`);
      return;
    }
    setStatus("Creating payment link...");
    try {
      const payload = { amount: numericAmount };
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
      {loading && <p>Loading wallet settings…</p>}
      {error && <p>Wallet settings error: {error}</p>}
      {profile?.payout.admin_upi_id && (
        <p>Official UPI ID: {profile.payout.admin_upi_id}</p>
      )}
      <p>Minimum recharge: ₹{minRecharge.toFixed(2)}</p>
      <form onSubmit={submit}>
        <label>
          Amount (₹)
          <input
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
            type="number"
            min={minRecharge}
            step="0.01"
          />
        </label>
        <button type="submit" disabled={!meetsMin}>
          Create Payment Link
        </button>
      </form>
      {status && <p>{status}</p>}
    </div>
  );
}

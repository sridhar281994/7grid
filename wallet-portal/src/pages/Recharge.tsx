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
  const coinsPerInr = profile?.conversion?.coins_per_inr ?? 1;
  const coinsPreview =
    !Number.isNaN(numericAmount) && numericAmount > 0
      ? numericAmount * coinsPerInr
      : null;
  const walletBalance = profile?.user.wallet_balance ?? 0;
  const userUpi = profile?.user.upi_id?.trim();
  const userPaypal = profile?.user.paypal_id?.trim();

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
      {profile && (
        <div className="info-panel">
          <p>
            Coins per ₹1: <strong>{coinsPerInr.toFixed(2)}</strong>
          </p>
          <p>
            Current balance: <strong>{walletBalance.toFixed(2)} coins</strong>
          </p>
          <p>
            Your UPI ID on file:{" "}
            <strong>{userUpi || "Add this inside the SR Tech app"}</strong>
          </p>
          {userPaypal && (
            <p>
              Your PayPal ID on file: <strong>{userPaypal}</strong>
            </p>
          )}
        </div>
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
      {coinsPreview !== null && (
        <p>Estimated coins added: {coinsPreview.toFixed(2)} coins.</p>
      )}
      {status && <p>{status}</p>}
    </div>
  );
}

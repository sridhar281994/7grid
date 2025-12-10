import { useEffect, useMemo, useState } from "react";
import apiFetch from "../api";
import usePortalProfile from "../hooks/usePortalProfile";

type Method = "upi" | "paypal";

export default function Withdraw() {
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<Method>("upi");
  const [status, setStatus] = useState<string | null>(null);
  const { profile, loading, error, refresh } = usePortalProfile();

  const availableMethods = useMemo<Method[]>(() => {
    if (!profile) return [];
    const methods: Method[] = [];
    if (profile.payout.has_upi_details) methods.push("upi");
    if (profile.payout.has_paypal_details && profile.payout.paypal_enabled) {
      methods.push("paypal");
    }
    return methods;
  }, [profile]);

  useEffect(() => {
    if (!profile) return;
    if (availableMethods.length === 0) return;
    if (!availableMethods.includes(method)) {
      setMethod(availableMethods[0]);
    }
  }, [availableMethods, method, profile]);

  const accountValue =
    method === "upi" ? profile?.user.upi_id ?? "" : profile?.user.paypal_id ?? "";
  const minAmount =
    method === "upi"
      ? profile?.limits.withdraw_inr_min ?? 5
      : profile?.limits.withdraw_usd_min ?? 0.9;
  const numericAmount = Number(amount);
  const meetsMin = !Number.isNaN(numericAmount) && numericAmount >= minAmount;
  const currencyLabel = method === "upi" ? "₹" : profile?.payout.paypal_currency || "USD";
  const formattedMinAmount =
    method === "upi"
      ? `₹${minAmount.toFixed(2)}`
      : `${currencyLabel} ${minAmount.toFixed(2)}`;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!accountValue) {
      setStatus("Add your payout details in the SR Tech app first.");
      return;
    }
    if (!meetsMin) {
      setStatus(
        `Minimum ${method === "upi" ? "UPI" : "PayPal"} withdrawal is ${formattedMinAmount}.`
      );
      return;
    }
    setStatus("Submitting request...");
    try {
      const payload = {
        amount: numericAmount,
        method,
        account: accountValue,
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
      {loading && !profile && <p>Loading payout settings…</p>}
      {error && !profile && (
        <>
          <p>Error: {error}</p>
          <button type="button" onClick={refresh}>
            Retry
          </button>
        </>
      )}
      {!loading && !error && availableMethods.length === 0 && (
        <p>
          No payout method on file. Please add your UPI ID or PayPal email inside the SR Tech app
          and try again.
        </p>
      )}
      {!profile || availableMethods.length === 0 ? null : (
        <>
          <form onSubmit={submit}>
            <label>
              Amount ({currencyLabel})
              <input
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                required
                type="number"
                min={minAmount}
                step="0.01"
              />
            </label>
            <p>
              Minimum withdrawal ({method === "upi" ? "UPI" : "PayPal"}): {formattedMinAmount}
            </p>
            <label>
              Method
              <select value={method} onChange={(e) => setMethod(e.target.value as Method)}>
                {availableMethods.includes("upi") && (
                  <option value="upi">UPI ({profile.user.upi_id})</option>
                )}
                {availableMethods.includes("paypal") && (
                  <option value="paypal">PayPal ({profile.user.paypal_id})</option>
                )}
              </select>
            </label>
            <label>
              Account
              <input value={accountValue} readOnly disabled />
            </label>
            <p>Update payout details from the SR Tech app.</p>
            <button type="submit" disabled={!meetsMin || !accountValue}>
              Request Withdrawal
            </button>
          </form>
        </>
      )}
      {status && <p>{status}</p>}
    </div>
  );
}

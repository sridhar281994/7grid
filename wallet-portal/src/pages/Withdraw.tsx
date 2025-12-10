import { useEffect, useMemo, useState } from "react";
import apiFetch from "../api";
import usePortalProfile from "../hooks/usePortalProfile";

type Method = "upi" | "paypal";
type WithdrawResponse = {
  withdrawal_id: number;
  coins_deducted?: number;
};

export default function Withdraw() {
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<Method>("upi");
  const [status, setStatus] = useState<string | null>(null);
  const { profile, loading, error, refresh } = usePortalProfile();

  const coinsPerInr = profile?.conversion?.coins_per_inr ?? 1;
  const coinsPerUsd = profile?.conversion?.coins_per_usd ?? 80;

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
  const walletBalance = profile?.user.wallet_balance ?? 0;
  const coinsPerUnit = method === "upi" ? coinsPerInr : coinsPerUsd;
  const coinsRequired =
    !Number.isNaN(numericAmount) && numericAmount > 0
      ? numericAmount * coinsPerUnit
      : null;

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
      const res = await apiFetch<WithdrawResponse>("/wallet-portal/withdraw", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const coinsText =
        typeof res.coins_deducted === "number"
          ? ` (${res.coins_deducted.toFixed(2)} coins deducted)`
          : "";
      setStatus(`Request queued (ID ${res.withdrawal_id})${coinsText}. Admin will process soon.`);
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
            <div className="info-panel">
              <p>
                Wallet balance: <strong>{walletBalance.toFixed(2)} coins</strong>
              </p>
              <p>
                Conversion: <strong>{coinsPerInr.toFixed(2)}</strong> coins per ₹1 ·{" "}
                <strong>{coinsPerUsd.toFixed(2)}</strong> coins per $1
              </p>
            </div>
            <p className="field-label">Payment method</p>
            <div className="method-toggle" role="group" aria-label="Payment method">
              {availableMethods.includes("upi") && (
                <button
                  type="button"
                  className={method === "upi" ? "active" : ""}
                  onClick={() => setMethod("upi")}
                >
                  UPI ({profile.user.upi_id || "update in app"})
                </button>
              )}
              {availableMethods.includes("paypal") && (
                <button
                  type="button"
                  className={method === "paypal" ? "active" : ""}
                  onClick={() => setMethod("paypal")}
                >
                  PayPal ({profile.user.paypal_id || "update in app"})
                </button>
              )}
            </div>
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
              Account
              <input value={accountValue} readOnly disabled />
            </label>
            <p>Update payout details from the SR Tech app.</p>
            {coinsRequired !== null && (
              <p>
                Estimated coins deducted: <strong>{coinsRequired.toFixed(2)} coins</strong>
              </p>
            )}
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

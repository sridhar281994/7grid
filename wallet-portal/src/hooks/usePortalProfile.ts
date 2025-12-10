import { useCallback, useEffect, useState } from "react";
import apiFetch from "../api";

export type PortalProfile = {
  user: {
    id: number;
    name: string | null;
    email: string | null;
    upi_id: string | null;
    paypal_id: string | null;
    wallet_balance: number;
  };
  limits: {
    recharge_inr_min: number;
    withdraw_inr_min: number;
    withdraw_usd_min: number;
  };
  payout: {
    paypal_enabled: boolean;
    paypal_currency: string;
    has_upi_details: boolean;
    has_paypal_details: boolean;
    upi_id: string | null;
    paypal_id: string | null;
  };
  conversion: {
    coins_per_inr: number;
    coins_per_usd: number;
  };
};

export default function usePortalProfile() {
  const [profile, setProfile] = useState<PortalProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch<PortalProfile>("/wallet-portal/profile");
      setProfile(res);
    } catch (err: any) {
      setProfile(null);
      setError(err?.message || "Failed to load wallet profile.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { profile, loading, error, refresh };
}

BEGIN;

-- 1. Enum for payout method (UPI / PayPal)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'withdrawalmethod') THEN
        CREATE TYPE withdrawalmethod AS ENUM ('upi', 'paypal');
    END IF;
END$$;

-- 2. Enum for payout status lifecycle
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'withdrawalstatus') THEN
        CREATE TYPE withdrawalstatus AS ENUM ('pending', 'processing', 'paid', 'rejected');
    END IF;
END$$;

-- 3. Withdrawals table (one-to-one with wallet_transactions withdraw rows)
CREATE TABLE IF NOT EXISTS withdrawals (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    wallet_tx_id INTEGER NOT NULL UNIQUE REFERENCES wallet_transactions(id) ON DELETE CASCADE,
    amount NUMERIC(10, 2) NOT NULL,
    method withdrawalmethod NOT NULL,
    account VARCHAR(255) NOT NULL,
    status withdrawalstatus NOT NULL DEFAULT 'pending',
    payout_txn_id VARCHAR(255),
    details TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_withdrawals_status ON withdrawals (status);
CREATE INDEX IF NOT EXISTS idx_withdrawals_method ON withdrawals (method);

COMMIT;

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import get_db
from models import WithdrawalRequest, WithdrawalStatus, WalletTransaction, TxStatus, User
from utils.security import require_admin, get_request_context
from utils.audit import log_payout_action
from routers.wallet import withdraw_mark_success, withdraw_mark_failed

router = APIRouter(prefix="/admin/wallet", tags=["admin-wallet"])


def _serialize_withdrawal(w: WithdrawalRequest) -> dict:
    return {
        "id": w.id,
        "user_id": w.user_id,
        "amount": float(w.amount),
        "method": w.method.value,
        "account": w.account,
        "status": w.status.value,
        "created_at": w.created_at,
        "channel": w.channel,
        "initiator_ip": w.initiator_ip,
        "details": w.details,
    }


@router.get("/withdrawals/pending")
def list_pending_withdrawals(
    limit: int = 50,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin(mfa_required=True)),
):
    rows = (
        db.query(WithdrawalRequest)
        .filter(WithdrawalRequest.status == WithdrawalStatus.PENDING)
        .order_by(WithdrawalRequest.created_at.asc())
        .limit(limit)
        .all()
    )
    return {"ok": True, "withdrawals": [_serialize_withdrawal(row) for row in rows]}


@router.post("/withdrawals/{withdrawal_id}/approve")
def approve_withdrawal(
    withdrawal_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin(mfa_required=True)),
):
    withdrawal = db.get(WithdrawalRequest, withdrawal_id)
    if not withdrawal:
        raise HTTPException(404, "Withdrawal not found")
    tx = db.get(WalletTransaction, withdrawal.wallet_tx_id)
    if not tx or tx.status != TxStatus.PENDING:
        raise HTTPException(400, "Transaction already processed")

    ctx = get_request_context(request)
    # Placeholder: actual payout logic (PayPal/UPI) will happen elsewhere.
    response = withdraw_mark_success(tx_id=tx.id, db=db)
    log_payout_action(
        db,
        withdrawal=withdrawal,
        admin=admin,
        action="approve",
        status_before=withdrawal.status.value,
        status_after=WithdrawalStatus.PAID.value,
        ip=ctx.get("ip"),
        user_agent=ctx.get("user_agent"),
        details="Approved manually via admin panel",
        provider_txn_id=withdrawal.payout_txn_id,
    )
    return response


@router.post("/withdrawals/{withdrawal_id}/reject")
def reject_withdrawal(
    withdrawal_id: int,
    request: Request,
    reason: str = "Rejected by admin",
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin(mfa_required=True)),
):
    withdrawal = db.get(WithdrawalRequest, withdrawal_id)
    if not withdrawal:
        raise HTTPException(404, "Withdrawal not found")
    tx = db.get(WalletTransaction, withdrawal.wallet_tx_id)
    if not tx or tx.status != TxStatus.PENDING:
        raise HTTPException(400, "Transaction already processed")

    ctx = get_request_context(request)
    response = withdraw_mark_failed(tx_id=tx.id, reason=reason, db=db)
    log_payout_action(
        db,
        withdrawal=withdrawal,
        admin=admin,
        action="reject",
        status_before=withdrawal.status.value,
        status_after=WithdrawalStatus.REJECTED.value,
        ip=ctx.get("ip"),
        user_agent=ctx.get("user_agent"),
        details=reason,
    )
    return response

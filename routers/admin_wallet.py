from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import WithdrawalRequest, WithdrawalStatus, TxStatus
from utils.security import get_current_admin, require_admin
from utils.audit import log_payout_action

router = APIRouter(prefix="/admin/wallet", tags=["admin-wallet"])


@router.get("/withdrawals/pending")
def list_pending_withdrawals(
    db: Session = Depends(get_db),
    _: None = Depends(require_admin(mfa_required=True)),
):
    rows = (
        db.query(WithdrawalRequest)
        .filter(WithdrawalRequest.status == WithdrawalStatus.PENDING)
        .order_by(WithdrawalRequest.created_at.asc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": w.id,
            "user_id": w.user_id,
            "amount": float(w.amount),
            "method": w.method.value,
            "account": w.account,
            "created_at": w.created_at,
        }
        for w in rows
    ]

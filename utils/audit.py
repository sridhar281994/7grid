import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from models import PayoutAuditLog, WithdrawalRequest, User


def log_payout_action(
    db: Session,
    *,
    withdrawal: WithdrawalRequest,
    admin: Optional[User],
    action: str,
    status_before: str,
    status_after: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    details: Optional[str] = None,
    provider_txn_id: Optional[str] = None,
):
    log = PayoutAuditLog(
        withdrawal_id=withdrawal.id,
        admin_id=admin.id if admin else None,
        action=action,
        status_before=status_before,
        status_after=status_after,
        ip_address=ip,
        user_agent=user_agent,
        details=details,
        provider_txn_id=provider_txn_id,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(log)
    db.commit()
    return log

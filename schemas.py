from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime

# ==== Auth ====
class SendOtpIn(BaseModel):
    phone: str = Field(min_length=10, max_length=20)

class VerifyOtpIn(BaseModel):
    phone: str
    code: str

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

# ==== Users ====
class UserOut(BaseModel):
    id: int
    phone: str
    name: Optional[str] = None
    upi_id: Optional[str] = None
    created_at: datetime
    class Config:
        from_attributes = True

class UpdateProfileIn(BaseModel):
    name: Optional[str] = None
    upi_id: Optional[str] = None

# ==== Wallet ====
class WalletTxIn(BaseModel):
    amount: float = Field(gt=0)
    type: Literal["recharge", "withdraw"]

class WalletTxOut(BaseModel):
    id: int
    amount: float
    type: str
    status: str
    created_at: datetime
    class Config:
        from_attributes = True

# ==== Game ====
class CreateMatchIn(BaseModel):
    stake_amount: int  # 4,8,12

class JoinMatchIn(BaseModel):
    match_id: int

class FinishMatchIn(BaseModel):
    match_id: int
    winner_user_id: int

class MatchOut(BaseModel):
    id: int
    stake_amount: int
    status: str
    p1_user_id: Optional[int]
    p2_user_id: Optional[int]
    winner_user_id: Optional[int]
    created_at: datetime
    class Config:
        from_attributes = True

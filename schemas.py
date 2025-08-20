from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime

# --- Auth ---
class SendOtpIn(BaseModel):
    phone: str = Field(min_length=8, max_length=20)
    name: Optional[str] = None  # allow setting on first send

class VerifyOtpIn(BaseModel):
    phone: str
    code: str = Field(min_length=4, max_length=6)

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"

# --- User / Wallet ---
class ProfileUpdateIn(BaseModel):
    name: Optional[str] = None
    upi_id: Optional[str] = None

class WalletActionIn(BaseModel):
    amount: float = Field(gt=0)

class UserOut(BaseModel):
    id: int
    phone: str
    name: Optional[str]
    upi_id: Optional[str]
    wallet_balance: float
    class Config:
        from_attributes = True

# --- Game ---
class StartGameIn(BaseModel):
    stake_rs: Literal[4, 8, 12]

class GameResultOut(BaseModel):
    match_id: int
    stake_rs: int
    result: Literal["win", "lose", "danger"]
    new_balance: float
    created_at: datetime

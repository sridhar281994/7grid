import random
from sqlalchemy.orm import Session
from crud import create_otp, verify_and_consume_otp

def generate_code(n=6) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(n))

def send_otp(db: Session, phone: str) -> bool:
    # In production, integrate SMS provider here (Twilio, etc.)
    code = generate_code()
    create_otp(db, phone, code, ttl_sec=300)
    # For demo/testing: log to console (Render logs)
    print(f"[OTP] Phone={phone} Code={code}")
    return True

def verify_otp(db: Session, phone: str, code: str) -> bool:
    return verify_and_consume_otp(db, phone, code)

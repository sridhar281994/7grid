import asyncio
from database import SessionLocal
from routers.wallet import process_paypal_withdrawals
from utils.security import FakeUser

async def main():
    db = SessionLocal()
    try:
        result = process_paypal_withdrawals(limit=25, db=db)
        print(result)
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())

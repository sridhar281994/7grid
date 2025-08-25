# Spin Dice API (FastAPI + Render)

### ENV
- `DATABASE_URL` (Render external Postgres)
- `SECRET_KEY` (JWT signing)

### Local run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="postgresql://.../spin_db"
export SECRET_KEY="dev-secret"
uvicorn main:app --reload
Endpoints
GET /health
POST /auth/send-otp {phone}
POST /auth/verify-otp {phone, code} -> {access_token}
GET /users/{id}
PATCH /users/{id} {name?, upi_id?}
POST /wallet/{user_id}/tx {amount, type:"recharge"|"withdraw"}
GET /wallet/{user_id}/history
POST /game/create?user_id=1 {stake_amount:4|8|12}
POST /game/join?user_id=2 {match_id}
POST /game/finish {match_id, winner_user_id}
GET /game/waiting

---

## How to deploy on Render

1) Push this repo to GitHub  
2) Render → **New → Web Service** → select repo  
3) Build: `pip install -r requirements.txt`  
4) Start: `uvicorn main:app --host=0.0.0.0 --port $PORT`  
5) Add env vars:
   - `DATABASE_URL` = your external DB URL
   - `SECRET_KEY` = long random string
6) Deploy → test `/health`

---

This gives you a working backend that your Kivy client can call for:
- OTP login,
- user profile,
- simple wallet records (recharge/withdraw entries),
- game room creation/join/finish for 4/8/12 stakes.

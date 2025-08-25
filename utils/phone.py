def normalize_phone(phone: str) -> str:
    p = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    digits = "".join(ch for ch in p if ch.isdigit())
    if len(digits) == 10 and not p.startswith("+"):
        return "+91" + digits
    if p.startswith("+") and 10 <= len(digits) <= 15:
        return p
    return "+" + digits if not p.startswith("+") else p

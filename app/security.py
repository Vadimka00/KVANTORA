import base64, hmac, hashlib
from .config import config

def _sign(data: str) -> str:
    secret = config.deep_link_secret.encode()
    sig = hmac.new(secret, data.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")[:22]

def pack_deeplink(chat_id: int, msg_id: int) -> str:
    data = f"{chat_id}:{msg_id}"
    raw = base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")
    return f"{raw}.{_sign(data)}"

def unpack_deeplink(payload: str) -> tuple[int, int] | None:
    try:
        raw, sig = payload.split(".", 1)
        pad = "=" * (-len(raw) % 4)
        data = base64.urlsafe_b64decode(raw + pad).decode()
        if _sign(data) != sig:
            return None
        c, m = data.split(":", 1)
        return int(c), int(m)
    except Exception:
        return None
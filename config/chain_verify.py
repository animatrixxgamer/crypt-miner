"""
On-chain payment verification (fake-receipt detection).

When a user sends a screenshot + transaction hash, the bot asks public
blockchain explorers whether that transaction really exists, whether it was
sent to OUR wallet address, and whether the amount matches the plan price.
Fake/edited screenshots fail this check and are flagged for manual review.

Supported today (no API keys required):
  * BTC, ETH, DOGE, LTC, BCH, DASH  → BlockCypher
  * USDT (TRC-20, address starting with "T") → TronScan
Everything else (e.g. USDT-ERC20, SOL) is reported as "unsupported" and the
payment goes to manual review — we never auto-approve on uncertainty.
"""
import logging
import re

logger = logging.getLogger(__name__)

# coin -> (blockcypher network, decimals)
_BLOCKCYPHER = {
    "BTC": ("btc", 8),
    "ETH": ("eth", 18),
    "DOGE": ("doge", 8),
    "LTC": ("ltc", 8),
    "BCH": ("bch", 8),
    "DASH": ("dash", 8),
}

_HEX_RE = re.compile(r"^[0-9a-fA-F]{32,128}$")
_B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{26,128}$")


def _looks_like_txid(txid: str) -> bool:
    t = (txid or "").strip()
    if not t:
        return False
    return bool(_HEX_RE.match(t) or _B58_RE.match(t))


async def _get_json(url: str):
    """GET JSON; returns (data|None, http_status)."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 200:
                try:
                    return r.json(), 200
                except Exception:
                    return None, 200
            return None, r.status_code
    except Exception as e:
        logger.warning(f"Chain check network error for {url}: {e}")
        return None, 0


def _clean_txid(txid: str) -> str:
    t = (txid or "").strip().strip("`").strip()
    if t.lower().startswith("0x"):
        t = t[2:]
    return t


async def _verify_blockcypher(coin: str, dest_addr: str, txid: str,
                              expected: float, tol: float) -> dict:
    net, decimals = _BLOCKCYPHER[coin]
    data, status = await _get_json(f"https://api.blockcypher.com/v1/{net}/main/txs/{txid}")
    if data is None:
        if status == 404:
            return {"ok": False, "status": "not_found", "detail": "Transaction not found on the blockchain."}
        if status == 429:
            return {"ok": False, "status": "network_error", "detail": "Explorer rate limit — please retry in a minute."}
        return {"ok": False, "status": "network_error", "detail": "Could not reach the blockchain explorer."}

    conf = int(data.get("confirmations") or 0)
    received = 0.0
    paid_to_us = False
    for out in data.get("outputs", []):
        addrs = [str(a) for a in (out.get("addresses") or [])]
        if dest_addr in addrs or (dest_addr or "").lower() in [a.lower() for a in addrs]:
            paid_to_us = True
            received += int(out.get("value") or 0) / (10 ** decimals)

    if not paid_to_us:
        return {"ok": False, "status": "wrong_recipient",
                "detail": "Transaction exists but was NOT sent to our wallet address."}
    if conf < 1:
        return {"ok": False, "status": "unconfirmed",
                "detail": "Transaction is still unconfirmed (0 confirmations)."}
    low = expected * (1 - tol)
    high = expected * (1 + tol)
    if not (low <= received <= high):
        return {"ok": False, "status": "amount_mismatch",
                "detail": f"Amount mismatch — received ~{received:.8f}, expected ~{expected:.8f}."}

    return {"ok": True, "status": "ok", "received": received, "confirmations": conf,
            "detail": f"Confirmed on-chain ({conf} confirmations), amount OK.",
            "link": f"https://live.blockcypher.com/{net}/tx/{txid}/"}


async def _verify_tron(txid: str, dest_addr: str, expected: float, tol: float) -> dict:
    data, status = await _get_json(f"https://apilist.tronscanapi.com/api/transaction-info?hash={txid}")
    if data is None:
        if status == 404:
            return {"ok": False, "status": "not_found", "detail": "Transaction not found on TRON."}
        return {"ok": False, "status": "network_error", "detail": "Could not reach the TRON explorer."}

    if str(data.get("contractRet") or data.get("ret") or "").upper() not in ("SUCCESS", "SUCCESSFUL"):
        return {"ok": False, "status": "not_found",
                "detail": "Transaction exists but failed / was not executed."}

    info = None
    trc = data.get("trc20TransferInfo") or data.get("tokenTransferInfo") or []
    for item in trc:
        if isinstance(item, dict):
            to_addr = str(item.get("to_address") or item.get("to") or "")
            if to_addr and (to_addr == dest_addr or to_addr.lower() == (dest_addr or "").lower()):
                info = item
                break
    if info is None:
        return {"ok": False, "status": "wrong_recipient",
                "detail": "Transaction found but nothing was sent to our wallet address."}

    try:
        raw = int(info.get("amount") or 0)
        decimals = int(info.get("decimals") or data.get("tokenInfo", {}).get("decimals") or 6)
    except (TypeError, ValueError):
        decimals = 6
        raw = 0
    # amount is expected in the token's smallest unit (e.g. 6 decimals for USDT TRC-20)
    received = raw / (10 ** decimals) if decimals else 0.0
    low = expected * (1 - tol)
    high = expected * (1 + tol)
    if not (low <= received <= high):
        return {"ok": False, "status": "amount_mismatch",
                "detail": f"Amount mismatch — received ~{received:.4f}, expected ~{expected:.4f}."}

    return {"ok": True, "status": "ok", "received": received, "confirmations": 1,
            "detail": "Confirmed on TRON (token transfer), amount OK.",
            "link": f"https://tronscan.org/#/transaction/{txid}"}


async def verify_transaction(coin, dest_address, txid, expected_amount,
                             tolerance: float = 0.01) -> dict:
    """Verify a claimed crypto payment against the public blockchain.

    Returns a dict: {ok, status, received, confirmations, detail, link}
    status: ok | not_found | unconfirmed | amount_mismatch | wrong_recipient
            | unsupported | network_error | invalid_hash
    ok=False is NEVER an auto-approve — callers must route to manual review.
    """
    base = {"ok": False, "received": None, "confirmations": None,
            "detail": "", "link": ""}
    txid = _clean_txid(txid)
    dest = (dest_address or "").strip()
    coin_u = str(coin or "").upper()
    try:
        expected = float(expected_amount)
    except (TypeError, ValueError):
        expected = 0.0

    if not _looks_like_txid(txid):
        return {**base, "status": "invalid_hash", "detail": "That doesn't look like a transaction hash."}
    if not dest:
        return {**base, "status": "unsupported", "detail": "No wallet address configured for this coin."}

    try:
        if coin_u.startswith("USDT") and dest.startswith("T"):
            return await _verify_tron(txid, dest, expected, tolerance)
        if coin_u.startswith("USDT") and dest.startswith("0x"):
            return {**base, "status": "unsupported",
                    "detail": "USDT-ERC20 auto-check isn't enabled — sending to manual review."}
        if coin_u in _BLOCKCYPHER:
            return await _verify_blockcypher(coin_u, dest, txid, expected, tolerance)
        return {**base, "status": "unsupported",
                "detail": f"Automatic on-chain check isn't available for {coin_u} — manual review."}
    except Exception as e:
        logger.warning(f"Chain verification crashed for {coin_u}/{txid}: {e}")
        return {**base, "status": "network_error", "detail": "Verification failed — manual review."}

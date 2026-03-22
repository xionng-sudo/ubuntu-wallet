from __future__ import annotations

import os
from typing import Optional

_ENV_KEY = "WALLET_PRIVATE_KEY"


def load_private_key(private_key: Optional[str] = None) -> str:
    """
    Load and validate a wallet private key.

    Sources (in priority order):
      1. ``private_key`` argument
      2. ``WALLET_PRIVATE_KEY`` environment variable

    The key must be a 64-hex-character string (with or without ``0x`` prefix).

    Returns
    -------
    str
        The private key in normalised ``0x<64-hex>`` form.

    Raises
    ------
    ValueError
        If no key is found or the key fails format validation.

    Security note
    -------------
    Never commit private keys to source control.  Store them in ``.env`` and
    ensure ``.env`` is listed in ``.gitignore``.
    """
    key = private_key or os.environ.get(_ENV_KEY, "")
    if not key:
        raise ValueError(
            f"Wallet private key not found. "
            f"Set {_ENV_KEY} in your .env file or pass it explicitly.\n"
            "WARNING: Never commit private keys to source control."
        )
    key = key.strip()
    if key.startswith(("0x", "0X")):
        key = key[2:]
    if len(key) != 64 or not all(c in "0123456789abcdefABCDEF" for c in key):
        raise ValueError(
            "Invalid private key format. "
            "Expected a 64-character hexadecimal string (with or without '0x' prefix)."
        )
    return "0x" + key.lower()


def get_account(w3, private_key: Optional[str] = None):
    """
    Return a web3 ``LocalAccount`` for the given private key.

    Parameters
    ----------
    w3:
        Connected ``Web3`` instance.
    private_key:
        Raw private key string (falls back to ``WALLET_PRIVATE_KEY`` env var).
    """
    key = load_private_key(private_key)
    return w3.eth.account.from_key(key)


def get_eth_balance(w3, address: str) -> float:
    """Return the ETH balance (in ether, not wei) for *address*."""
    balance_wei = w3.eth.get_balance(w3.to_checksum_address(address))
    return float(w3.from_wei(balance_wei, "ether"))

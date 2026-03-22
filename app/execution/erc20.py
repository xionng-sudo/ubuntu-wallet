from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Minimal ERC-20 ABI — only the functions we need
# ---------------------------------------------------------------------------
_ERC20_ABI = [
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner",   "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount",  "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "decimals",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
]

# Unlimited approval amount (max uint256)
MAX_UINT256: int = 2**256 - 1


def _token_contract(w3, token_address: str):
    from web3 import Web3
    return w3.eth.contract(
        address=Web3.to_checksum_address(token_address),
        abi=_ERC20_ABI,
    )


def get_allowance(w3, token_address: str, owner: str, spender: str) -> int:
    """Return the current ERC-20 allowance (in token's smallest unit)."""
    from web3 import Web3
    token = _token_contract(w3, token_address)
    return token.functions.allowance(
        Web3.to_checksum_address(owner),
        Web3.to_checksum_address(spender),
    ).call()


def get_token_balance(w3, token_address: str, owner: str) -> int:
    """Return ERC-20 balance for *owner* (in token's smallest unit)."""
    from web3 import Web3
    token = _token_contract(w3, token_address)
    return token.functions.balanceOf(Web3.to_checksum_address(owner)).call()


def build_approve_tx(
    w3,
    token_address: str,
    spender: str,
    amount_wei: int,
    from_address: str,
    gas_price_gwei: Optional[float] = None,
) -> dict:
    """
    Build an unsigned ERC-20 ``approve`` transaction dict.

    Parameters
    ----------
    w3:
        Connected Web3 instance.
    token_address:
        ERC-20 token contract address.
    spender:
        Address to approve (e.g. Uniswap SwapRouter02).
    amount_wei:
        Allowance to grant in smallest units; use ``MAX_UINT256`` for unlimited.
    from_address:
        Transaction sender.
    gas_price_gwei:
        Override gas price in Gwei; uses on-chain suggestion when ``None``.
    """
    from web3 import Web3
    token = _token_contract(w3, token_address)
    gas_price = (
        Web3.to_wei(gas_price_gwei, "gwei")
        if gas_price_gwei is not None
        else w3.eth.gas_price
    )
    return token.functions.approve(
        Web3.to_checksum_address(spender),
        amount_wei,
    ).build_transaction(
        {
            "from":     Web3.to_checksum_address(from_address),
            "gasPrice": gas_price,
            "nonce":    w3.eth.get_transaction_count(
                            Web3.to_checksum_address(from_address)
                        ),
        }
    )


def ensure_allowance(
    w3,
    account,
    token_address: str,
    spender: str,
    required_wei: int,
    gas_price_gwei: Optional[float] = None,
) -> Optional[str]:
    """
    Ensure *account* has approved *spender* to spend at least *required_wei*
    of *token_address*.

    If the current allowance is sufficient, returns ``None`` (no transaction
    sent).  Otherwise sends an unlimited ``approve`` transaction and waits for
    its receipt.

    Returns
    -------
    Optional[str]
        Transaction hash (hex) of the approval tx, or ``None`` if already
        approved.

    Raises
    ------
    RuntimeError
        If the approval transaction is mined but reverts (status = 0).
    """
    current = get_allowance(w3, token_address, account.address, spender)
    if current >= required_wei:
        return None

    tx = build_approve_tx(
        w3,
        token_address=token_address,
        spender=spender,
        amount_wei=MAX_UINT256,
        from_address=account.address,
        gas_price_gwei=gas_price_gwei,
    )
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt["status"] != 1:
        raise RuntimeError(
            f"Approve transaction {tx_hash.hex()} reverted (status=0). "
            "Check token address, spender, gas, and wallet balance."
        )
    return tx_hash.hex()

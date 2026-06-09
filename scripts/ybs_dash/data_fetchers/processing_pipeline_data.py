import logging

from brownie import Contract
from constants import YCRV_SPLITTER, YCRV_RECEIVER, YCRV_FEE_BURNER, TRADE_HANDLER, YCRV, CRVUSD

log = logging.getLogger(__name__)

ERC20_METADATA_ABI = [
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
    {
        "name": "symbol",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "string"}],
    },
]


def erc20_metadata_contract(address):
    return Contract.from_abi("ERC20Metadata", address, ERC20_METADATA_ABI)


def build_data(token, staker_data):
    if token != YCRV:
        return {}
    splitter = Contract(YCRV_SPLITTER)
    receiver = Contract(YCRV_RECEIVER)
    burner = Contract(YCRV_FEE_BURNER)
    burn_tokens = list(burner.getApprovals(TRADE_HANDLER))
    if CRVUSD not in burn_tokens:
        burn_tokens.append(CRVUSD)
        
    balances = {}
    for token_address in burn_tokens:
        token = erc20_metadata_contract(token_address)
        try:
            balance = token.balanceOf(YCRV_FEE_BURNER)
        except Exception as exc:
            log.warning("Skipping fee burner token %s: balanceOf failed: %s", token_address, exc)
            continue

        if balance <= 1:
            continue

        try:
            decimals = token.decimals()
        except Exception as exc:
            log.warning("Skipping fee burner token %s: decimals failed: %s", token_address, exc)
            continue

        try:
            symbol = token.symbol()
        except Exception as exc:
            log.warning("Using address as symbol for fee burner token %s: %s", token_address, exc)
            symbol = token.address

        balances[token.address] = {
            'balance': balance / 10 ** decimals,
            'symbol': symbol,
        }

        
    reward_token = staker_data['reward_token']
    receiver_balance = reward_token.balanceOf(receiver) / 10 ** reward_token.decimals()
    splitter = Contract(YCRV_SPLITTER)
    split_ratios = splitter.getSplits()
        
    admin_splits = [split_ratios['adminFeeSplits'][i] / 1e18 for i in range(3)]
    incentive_splits = [split_ratios['voteIncentiveSplits'][i] / 1e18 for i in range(3)]

    return {
        'receiver': receiver,
        'splitter': splitter,
        'fee_burner': burner,
        'receiver_balance': receiver_balance,
        'burner_balances': balances,
        'split_ratio_admin_fees': admin_splits,
        'split_ratio_vote_incentives': incentive_splits,
    }

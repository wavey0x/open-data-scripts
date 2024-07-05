from brownie import Contract, ZERO_ADDRESS, chain
import utils as utilities
from constants import YCRV_SPLITTER, YCRV_RECEIVER, YCRV_FEE_BURNER, TRADE_HANDLER, YCRV, CRVUSD

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
    for token in burn_tokens:
        token = Contract(token)
        balance = token.balanceOf(YCRV_FEE_BURNER)
        if balance > 1:
            balances[token.address] = {}
            balances[token.address]['balance'] = balance / 10 ** token.decimals()
            balances[token.address]['symbol'] = token.symbol()

        
    reward_token = staker_data['reward_token']
    receiver_balance = reward_token.balanceOf(receiver) / 10 ** staker_data['strategy_data']['reward_token_decimals']
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


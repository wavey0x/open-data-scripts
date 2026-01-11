from brownie import chain, network
from utils import db as db_utils
from scripts.ybs_dash.main import populate_staker_info
from datetime import datetime

def main():
    """
    Single-pass execution. Called by scripts/run.py on ~10min schedule.
    Fetches events since last run (tracked in database).
    """
    if not network.is_connected():
        network.connect("mainnet", launch_rpc=False)
    db_utils.ensure_ybs_schema()
    staker_info = populate_staker_info()
    height = chain.height

    for token, info in staker_info.items():
        process_token_events(token, info, height)

    print(f"Event indexer completed. Processed up to block {height}")

def process_token_events(token, info, height):
    """Process all event types for a single token"""
    ybs = info['ybs']
    rewards = info['rewards']
    decimals = info['decimals']
    symbol = info['symbol']

    # Get last blocks from database (where we left off)
    last_stake = db_utils.get_last_block_for_event(ybs.address, 'Staked')
    last_unstake = db_utils.get_last_block_for_event(ybs.address, 'Unstaked')
    last_claim = db_utils.get_last_block_for_event(ybs.address, 'RewardsClaimed')
    last_deposit = db_utils.get_last_block_for_event(ybs.address, 'RewardDeposited')

    min_block = min(last_stake, last_unstake, last_claim, last_deposit)
    print(f"{symbol}: Checking events from block {min_block} to {height}")

    # Process Staked events
    staked_count = 0
    for log in ybs.events.Staked.get_logs(fromBlock=last_stake, toBlock=height):
        handle_stake_event(log, token, info, is_stake=True)
        staked_count += 1

    # Process Unstaked events
    unstaked_count = 0
    for log in ybs.events.Unstaked.get_logs(fromBlock=last_unstake, toBlock=height):
        handle_stake_event(log, token, info, is_stake=False)
        unstaked_count += 1

    # Process RewardsClaimed events
    claimed_count = 0
    for log in rewards.events.RewardsClaimed.get_logs(fromBlock=last_claim, toBlock=height):
        handle_reward_event(log, token, info, is_claim=True)
        claimed_count += 1

    # Process RewardDeposited events
    deposited_count = 0
    for log in rewards.events.RewardDeposited.get_logs(fromBlock=last_deposit, toBlock=height):
        handle_reward_event(log, token, info, is_claim=False)
        deposited_count += 1

    total = staked_count + unstaked_count + claimed_count + deposited_count
    if total > 0:
        print(f"  {symbol}: {staked_count} staked, {unstaked_count} unstaked, {claimed_count} claimed, {deposited_count} deposited")

def handle_stake_event(log, token, info, is_stake):
    """Process a single Staked or Unstaked event"""
    decimals = info['decimals']
    block = chain[log.blockNumber]
    max_weeks = info['ybs'].MAX_STAKE_GROWTH_WEEKS()

    # Handle both Staked (weightAdded) and Unstaked (weightRemoved) events
    weight_change = log['args'].get('weightAdded') or log['args'].get('weightRemoved', 0)
    amount = log['args']['amount'] / 10 ** decimals
    week = log['args']['week']
    unlock_week = week + max_weeks if is_stake else None

    if is_stake:
        db_utils.upsert_stake_bucket(token, unlock_week, amount)
    else:
        remaining = amount
        for target_week in range(week + max_weeks, week, -1):
            bucket_amount = db_utils.get_stake_bucket_amount(token, target_week)
            if bucket_amount <= 0:
                continue
            if bucket_amount >= remaining:
                db_utils.upsert_stake_bucket(token, target_week, -remaining)
                remaining = 0
                break
            remaining -= bucket_amount
            db_utils.upsert_stake_bucket(token, target_week, -bucket_amount)
        if remaining > 0:
            print(f"Warning: {token} unstake of {remaining} could not be fully bucketed.")

    record = {
        'ybs': log.address,
        'account': log['args']['account'],
        'amount': amount,
        'is_stake': is_stake,
        'week': week,
        'unlock_week': unlock_week,
        'new_weight': log['args']['newUserWeight'] / 10 ** decimals,
        'net_weight_change': weight_change / 10 ** decimals,
        'timestamp': block.timestamp,
        'date_str': datetime.utcfromtimestamp(block.timestamp).strftime('%Y-%m-%d %H:%M:%S'),
        'txn_hash': log.transactionHash.hex(),
        'block': log.blockNumber,
        'token': token,
    }
    db_utils.insert_stake(record)

def handle_reward_event(log, token, info, is_claim):
    """Process a single RewardsClaimed or RewardDeposited event"""
    decimals = info['decimals']
    block = chain[log.blockNumber]

    # RewardsClaimed has 'account', RewardDeposited has 'depositor'
    account = log['args']['account'] if is_claim else log['args']['depositor']

    record = {
        'ybs': info['ybs'].address,
        'reward_distributor': log.address,
        'is_claim': is_claim,
        'account': account,
        'amount': log['args']['rewardAmount'] / 10 ** decimals,
        'week': log['args']['week'],
        'timestamp': block.timestamp,
        'date_str': datetime.utcfromtimestamp(block.timestamp).strftime('%Y-%m-%d %H:%M:%S'),
        'txn_hash': log.transactionHash.hex(),
        'block': log.blockNumber,
        'token': token,
    }
    db_utils.insert_reward(record)

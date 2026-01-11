from brownie import chain, network
from utils import db as db_utils
from scripts.ybs_dash.main import populate_staker_info
from datetime import datetime
from config import get_json_path
import json
import logging
import os

CURSOR_FILE = get_json_path("ybs_event_cursor.json")
CHUNK_SIZE = 25_000

def main():
    """
    Single-pass execution. Called by scripts/run.py on ~10min schedule.
    Fetches events since last run (tracked in database).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not network.is_connected():
        network.connect("mainnet", launch_rpc=False)
    db_utils.ensure_ybs_schema()
    cursor = _load_cursor()
    staker_info = populate_staker_info()
    height = chain.height

    for token, info in staker_info.items():
        process_token_events(token, info, height, cursor)

    _save_cursor(cursor)
    print(f"Event indexer completed. Processed up to block {height}")

def process_token_events(token, info, height, cursor):
    """Process all event types for a single token"""
    ybs = info['ybs']
    rewards = info['rewards']
    decimals = info['decimals']
    symbol = info['symbol']
    ybs_addr = ybs.address.lower()

    # Get last blocks from database (where we left off)
    last_stake = _max_start_block(cursor, ybs_addr, 'Staked', db_utils.get_last_block_for_event(ybs.address, 'Staked'))
    last_unstake = _max_start_block(cursor, ybs_addr, 'Unstaked', db_utils.get_last_block_for_event(ybs.address, 'Unstaked'))
    last_claim = _max_start_block(cursor, ybs_addr, 'RewardsClaimed', db_utils.get_last_block_for_event(ybs.address, 'RewardsClaimed'))
    last_deposit = _max_start_block(cursor, ybs_addr, 'RewardDeposited', db_utils.get_last_block_for_event(ybs.address, 'RewardDeposited'))

    min_block = min(last_stake, last_unstake, last_claim, last_deposit)
    print(f"{symbol}: Checking events from block {min_block} to {height}")

    # Process Staked events
    staked_count = 0
    staked_count += _process_event_in_chunks(
        ybs.events.Staked, last_stake, height, cursor, ybs_addr, 'Staked',
        lambda log: handle_stake_event(log, token, info, is_stake=True)
    )

    # Process Unstaked events
    unstaked_count = 0
    unstaked_count += _process_event_in_chunks(
        ybs.events.Unstaked, last_unstake, height, cursor, ybs_addr, 'Unstaked',
        lambda log: handle_stake_event(log, token, info, is_stake=False)
    )

    # Process RewardsClaimed events
    claimed_count = 0
    claimed_count += _process_event_in_chunks(
        rewards.events.RewardsClaimed, last_claim, height, cursor, ybs_addr, 'RewardsClaimed',
        lambda log: handle_reward_event(log, token, info, is_claim=True)
    )

    # Process RewardDeposited events
    deposited_count = 0
    deposited_count += _process_event_in_chunks(
        rewards.events.RewardDeposited, last_deposit, height, cursor, ybs_addr, 'RewardDeposited',
        lambda log: handle_reward_event(log, token, info, is_claim=False)
    )

    total = staked_count + unstaked_count + claimed_count + deposited_count
    if total > 0:
        print(f"  {symbol}: {staked_count} staked, {unstaked_count} unstaked, {claimed_count} claimed, {deposited_count} deposited")


def _process_event_in_chunks(event, start_block, end_block, cursor, ybs_addr, event_type, handler):
    """Fetch logs in chunks and persist cursor even when no events occur."""
    log = logging.getLogger(__name__)
    if start_block > end_block:
        return 0
    count = 0
    current = start_block
    while current <= end_block:
        chunk_end = min(current + CHUNK_SIZE - 1, end_block)
        log.info("%s %s: scanning %s-%s", ybs_addr, event_type, current, chunk_end)
        for log_item in event.get_logs(fromBlock=current, toBlock=chunk_end):
            handler(log_item)
            count += 1
        _set_cursor_block(cursor, ybs_addr, event_type, chunk_end + 1)
        _save_cursor(cursor)
        current = chunk_end + 1
    return count


def _load_cursor():
    if not os.path.exists(CURSOR_FILE):
        return {}
    try:
        with open(CURSOR_FILE, "r") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cursor(cursor):
    os.makedirs(os.path.dirname(CURSOR_FILE), exist_ok=True)
    with open(CURSOR_FILE, "w") as handle:
        json.dump(cursor, handle, indent=2, sort_keys=True)


def _get_cursor_block(cursor, ybs_addr, event_type):
    return cursor.get(ybs_addr, {}).get(event_type)


def _set_cursor_block(cursor, ybs_addr, event_type, block):
    cursor.setdefault(ybs_addr, {})[event_type] = block


def _max_start_block(cursor, ybs_addr, event_type, db_block):
    cursor_block = _get_cursor_block(cursor, ybs_addr, event_type)
    if cursor_block is None:
        return db_block
    return max(cursor_block, db_block)

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

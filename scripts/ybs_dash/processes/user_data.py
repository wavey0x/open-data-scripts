from brownie import Contract, chain
from utils import utils as utilities
from utils import db as db_utils
import json, os
from dotenv import load_dotenv
from scripts.ybs_dash.main import populate_staker_info
from datetime import datetime

load_dotenv()
staker_info = {}
height = chain.height

def main():
    global staker_info
    staker_info = populate_staker_info()

    for token, info in staker_info.items():
        fill_weeks(token, info)

def fill_weeks(token, info):
    ybs = info['ybs']
    
    users = db_utils.query_unique_accounts(token)
    decimals = ybs.decimals()
    max_weeks = ybs.MAX_STAKE_GROWTH_WEEKS()

    current_week = ybs.getWeek()
    last_filled_week = db_utils.get_highest_week_id_for_token(token)
    if not last_filled_week:
        last_filled_week = utilities.get_launch_week(ybs.address) - 1

    print(f'Last filled week: {last_filled_week}')
    for week in range(current_week, last_filled_week, -1):
        print(f'Iterating over week {week}....')
        end_block = height
        try:
            end_block = utilities.get_week_end_block(ybs.address, week)
        except:
            pass
        insert_week_info(info, week, end_block, max_weeks, decimals, False)
        insert_users_info(users, info, week, end_block, max_weeks, decimals, False)

    update_current_week(token, info)

def insert_week_info(
    info, 
    week,
    end_block,
    max_weeks, 
    decimals,
    do_upsert=False,
):
    ybs = info['ybs']
    supply = ybs.totalSupply(block_identifier=end_block) / 10 ** decimals
    global_weight = ybs.getGlobalWeightAt(week) / 10 ** decimals
    start_ts = utilities.get_week_start_ts(ybs.address, week)
    end_ts = utilities.get_week_end_ts(ybs.address, week)
    stake_map = build_global_stake_map(ybs, week, end_block, max_weeks, decimals)
    print(stake_map)
    db_utils.insert_week_info({
        'week_id': week,
        'token': info['token'].address,
        'weight': global_weight,
        'total_supply': supply,
        'boost': global_weight / supply,
        'ybs': ybs.address,
        'start_ts': start_ts,
        'end_ts': end_ts,
        'start_block': utilities.get_week_start_block(ybs.address, week),
        'end_block': end_block,
        'start_time_str': datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d"),
        'end_time_str': datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d"),
        'stake_map': stake_map
    }, do_upsert)
    print(f'Week {week} successfully written.')

def insert_users_info(users, info, week, end_block, max_weeks, decimals, do_upsert=False):
    ybs = info['ybs']
    token = info['token']
    rewards = info['rewards']
    reward_decimals = Contract(info['rewards'].rewardToken()).decimals()
    for user in users:
        weight = ybs.getAccountWeightAt(user, week) / 1e18
        if weight == 0:
            continue
        balance = ybs.balanceOf(user, block_identifier=end_block) / 1e18
        acct_data = ybs.accountData(user, block_identifier=end_block)    
        stake_map = build_user_stake_map(
            ybs, user, acct_data, week, end_block, max_weeks, decimals
        )

        db_utils.insert_user_info({
            'account': user,
            'week_id': week,
            'token': token.address,
            'weight': weight,
            'balance': balance,
            'boost': weight / balance,
            'stake_map': stake_map,
            'rewards_earned': rewards.getClaimableAt(user, week) / 10 ** reward_decimals,
            'ybs': ybs.address,
            'total_realized': stake_map['realized'],
        }, do_upsert)
        print(f'User {user} @ week {week} successfully written.')


def update_current_week(token, info):
    ybs = info['ybs']
    week = ybs.getWeek()
    decimals = ybs.decimals()
    max_weeks = ybs.MAX_STAKE_GROWTH_WEEKS()
    # Populate current week
    last_stake_recorded = db_utils.get_latest_stake_recorded_for_token(token)
    if not last_stake_recorded:
        last_stake_recorded = ybs.getWeek(),info['ybs_deploy_block']
    last_stake_recorded = max(
        last_stake_recorded,
        utilities.get_week_start_block(ybs.address, ybs.getWeek()),
    )

    logs = ybs.events.Staked.get_logs(fromBlock=last_stake_recorded, toBlock=height)
    logs += ybs.events.Unstaked.get_logs(fromBlock=last_stake_recorded, toBlock=height)

    users = set()
    for log in logs:
        users.add(log['args']['account'])

    if len(logs) > 0:
        insert_week_info(info, week, height, max_weeks, decimals, True)
        insert_users_info(users, info, week, height, max_weeks, decimals, True)


def build_global_stake_map(ybs, week, block, max_weeks, decimals):
    pending_map = {}
    pending_map['realized'] = ybs.totalSupply() / 10 ** decimals
    for i in range(max_weeks):
        target_week = week + 1 + i
        amt = ybs.globalWeeklyToRealize(
            target_week, block_identifier=block
        )['weight'] * 2 / 10 ** decimals

        pending_map[target_week] = {
            'amount': amt,
            'week_start_ts': utilities.get_week_start_ts(ybs.address, target_week),
            'max_weeks': max_weeks
        }
        pending_map['realized'] -= amt

    return pending_map

def test():
    ybs = Contract('0xF4C6e0E006F164535508787873d86b84fe901975')
    user = '0xA323CCcbCbaDe7806ca5bB9951bebD89A7882bf8'
    week = 47

    block = utilities.get_week_end_block(ybs.address, week)

    acct_data = ybs.accountData(user, block_identifier=block)
    
    max_weeks = ybs.MAX_STAKE_GROWTH_WEEKS()
    decimals = 18
    

    map = build_user_stake_map(ybs, user, acct_data, week, block, max_weeks, decimals)
    balance = ybs.balanceOf(user, block_identifier=block) / 1e18

    print(map)
    print(balance)

    assert False

def build_user_stake_map(ybs, user, acct_data, week, block, max_weeks, decimals):
    """
    Returns a dict where keys are the deposit week and 
    """
    week_offset = week - acct_data['lastUpdateWeek']
    bitmap = acct_data['updateWeeksBitmap']
    bitstring = format(bitmap, '08b')[::-1][:-(max_weeks-1)]    # Reverse order and trim
    bitarray = [int(char) for char in bitstring]            # Convert to array
    # bitarray = shift_array(bitarray, week_offset) # Adjust for offset
    realized = acct_data['realizedStake'] * 2 / 10 ** decimals
    pending_map = {}

    for i, bit in enumerate(bitarray):
        target_week = week - week_offset + (len(bitarray) - 1 - i)
        amt = 0
        if target_week < week:
            realized += ybs.accountWeeklyToRealize(
                user, target_week, block_identifier=block
            )['weight'] * 2 / 10 ** decimals
        else:
            amt = ybs.accountWeeklyToRealize(
                user, target_week, block_identifier=block
            )['weight'] * 2 / 10 ** decimals
        pending_map[target_week] = {
            'amount': amt,
            'week_start_ts': utilities.get_week_start_ts(ybs.address, target_week),
            'max_weeks': max_weeks,
        }
        pending_map['realized'] = realized

    return pending_map

def shift_array(arr, offset):
    length = len(arr)
    if offset >= length:
        return [0] * length
    return [0] * offset + arr[:length - offset]
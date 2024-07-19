from brownie import Contract, chain
from utils import utils as utilities
from utils import db as db_utils
import json, os
from dotenv import load_dotenv
from scripts.ybs_dash.main import populate_staker_info
from datetime import datetime

load_dotenv()
staker_info = {}

def main():
    global staker_info
    staker_info = populate_staker_info()

    for token, info in staker_info.items():
        fill_weeks(token, info)

def fill_weeks(token, info):
    ybs = info['ybs']
    utils = info['utils']
    rewards = info['rewards']
    reward_decimals = Contract(info['rewards'].rewardToken()).decimals()
    users = db_utils.query_unique_accounts(token)
    decimals = ybs.decimals()
    max_weeks = ybs.MAX_STAKE_GROWTH_WEEKS()

    current_week = ybs.getWeek() - 1
    last_filled_week = db_utils.get_highest_week_id_for_token(token)
    if not last_filled_week:
        last_filled_week = utilities.get_launch_week(ybs.address) - 1

    print(f'Current week: {current_week}')
    print(f'Last filled week: {last_filled_week}')
    for week in range(current_week, last_filled_week, -1):
        print(f'Iterating over week {week}....')
        try:
            end_block = utilities.get_week_end_block(ybs.address, week)
            supply = ybs.totalSupply(block_identifier=end_block) / 10 ** decimals
            global_weight = ybs.getGlobalWeightAt(week) / 10 ** decimals
            start_ts = utilities.get_week_start_ts(ybs.address, week)
            end_ts = utilities.get_week_end_ts(ybs.address, week)
            stake_map = build_global_stake_map(ybs, week, end_block, max_weeks, decimals)
            db_utils.insert_week_info({
                'week_id': week,
                'token': token,
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
            })
            print(f'Week {week} successfully written.')
        except:
            break
        for user in users:
            weight = ybs.getAccountWeightAt(user, week) / 1e18
            if weight == 0:
                continue
            balance = ybs.balanceOf(user, block_identifier=end_block) / 1e18
            acct_data = ybs.accountData(user, block_identifier=end_block)    
            stake_map, realized = build_user_stake_map(
                ybs, user, acct_data, week, end_block, max_weeks, decimals
            )

            db_utils.insert_user_info({
                'account': user,
                'week_id': week,
                'token': token,
                'weight': weight,
                'balance': balance,
                'boost': weight / balance,
                'stake_map': stake_map,
                'rewards_earned': rewards.getClaimableAt(user, week) / 10 ** reward_decimals,
                'ybs': ybs.address,
                'total_realized': realized,
            })
            print(f'User {user} @ week {week} successfully written.')


def test():
    ybs = Contract('0xF4C6e0E006F164535508787873d86b84fe901975')
    map = build_global_stake_map(
        ybs, 41, utilities.get_week_end_block(ybs.address, 41), 4, 18
    )
    print(map)

def build_global_stake_map(ybs, week, block, max_weeks, decimals):
    pending_map = {}
    for i in range(max_weeks):
        target_week = week + 1 + i
        amt = ybs.globalWeeklyToRealize(
            target_week, block_identifier=block
        )['weight'] / 10 ** decimals
        if amt > 0:
            pending_map[target_week] = {
                'amount': amt,
                'week_start_block': utilities.get_week_start_block(ybs.address, target_week),
                'week_start_ts': utilities.get_week_start_ts(ybs.address, target_week),
                'max_weeks': max_weeks,
            }

    return pending_map

def build_user_stake_map(ybs, user, acct_data, week, block, max_weeks, decimals):
    week_offset = week - acct_data['lastUpdateWeek']
    bitmap = acct_data['updateWeeksBitmap']
    bitstring = format(bitmap, '08b')[::-1][:-(max_weeks-1)]    # Reverse order and trim
    bitarray = [int(char) for char in bitstring]            # Convert to array
    # bitarray = shift_array(bitarray, week_offset) # Adjust for offset
    realized = acct_data['realizedStake'] / 10 ** decimals
    pending_map = {}

    for i, bit in enumerate(bitarray):
        target_week = week - week_offset + (len(bitarray) - 1 - i)
        if int(bit) != 1:
            continue
        if target_week <= week:
            realized += ybs.accountWeeklyToRealize(
                user, target_week, block_identifier=block
            )['weight'] / 10 ** decimals
        else:
            amt = ybs.accountWeeklyToRealize(
                user, target_week, block_identifier=block
            )['weight'] / 10 ** decimals
            pending_map[target_week] = {
                'amount':amt,
                'week_start_block': utilities.get_week_start_block(ybs.address, target_week),
                'week_start_ts': utilities.get_week_start_ts(ybs.address, target_week),
                'max_weeks': max_weeks,
            }

    return pending_map, realized

def shift_array(arr, offset):
    length = len(arr)
    if offset >= length:
        return [0] * length
    return [0] * offset + arr[:length - offset]
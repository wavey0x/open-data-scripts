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
        last_week = info['ybs'].getWeek() - 1
        fill_weeks(token, info, last_week)

    complete_job(last_week)
    assert False

def fill_weeks(token, info, last_week):
    ybs = info['ybs']
    utils = info['utils']
    rewards = info['rewards']
    reward_decimals = Contract(info['rewards'].rewardToken()).decimals()
    users = db_utils.query_unique_accounts(token)
    decimals = ybs.decimals()
    max_weeks = ybs.MAX_STAKE_GROWTH_WEEKS()

    last_filled_week = db_utils.get_highest_week_id_for_token(token)
    to_week = last_filled_week - 1 if not None else - 1

    for week in range(last_week, to_week, -1):
        if week == last_week:
            break
        try:
            end_block = utilities.get_week_end_block(ybs.address, week)
            supply = ybs.totalSupply(block_identifier=end_block) / 10 ** decimals
            global_weight = ybs.getGlobalWeightAt(week) / 10 ** decimals
            start_ts = utilities.get_week_start_ts(ybs.address, week)
            end_ts = utilities.get_week_end_ts(ybs.address, week)
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
                'end_time_str': datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d")
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
            stake_map = build_stake_map(
                ybs, user, acct_data, week, end_block, max_weeks
            )

            db_utils.insert_user_info({
                'account': user,
                'week_id': week,
                'token': token,
                'weight': weight,
                'balance': balance,
                'boost': weight / balance,
                'map': stake_map,
                'rewards_earned': rewards.getClaimableAt(user, week) / 10 ** reward_decimals,
                'ybs': ybs.address,
            })
            print(f'User {user} @ week {week} successfully written.')

def build_stake_map(ybs, user, acct_data, week, block, max_weeks):
    week_offset = week - acct_data['lastUpdateWeek']
    bitmap = acct_data['updateWeeksBitmap']
    bitstring = format(bitmap, '08b')[::-1][:-max_weeks]
    bitarray = [int(char) for char in bitstring]
    realized = acct_data['realizedStake']
    pending_map = {}
    for i, bit in enumerate(bitarray):
        target_week = week + max_weeks - (week_offset + i)
        if int(bit) != 1:
            continue
        target_week = week + max_weeks - (week_offset + i)

        if week_offset > i:
            realized += ybs.accountWeeklyToRealize(
                user, target_week, block_identifier=block
            )['weight']
        else:
            pending_map[target_week] = ybs.accountWeeklyToRealize(
                user, target_week, block_identifier=block
            )['weight']

    return pending_map
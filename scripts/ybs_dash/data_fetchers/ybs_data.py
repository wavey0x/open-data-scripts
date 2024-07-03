from brownie import Contract, chain
from utils import utils as utilities
from constants import WEEK
import json

DATA_FILE_PATH = 'ybs_data.json'

def build_data(token, staker_data):
    height = chain.height
    ybs = staker_data['ybs']
    return { 
        'weekly_data': get_week_data(token, staker_data, height),
        'ybs': ybs,
    }

def get_week_data(token, staker_data, height):
    cached_data = utilities.load_from_json(DATA_FILE_PATH)
    week_data = {}
    last_week_checked = 0
    try:
        week_data = cached_data[token]['weekly_data']
        last_week_checked = int(list(week_data.keys())[-1])
    except:
        if token not in cached_data:
            cached_data[token] = {}
        pass

    data = staker_data
    deploy_block = data['ybs_deploy_block']
    ybs = data['ybs']
    start_time = int(chain[deploy_block].timestamp / WEEK) * WEEK
    current_week = ybs.getWeek(block_identifier=height)
    ts = chain[height].timestamp
    current_week_start_time = int(ts / WEEK) * WEEK
    decimals = ybs.decimals()
     
    for i in range(0, 2_000):
        target_week_start_time = current_week_start_time - (WEEK * i)
        week = max(current_week - i, 0)
        if (
            last_week_checked > week or 
            target_week_start_time < start_time
        ):
            break
        start_block = max(utilities.get_week_start_block(ybs.address, week), deploy_block)
        end_block = utilities.get_week_end_block(ybs.address, week)
        utilities.get_week_end_block
        week_data[week] = {
            'global_weight': ybs.getGlobalWeightAt(week, block_identifier=height) / 10**decimals,
            'global_balance': ybs.totalSupply(block_identifier=end_block) / 10**decimals,
            'start_time': target_week_start_time,
            'start_block': start_block,
        }
        strategy = data['strategy_data']['strategy']
        week_data[week]['system_avg_boost'] = 0 if week_data[week]['global_balance'] == 0 else week_data[week]['global_weight'] / week_data[week]['global_balance']
        week_data[week]['strategy_weight'] = ybs.getAccountWeightAt(strategy, week, block_identifier=height) / 10 ** decimals
        week_data[week]['strategy_balance'] = ybs.balanceOf(strategy, block_identifier=end_block) / 10 ** decimals
        week_data[week]['strategy_boost'] = 0 if week_data[week]['strategy_balance'] == 0 else week_data[week]['strategy_weight'] / week_data[week]['strategy_balance']

    cached_data[token]['weekly_data'] = week_data
    with open(DATA_FILE_PATH, 'w') as file:
        json.dump(cached_data, file, indent=4)

    return cached_data[token]['weekly_data']

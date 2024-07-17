from brownie import Contract, ZERO_ADDRESS, chain
from constants import *
from multicall import Call, Multicall
import utils as utilities
import time, json, datetime
from eth_utils import humanize_seconds

DATA_FILE_PATH = 'ybs_data.json'
WEEK = 60 * 60 * 24 * 7
height = chain.height
def from_wei(value):
    return value / 1e18

YLOCKER_REGISTRY = Contract(YBS_REGISTRY)
YLOCKER_TOKENS = {
    '0xe3668873D944E4A949DA05fc8bDE419eFF543882', # yPRISMA
    '0xFCc5c47bE19d06BF83eB04298b026F81069ff65b', # yCRV
}
staker_info = {}
week_info = {}
users = {}

def main():
    start_time = datetime.datetime.now()
    for token in YLOCKER_TOKENS:
        populate_staker_info(token)
        users = get_all_users(token)
        get_week_data(token)
    end_time = datetime.datetime.now()
    duration = end_time - start_time
    print(f'Script run in {duration} seconds.')
    assert False

def populate_staker_info(token):
    staker_info[token] = {}
    data = staker_info[token]
    data['token'] = Contract(token)
    deployment = YLOCKER_REGISTRY.deployments(token)
    ybs = Contract(deployment['yearnBoostedStaker'])
    rewards_distributor = Contract(deployment['rewardDistributor'])
    utils = Contract(deployment['utilities'])
    data['ybs'] = ybs
    data['rewards'] = rewards_distributor
    data['utils'] = utils
    data['reward_token'] = Contract(data['rewards'].rewardToken())
    data['reward_token_is_v2'] = True
    try:
        data['reward_token_underlying'] = data['reward_token'].asset()
        data['reward_token_is_v2'] = False
    except:
        data['reward_token_underlying'] = Contract(data['reward_token'].token())
        
    data['autocompounder_is_v2'], data['autocompounder'] = lookup_autocompounder(token)
    data['strategy'] = lookup_strategy(data['autocompounder'], data['autocompounder_is_v2'])
    data['ybs_deploy_block'] = utilities.utils.contract_creation_block(ybs.address)

    staker_info[token] = data
    # get_tokens()
    # get_user_details(utils.address)
    # get_global_details()
    # get_v3_vault_details()

def get_all_users(token):
    data = staker_info[token]
    cached_data = utilities.utils.load_from_json(DATA_FILE_PATH)
    last_update = {
        'block': 0,
        'ts': 0,
        'week': 0,
    }
    users = []
    try:
        last_update = cached_data[token]['last_update']
        users = cached_data['users']
    except:
        pass

    ybs = data['ybs']
    logs = ybs.events.Staked.get_logs(
        fromBlock=max(data['ybs_deploy_block'], last_update['block']), 
        toBlock=height
    )
    new_unique_users = set()
    for log in logs:
        new_unique_users.add(log['args']['account'])

    for u in new_unique_users:
        if u not in users:
            users.append(u)

    if token not in cached_data:
        cached_data[token] = {}
    if 'last_update' not in cached_data[token]:
        cached_data[token]['last_update'] = {}
    if 'users' not in cached_data[token]:
        cached_data[token]['users'] = []

    cached_data[token]['users'] = users
    cached_data[token]['last_update'] = {
        'block': height,
        'ts': chain[height].timestamp,
        'week': ybs.getWeek(block_identifier=height),
    }

    with open(DATA_FILE_PATH, 'w') as file:
        json.dump(cached_data, file, indent=4)

    return cached_data[token]['users']

def lookup_autocompounder(token):
    registry = None
    try:
        is_v2 = True
        registry = Contract('0xaF1f5e1c19cB68B30aAD73846eFfDf78a5863319')
        return is_v2, Contract(registry.latestVault(token))
    except:
        is_v2 = False
        registry = Contract('0xff31A1B020c868F6eA3f61Eb953344920EeCA3af')
        return is_v2, Contract(registry.getEndorsedVaults(token)[0])

def lookup_strategy(vault, is_v2):
    dr = 0
    if is_v2:
        for i in range(20):
            s = vault.withdrawalQueue(i)
            if s == ZERO_ADDRESS:
                break
            else:
                if vault.strategies(s)['debtRatio'] > dr:
                    return Contract(s)
    
    queue = list(vault.get_default_queue())
    strategy = ZERO_ADDRESS
    for s in queue:
        current_debt = vault.strategies(s)['current_debt']
        if current_debt > dr:
            strategy = s
            dr = current_debt
    return Contract(strategy)

def get_week_data(token):
    cached_data = utilities.utils.load_from_json(DATA_FILE_PATH)
    week_data = {}
    last_week_checked = 0
    try:
        week_data = cached_data[token]['week_data']
        last_week_checked = int(list(week_data.keys())[-1])
    except:
        if token not in cached_data:
            cached_data[token] = {}
        pass

    data = staker_info[token]
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
        start_block = max(utilities.utils.get_week_start_block(ybs.address, week), deploy_block)
        end_block = utilities.utils.get_week_end_block(ybs.address, week)
        utilities.utils.get_week_end_block
        week_data[week] = {
            'global_weight': ybs.getGlobalWeightAt(week, block_identifier=height) / 10**decimals,
            'global_balance': ybs.totalSupply(block_identifier=end_block) / 10**decimals,
            'start_time': target_week_start_time,
            'start_block': start_block,
        }
        week_data[week]['system_avg_boost'] = 0 if week_data[week]['global_balance'] == 0 else week_data[week]['global_weight'] / week_data[week]['global_balance']
        week_data[week]['strategy_weight'] = ybs.getAccountWeightAt(data['strategy'], week, block_identifier=height) / 10 ** decimals
        week_data[week]['strategy_balance'] = ybs.balanceOf(data['strategy'], block_identifier=end_block) / 10 ** decimals
        week_data[week]['strategy_boost'] = 0 if week_data[week]['strategy_balance'] == 0 else week_data[week]['strategy_weight'] / week_data[week]['strategy_balance']

    cached_data[token]['week_data'] = week_data
    with open(DATA_FILE_PATH, 'w') as file:
        json.dump(cached_data, file, indent=4)

def get_tokens():
    stake_token = staker_info['utils'].TOKEN()
    reward_token = staker_info['rewards'].rewardToken()
    reward_token_underlying = Contract(reward_token).asset()
    staker_info['tokens'] = {
        'stake_token': stake_token,
        'reward_token': reward_token,
        'reward_token_underlying': reward_token_underlying,
    }
    return staker_info['tokens']

def get_token_prices():
    stake_token = staker_info['utils'].TOKEN()
    reward_token = staker_info['rewards'].rewardToken()
    reward_token_underlying = Contract(reward_token).asset()
    prices = utilities.utils.get_prices(tokens=[
        staker_info['tokens']['stake_token'],
        staker_info['tokens']['reward_token'],
        staker_info['tokens']['reward_token_underlying'],
    ])
    if reward_token not in prices:
        prices[reward_token] = prices[reward_token_underlying] * Contract(reward_token).pricePerShare() / 1e18
    return prices

def get_user_details(account):
    prices = get_token_prices()
    utils_address = staker_info['utils'].address
    stake_token = staker_info['stake_token']
    reward_token = staker_info['reward_token']
    stake_token_price = int(prices[stake_token] * 1e18)
    reward_token_price = int(prices[reward_token] * 1e18)

    multi = Multicall([
        Call(utils_address, ['getUserActiveBoostMultiplier(address)(uint256)', account], None),
        Call(utils_address, ['getUserProjectedBoostMultiplier(address)(uint256)', account], None),
        Call(utils_address, ['getUserActiveApr(address,uint256,uint256)(uint256)', account, stake_token_price, reward_token_price], None),
        Call(utils_address, ['getUserProjectedApr(address,uint256,uint256)(uint256)', account, stake_token_price, reward_token_price], None),
    ])
    multi = Multicall([
        Call(utils_address, ['getUserActiveBoostMultiplier(address)(uint256)', account], [('active_boost_multiplier', from_wei)]),
        Call(utils_address, ['getUserProjectedBoostMultiplier(address)(uint256)', account], [('projected_boost_multiplier', from_wei)]),
        Call(utils_address, ['getUserActiveApr(address,uint256,uint256)(uint256)', account, stake_token_price, reward_token_price], [('active_apr', from_wei)]),
        Call(utils_address, ['getUserProjectedApr(address,uint256,uint256)(uint256)', account, stake_token_price, reward_token_price], [('projected_apr', from_wei)]),
    ])
    return multi()
    # getAccountStakeAmountAt(account, week)
    # adjustedAccountWeightAt(account, week)

def get_global_details():
    prices = get_token_prices()
    utils_address = staker_info['utils'].address
    ybs_address = staker_info['ybs'].address
    stake_token = staker_info['stake_token']
    reward_token = staker_info['reward_token']
    stake_token_price = int(prices[stake_token] * 1e18)
    reward_token_price = int(prices[reward_token] * 1e18)

    multi = Multicall([
        Call(utils_address, ['getGlobalActiveBoostMultiplier()(uint256)'], [('global_active_boost_multiplier', from_wei)]),
        Call(utils_address, ['getGlobalProjectedBoostMultiplier()(uint256)'], [('global_projected_boost_multiplier', from_wei)]),
        Call(utils_address, ['getGlobalActiveApr(uint256,uint256)(uint256)', stake_token_price, reward_token_price], [('global_active_apr', from_wei)]),
        Call(utils_address, ['getGlobalProjectedApr(uint256,uint256)(uint256)', stake_token_price, reward_token_price], [('global_projected_apr', from_wei)]),
        Call(utils_address, ['getGlobalMinMaxActiveApr(uint256,uint256)(uint256)', stake_token_price, reward_token_price], [('global_min_max_active_apr', from_wei)]),
        Call(utils_address, ['getGlobalMinMaxProjectedApr(uint256,uint256)(uint256)', stake_token_price, reward_token_price], [('global_min_max_projected_apr', from_wei)]),
        Call(utils_address, ['activeRewardAmount()(uint256)'], [('active_reward_amount', from_wei)]),
        Call(utils_address, ['projectedRewardAmount()(uint256)'], [('projected_reward_amount', from_wei)]),
        Call(ybs_address, ['totalSupply()(uint256)'], [('total_supply', from_wei)]),
        Call(ybs_address, ['getGlobalWeight()(uint256)'], [('global_weight', from_wei)]),
    ])
    return multi()

def get_v3_vault_details():
    prices = get_token_prices()
    vault = Contract('0x11AaE8beE9b1Da827C641540D20e4e664677e06F')
    strategy = Contract('0xA323CCcbCbaDe7806ca5bB9951bebD89A7882bf8')
    asset = Contract(vault.asset())
    idle = asset.balanceOf(strategy) + asset.balanceOf(vault)
    idle /= 1e18
    last_report = strategy.lastReport()
    last_report_humanized = humanize_seconds(last_report)
    ts = int(time.time())
    next_week_start = int(ts / WEEK + 1) * WEEK
    time_until = next_week_start - ts
    time_until_humanized = humanize_seconds(time_until)
    print(f'Tokens to harvest: {idle} (${prices[asset.address]*idle:,.2f})')
    print(f'Last report: {last_report_humanized}')
    print(f'Next week in: {time_until_humanized}')
    assert False
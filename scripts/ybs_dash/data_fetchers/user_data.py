from brownie import Contract, chain
from utils import utils as utilities
import json, os
from dotenv import load_dotenv

load_dotenv()

def main():

    # Open local json
    # Get last_update week 
    # If last_update_week == current week, do nothing
    # If last_update_week < current_week 

    # List of users
    height = chain.height
    json_filename = os.getenv('YBS_JSON_FILE')
    cached_data = utilities.load_from_json(json_filename)
    last_update = cached_data['last_update']
    # last_update_block = cached_data['last_update_block']

    tokens = list(cached_data['data'].keys())
    for token in tokens:
        data = cached_data['data'][token]
        ybs = Contract(data['ybs'])
        pre_users = [] if 'users' not in data else data['users']
        last_week_updated = utilities.get_week_by_ts(ybs.address, last_update)
        last_week_updated_start_block = utilities.get_week_start_block(ybs.address, last_week_updated)
        start_block = deploy_block if len(pre_users) == 0 else last_week_updated_start_block
        deploy_block = utilities.contract_creation_block(ybs.address)
        post_users = get_users(ybs, start_block, height)
        users = list(set(pre_users).union(post_users))
        cached_data['data'][token]['users'] = users

    write_data_as_json(cached_data, os.getenv('PROJECT_DIRECTORY'), os.getenv('YBS_JSON_FILE'))
    assert False

def get_users(ybs, start_block, end_block):
    users = set()
    logs = ybs.events.Staked.get_logs(fromBlock=start_block, toBlock=end_block)
    for log in logs:
        users.add(log.args['account'])
    return list(users)

def write_user_data_to_db(user):
    get_last_user_record(user)

def write_data_as_json(data, project_directory="", json_filename=os.getenv('JSON_FILE')):
    json_file_path = os.path.join(project_directory,json_filename)
    with open(json_file_path, 'w') as file:
        json.dump(data, file, indent=4)

def get_last_user_record():
    return
# token
# week_id
# boost 
# balance
# weight
# apr

def fill_weeks():
    ybs = 
    utils = 
    current_week = ybs.getWeek()
    target_week = current_week - 1
    users = []

    for week in range(target_week, 0, -1):
        block = utilities.get_week_end_block(ybs.address, week)
        balance = ybs.balanceOf(user, block_identifier=block)
        supply = ybs.totalSupply(block_identifier=block)
        weight = ybs.getAccountWeightAt(user, week)
        rewards = 

    for user in users:



def build_stake_map():
    users = [
        '0x742bC36458625BD1Dfc521acaF401334Dcd696Fc',
        '0x4570d5b4177Cf209944e8E3fB1f2A77021ffD5C5',
        '0xab8c43e0c7358B92B32816C2250AA3cdbB35555A',
        '0x0e5437b1b3448D22C07caED31e5BCdC4eC5284a9',
        '0x987095086e4B0828f7B5940c2144E8aCB0f6D7b1',
        '0xBdF157c3bad2164Ce6F9dc607fd115374010c5dC',
    ]
    ybs = Contract('0xE9A115b77A1057C918F997c32663FdcE24FB873f')
    max_weeks = ybs.MAX_STAKE_GROWTH_WEEKS()
    # user = '0x3b3b49D5858542E3614333b1f1b864e16D1b3C9D'
    # user = '0xBdF157c3bad2164Ce6F9dc607fd115374010c5dC'
    for user in users:
        acct_data = ybs.accountData(user)
        current_week = ybs.getWeek()
        week_offset = current_week - acct_data['lastUpdateWeek']

        bitmap = acct_data['updateWeeksBitmap']
        bitstring = format(bitmap, '08b')[::-1][:-max_weeks]
        bitarray = [int(char) for char in bitstring]
        realized = acct_data['realizedStake']
        pending_map = {}
        for i, bit in enumerate(bitarray):
            
            target_week = current_week + max_weeks - (week_offset + i)
            # print(i, bit, current_week - week_offset - i, target_week)
            if int(bit) != 1:
                continue

            target_week = current_week + max_weeks - (week_offset + i)

            # print(f'Target week: {target_week}')
            if week_offset > i:
                realized += ybs.accountWeeklyToRealize(user, target_week)['weight']
            else:
                pending_map[target_week] = ybs.accountWeeklyToRealize(user, target_week)['weight']
        for key in pending_map:
            print(f'{user} {key} --> {pending_map[key]/1e18:,.0f}')
    assert False
    # Deposited in weeks: 0 , 2 , 3
    # Mature in weeks: 4 , 6 , 7
import os
import time
import json
from brownie import Contract, chain
from dotenv import load_dotenv
from utils import utils as utilities
from constants import YBS_REGISTRY
from scripts.ybs_dash.data_fetchers import (
    peg_data, 
    strategy_data,
    token_price_data,
    processing_pipeline_data,
    ybs_data,
)

load_dotenv()

def main():
    staker_data = populate_staker_info()
    current_time = int(time.time())
    current_height = chain.height
    
    for token, data in staker_data.items():
        data.update({
            'peg_data': peg_data.build_data(token, data, 10_000e18),
            'strategy_data': strategy_data.build_data(token, data),
            'price_data': token_price_data.build_data(token, data),
            'pipeline_data': processing_pipeline_data.build_data(token, data),
            'ybs_data': ybs_data.build_data(token, data)
        })
        
        price = data['price_data'][data['reward_token'].address]['price']
        data['strategy_data']['swap_min_usd'] *= price
        data['strategy_data']['swap_max_usd'] *= price

    staker_data = {
        'data': staker_data,
        'last_update': current_time,
        'last_update_block': current_height,
    }
    
    staker_data_str = stringify_dicts(staker_data)
    save_data_as_json(staker_data_str)

def populate_staker_info():
    registry = Contract(YBS_REGISTRY)
    num_tokens = registry.numTokens()
    
    result = {}
    for i in range(num_tokens):
        token = registry.tokens(i)
        deployment = registry.deployments(token)
        
        data = {
            'token': Contract(token),
            'ybs': Contract(deployment['yearnBoostedStaker']),
            'decimals': Contract(token).decimals(),
            'symbol': Contract(token).symbol(),
            'rewards': Contract(deployment['rewardDistributor']),
            'utils': Contract(deployment['utilities']),
            'ybs_deploy_block': utilities.contract_creation_block(deployment['yearnBoostedStaker']),
        }
        
        reward_token = Contract(data['rewards'].rewardToken())
        try:
            reward_token_underlying = Contract(reward_token.asset())
            data['reward_token_is_v2'] = False
        except:
            reward_token_underlying = Contract(reward_token.token())
            data['reward_token_is_v2'] = True
        
        data['reward_token'] = reward_token
        data['reward_token_underlying'] = reward_token_underlying
        
        result[token] = data
        
    return result

def stringify_dicts(data):
    if isinstance(data, dict):
        return {key: stringify_dicts(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [stringify_dicts(item) for item in data]
    elif isinstance(data, Contract):
        return data.address
    return data

def save_data_as_json(data):
    project_directory = os.getenv('PROJECT_DIRECTORY')
    json_filename = os.getenv('YBS_JSON_FILE')
    json_file_path = os.path.join(project_directory, json_filename)
    
    with open(json_file_path, 'w') as file:
        json.dump(data, file, indent=4)

if __name__ == "__main__":
    main()
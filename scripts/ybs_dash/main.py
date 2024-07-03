from brownie import Contract
import time, json, subprocess, os, datetime
from utils import utils as utilities
from constants import YBS_REGISTRY
# Import data fetchers
from scripts.ybs_dash.data_fetchers import (
    peg_data, 
    strategy_data,
    token_price_data, #, burner, voter, gauge_controller
    processing_pipeline_data,
    ybs_data,
)

staker_data = {}
staker_data_str = {}

def main():
    global staker_data
    staker_data = populate_staker_info()
    for token in staker_data:
        staker_data[token]['peg_data'] = peg_data.build_data(token, staker_data[token], 10_000e18)
        staker_data[token]['strategy_data'] = strategy_data.build_data(token, staker_data[token])
        staker_data[token]['price_data'] = token_price_data.build_data(token, staker_data[token])
        staker_data[token]['pipeline_data'] = processing_pipeline_data.build_data(token, staker_data[token])
        staker_data[token]['ybs_data'] = ybs_data.build_data(token, staker_data[token])

        # Fill in more data
        price = staker_data[token]['price_data'][staker_data[token]['reward_token'].address]['price']
        staker_data[token]['strategy_data']['swap_min_usd'] *= price
        staker_data[token]['strategy_data']['swap_max_usd'] *= price

    global staker_data_str
    staker_data_str = stringify_dicts(staker_data)
    json_filename = os.getenv('YBS_JSON_FILE')
    project_directory = os.getenv('TARGET_PROJECT_DIRECTORY')
    write_data_as_json(staker_data_str, project_directory, json_filename)
    

def populate_staker_info():
    result = {}
    registry = Contract(YBS_REGISTRY)
    num_tokens = registry.numTokens()
    for i in range(num_tokens):
        token = registry.tokens(i)
        data = {}
        data['token'] = Contract(token)
        deployment = registry.deployments(token)
        ybs = Contract(deployment['yearnBoostedStaker'])
        rewards_distributor = Contract(deployment['rewardDistributor'])
        utils = Contract(deployment['utilities'])
        data['ybs'] = ybs
        data['decimals'] = data['token'].decimals()
        data['symbol'] = data['token'].symbol()
        data['rewards'] = rewards_distributor
        data['utils'] = utils
        data['ybs_deploy_block'] = utilities.contract_creation_block(ybs.address)
        data['reward_token'] = Contract(data['rewards'].rewardToken())
        data['reward_token_is_v2'] = True
        try:
            data['reward_token_underlying'] = Contract(data['reward_token'].asset())
            data['reward_token_is_v2'] = False
        except:
            data['reward_token_underlying'] = Contract(data['reward_token'].token())
        result[token] = data
    return result


def stringify_dicts(data):
    """
    Recursively replace eth-brownie Contract objects in a dictionary with their address strings.

    Args:
    data (dict): The dictionary to process.

    Returns:
    dict: The processed dictionary with Contract objects replaced by their addresses.
    """
    if isinstance(data, dict):
        # Go through each dictionary item
        return {key: stringify_dicts(value) for key, value in data.items()}
    elif isinstance(data, list):
        # Process each element in the list
        return [stringify_dicts(item) for item in data]
    elif isinstance(data, Contract):
        # Replace Contract object with its address
        return data.address
    else:
        # Return the item as is if it's neither a dict, a list, nor a Contract
        return data


def write_data_as_json(data, project_directory="", json_filename=os.getenv('JSON_FILE')):
    json_file_path = os.path.join(project_directory,json_filename)
    with open(json_file_path, 'w') as file:
        json.dump(data, file, indent=4)
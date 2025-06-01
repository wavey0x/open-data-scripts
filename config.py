import os

# Project directories
PROJECT_DIRECTORY = './data'

# JSON file paths
YBS_JSON_FILE = 'ybs_data.json'
PRISMA_JSON_FILE = 'prisma_liquid_locker_data.json'
RESUPPLY_JSON_FILE = 'resupply_market_data.json'
RAW_BOOST_JSON_FILE = 'raw_boost_data.json'

# Contract addresses
YBS_REGISTRY = "0x262be1d31d0754399d8d5dc63B99c22146E9f738"
RESUPPLY_REGISTRY = "0x10101010E0C3171D894B71B3400668aF311e7D94"
RESUPPLY_DEPLOYER = "0x5555555524De7C56C1B20128dbEAace47d2C0417"

# Helper function to get full file path
def get_json_path(filename):
    return os.path.join(PROJECT_DIRECTORY, filename) 
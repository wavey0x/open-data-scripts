from brownie import Contract, chain
from utils.utils import contract_creation_block
from config import RESUPPLY_REGISTRY
from pprint import pprint

def main():
    registry = Contract(RESUPPLY_REGISTRY)
    pairs = registry.getAllPairAddresses()
    deploy_info = {}
    for pair in pairs:
        deploy_block = contract_creation_block(pair)
        deploy_ts = chain[deploy_block].timestamp
        pair = Contract(pair)
        name = pair.name()
        collateral = Contract(pair.collateral())
        protocol_id = 0
        if not hasattr(collateral, 'collateral_token'):
            protocol_id = 1

        # Make sure 
        if protocol_id == 0:
            assert 'CurveLend' in name, f"CurveLend not in name: {name}"
        else:
            assert protocol_id == 1, f"protocol_id is not 1: {protocol_id}"
            assert 'FraxLend' not in name, f"FraxLend in name: {name}"

        
        deploy_info[pair.address] = {
            "protocol_id": protocol_id,
            "deploy_block": deploy_block,
            "deploy_ts": deploy_ts,
            "name": name,
        }
    
    import json
    with open("deploy_info.json", "w") as f:
        json.dump(deploy_info, f, indent=4)

if __name__ == "__main__":
    main()
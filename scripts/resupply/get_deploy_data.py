from brownie import Contract, chain
from utils.utils import contract_creation_block
from config import RESUPPLY_DEPLOYER, RESUPPLY_REGISTRY

def main():
    registry = Contract(RESUPPLY_REGISTRY)
    deployer = Contract(RESUPPLY_DEPLOYER)
    pairs = registry.getAllPairAddresses()
    deploy_info = {}
    for pair_address in pairs:
        deploy_block = contract_creation_block(pair_address)
        deploy_ts = chain[deploy_block].timestamp
        pair = Contract(pair_address)
        name = pair.name()
        protocol_id = deployer.deployInfo(pair_address)[0]

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

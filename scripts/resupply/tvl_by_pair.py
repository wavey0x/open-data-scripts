import pylab
import numpy as np
import time
from brownie import Contract, chain, multicall
from collections import defaultdict
from datetime import datetime
from config import RESUPPLY_REGISTRY, RESUPPLY_DEPLOYER
from utils.utils import closest_block_after_timestamp

# Approximate blocks per day on Ethereum
BLOCKS_PER_DAY = 7200
N_DAYS = 180  # 6 months


def main():
    start_time = time.time()

    # Get registry and all pairs
    registry = Contract(RESUPPLY_REGISTRY)
    deployer = Contract(RESUPPLY_DEPLOYER)
    pairs = registry.getAllPairAddresses()
    pair_contracts = [Contract(pair) for pair in pairs]

    print(f"Found {len(pair_contracts)} pairs")

    # Get deployment info for each pair
    pair_deploy_blocks = {}
    pair_names = {}

    print("Getting deployment blocks for each pair...")
    with multicall():
        for i, pair in enumerate(pair_contracts):
            pair_names[i] = pair.name()
            deploy_info = deployer.deployInfo(pair.address)
            # deploy_info returns (protocol_id, deploy_time, ...)
            deploy_time = deploy_info[1]
            pair_deploy_blocks[i] = closest_block_after_timestamp(deploy_time)

    print(f"Pairs and deployment blocks:")
    for i in range(len(pair_contracts)):
        print(f"  {pair_names[i]}: block {pair_deploy_blocks[i]}")

    # Calculate block range for last 6 months
    current_block = chain.height
    blocks_back = BLOCKS_PER_DAY * N_DAYS
    start_block = max(0, current_block - blocks_back)

    # Sample once per day
    blocks = [int(b) for b in np.linspace(start_block, current_block, N_DAYS)]

    times = []
    debt_by_pair = defaultdict(list)

    # Sample debt at each block
    for block in blocks:
        timestamp = chain[block].timestamp
        times.append(datetime.fromtimestamp(timestamp))

        # Only query pairs that existed at this block
        pairs_to_query = [i for i in range(len(pair_contracts)) if pair_deploy_blocks[i] <= block]

        # Use multicall to batch all queries for pairs that existed
        debt_values = {}
        with multicall(block_identifier=block):
            for i in pairs_to_query:
                borrow = pair_contracts[i].totalBorrow()
                # Convert to millions: divide by 1e18 (wei to USD) then by 1e6 (to millions)
                debt_values[i] = borrow[0] / 1e18 / 1e6

        # Append values for all pairs (0 if didn't exist yet)
        for i in range(len(pair_contracts)):
            debt_by_pair[i].append(debt_values.get(i, 0))

        print(f"Block {block} ({datetime.fromtimestamp(timestamp)}): queried {len(pairs_to_query)}/{len(pair_contracts)} pairs")

    # Plot results
    MIN_DEBT_THRESHOLD = 0.15  # 150k in millions
    MAX_DEBT_THRESHOLD = 6.0   # 5M in millions
    for i in range(len(pair_contracts)):
        # Only plot pairs with debt between 150k and 5M
        max_debt = max(debt_by_pair[i])
        if MIN_DEBT_THRESHOLD <= max_debt <= MAX_DEBT_THRESHOLD:
            # Trim label: "Resupply Pair (Platform: token1/token2) - num" -> "token1/token2 - num"
            label = pair_names[i].split(': ', 1)[1].replace(') ', ' ') if ': ' in pair_names[i] else pair_names[i]
            pylab.plot(times, debt_by_pair[i], label=label)

    pylab.xlabel('Date')
    pylab.ylabel('Total Debt (M)')
    pylab.title('Resupply Pairs Total Debt (last 6 months)')
    pylab.xticks(rotation=45, ha='right')
    pylab.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    pylab.tight_layout()

    elapsed_time = time.time() - start_time
    print(f"\nData processing completed in {elapsed_time:.2f} seconds")

    pylab.show()


if __name__ == "__main__":
    main()

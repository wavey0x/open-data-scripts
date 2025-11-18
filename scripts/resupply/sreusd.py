from brownie import chain, interface, Contract
from config import DAY, UTILITIES, SREUSD
from utils.utils import closest_block_before_timestamp, contract_creation_block

def get_sreusd_data():
    utils = interface.IUtilities(UTILITIES)
    sreusd = Contract(SREUSD)
    current_time = int(chain.time())
    deploy_block = contract_creation_block(SREUSD)
    data_points = []

    # Sample twice per day (00:00 and 12:00 UTC) for last 30 days
    for day_offset in range(30, 0, -1):
        for hour_offset in [0, 12]:
            timestamp = (current_time - (day_offset * DAY)) // DAY * DAY + hour_offset * 3600
            block = closest_block_before_timestamp(timestamp)
            if block >= deploy_block:
                rate = utils.sreusdRates(block_identifier=block)
                total_assets = sreusd.totalAssets(block_identifier=block) / 1e18
                data_points.append({
                    'block': block,
                    'timestamp': chain[block].timestamp,
                    'rate': rate,
                    'apr': rate * 365 * 86400 / 1e18,
                    'total_assets': total_assets
                })

    # Most recent data point
    rate = utils.sreusdRates()
    total_assets = sreusd.totalAssets() / 1e18
    data_points.append({
        'block': chain.height,
        'timestamp': current_time,
        'rate': rate,
        'apr': rate * 365 * 86400 / 1e18,
        'total_assets': total_assets
    })

    return data_points
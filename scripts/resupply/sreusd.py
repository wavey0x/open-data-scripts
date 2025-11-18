from brownie import chain, interface
from config import DAY, UTILITIES, SREUSD
from utils.utils import closest_block_before_timestamp, contract_creation_block

def get_sreusd_data():
    utils = interface.IUtilities(UTILITIES)
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
                data_points.append({
                    'timestamp': chain[block].timestamp,
                    'apr': rate * 365 * 86400 / 1e18
                })

    # Most recent data point
    rate = utils.sreusdRates()
    data_points.append({
        'timestamp': current_time,
        'apr': rate * 365 * 86400 / 1e18
    })

    return data_points
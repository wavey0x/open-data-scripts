from brownie import Contract, chain, ZERO_ADDRESS, interface
import json
import os
import time
from config import (
    STABLECOIN,
    WEEK,
    DAY,
    UTILITIES,
    SREUSD
)
import requests
from utils.utils import get_prices, closest_block_before_timestamp
from .authorizations import get_all_selectors

def get_sreusd_data():
    sreusd = Contract(SREUSD)
    current_time = int(chain.time())
    data_points = []

    # Sample twice per day (00:00 and 12:00 UTC) for last 30 days
    for day_offset in range(30, 0, -1):
        day_start = (current_time - (day_offset * DAY)) // DAY * DAY

        # 00:00 UTC
        block = closest_block_before_timestamp(day_start)
        rate = sreusd.sreusdRates(block_identifier=block)
        data_points.append({
            'timestamp': chain[block].timestamp,
            'apr': rate * 365 * 86400 / 1e18
        })

        # 12:00 UTC
        block = closest_block_before_timestamp(day_start + DAY // 2)
        rate = sreusd.sreusdRates(block_identifier=block)
        data_points.append({
            'timestamp': chain[block].timestamp,
            'apr': rate * 365 * 86400 / 1e18
        })

    # Most recent data point
    rate = sreusd.sreusdRates()
    data_points.append({
        'timestamp': current_time,
        'apr': rate * 365 * 86400 / 1e18
    })

    return data_points
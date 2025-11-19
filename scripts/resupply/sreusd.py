from brownie import chain, interface, Contract
from config import DAY, UTILITIES, SREUSD
from utils.utils import closest_block_before_timestamp, contract_creation_block
import requests
import time

# Global cache for CoinGecko tokens
COINGECKO_TOKENS = None

# sreUSD/crvUSD CurveLend market address
SREUSD_MARKET = '0xC32B0Cf36e06c790A568667A17DE80cba95A5Aad'

def get_coingecko_tokens():
    global COINGECKO_TOKENS
    if COINGECKO_TOKENS is not None:
        return COINGECKO_TOKENS

    url = f"https://tokens.coingecko.com/uniswap/all.json"
    max_retries = 3
    base_delay = 2

    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=5)

            if response.status_code == 429:
                delay = base_delay * (2 ** attempt)
                print(f"Rate limited by CoinGecko, waiting {delay} seconds...")
                time.sleep(delay)
                continue

            if response.status_code != 200:
                print(f"Warning: CoinGecko request failed with status {response.status_code}")
                return None

            COINGECKO_TOKENS = response.json()
            return COINGECKO_TOKENS

        except (requests.exceptions.RequestException, requests.exceptions.JSONDecodeError) as e:
            print(f"Warning: Failed to fetch CoinGecko tokens: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            return None

    return None

def get_token_logo_url(token_address):
    try:
        # First try CoinGecko using cached data
        if token_address not in [
            '0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E', # crvusd
            '0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0', # wsteth
        ]:
            tokens = get_coingecko_tokens()
            if tokens and 'tokens' in tokens:
                for token in tokens['tokens']:
                    if token['address'].lower() == token_address.lower():
                        return token['logoURI']

        # Fallback to SmolDapp token assets
        return f"https://assets.smold.app/api/token/1/{token_address}/logo-32.png"

    except requests.exceptions.RequestException as e:
        print(f"Warning: Request failed for token {token_address}: {str(e)}")
        return None

    return None

def get_sreusd_market_data():
    """Collect underlying CurveLend market data for sreUSD/crvUSD market"""
    market = Contract(SREUSD_MARKET)

    # Get token information
    collat_token_address = market.collateral_token()
    collat_token = Contract(collat_token_address)
    collateral_token_decimals = collat_token.decimals()
    collateral_token_symbol = collat_token.symbol()

    deposit_token_address = market.asset()
    deposit_token = Contract(deposit_token_address)
    deposit_token_symbol = deposit_token.symbol()

    # Get controller and calculate metrics
    controller = Contract(market.controller())
    total_debt = controller.total_debt() / 1e18
    liquidity = deposit_token.balanceOf(controller.address) / 1e18
    total_supplied = market.totalAssets() / 1e18

    utilization = 0
    if total_supplied > 0:
        utilization = total_debt / total_supplied

    # Get interest rates
    amm = Contract(controller.amm())
    lend_rate = market.lend_apr() / 1e18
    borrow_rate = amm.rate() * 365 * 86400 / 1e18

    # Calculate global LTV
    collat_value = collat_token.balanceOf(amm.address) / 10 ** collateral_token_decimals * amm.price_oracle() / 1e18
    debt_value = controller.total_debt() / 1e18
    global_ltv = 0
    if collat_value > 0:
        global_ltv = debt_value / collat_value

    # Get token logos
    deposit_token_logo = get_token_logo_url(deposit_token_address)
    collateral_token_logo = get_token_logo_url(collat_token_address)

    return {
        'market_name': 'CurveLend',
        'market': SREUSD_MARKET,
        'collat_token': collat_token_address,
        'deposit_token': deposit_token_address,
        'deposit_token_symbol': deposit_token_symbol,
        'collateral_token_symbol': collateral_token_symbol,
        'collateral_token_decimals': collateral_token_decimals,
        'deposit_token_logo': deposit_token_logo,
        'collateral_token_logo': collateral_token_logo,
        'controller': controller.address,
        'interest_rate_contract': amm.address,
        'total_debt': total_debt,
        'total_supplied': total_supplied,
        'liquidity': liquidity,
        'utilization': utilization,
        'lend_rate': lend_rate,
        'borrow_rate': borrow_rate,
        'global_ltv': global_ltv,
    }

def get_sreusd_data():
    """Collect sreUSD data including market snapshot and historical rates/TVL"""
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

    # Return nested structure with market data and historical data
    return {
        'market_data': get_sreusd_market_data(),
        'historical_data': data_points
    }
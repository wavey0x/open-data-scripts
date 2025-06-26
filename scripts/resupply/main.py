from brownie import Contract, chain, ZERO_ADDRESS
import json
import os
import time
from config import RESUPPLY_JSON_FILE, RESUPPLY_REGISTRY, RESUPPLY_DEPLOYER, GOV_TOKEN, RESUPPLY_UTILS, get_json_path
import requests
from utils.utils import get_prices

registry = Contract(RESUPPLY_REGISTRY)
deployer = Contract(RESUPPLY_DEPLOYER)
utils = Contract(RESUPPLY_UTILS)
rsup_price = 0

# Global cache for CoinGecko tokens
COINGECKO_TOKENS = None

class MarketData:
    pair: str
    name: str  # collat token symbol / borrow token symbol
    collat_token: str
    deposit_token: str
    deposit_token_logo: str
    collateral_token_decimals: int
    collateral_token_logo: str
    deposit_token_symbol: str
    collateral_token_symbol: str
    utilization: float
    liquidity: float
    lend_rate: float
    borrow_rate: float
    interest_rate_contract: str
    global_ltv: float
    total_debt: float
    total_supplied: float
    controller: str
    resupply_borrow_limit: float
    resupply_total_debt: float
    resupply_utilization: float
    resupply_available_liquidity: float
    resupply_borrow_rate: float
    resupply_ltv: float
    resupply_rewards_rate: float

    def to_json(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

    def __init__(self, pair):
        self.pair = pair
        pair = Contract(pair)
        
        self.name = pair.name()
        print(f'Processing pair: {self.name} {pair.address}')
        self.market = pair.collateral()
        market = Contract(self.market)
        self.protocol_id = 0 if hasattr(market, 'collateral_token') else 1
        self.resupply_available_liquidity = pair.totalDebtAvailable() / 1e18
        borrow = pair.totalBorrow()
        self.resupply_total_debt = borrow[0] / 1e18
        self.resupply_borrow_limit = pair.borrowLimit() / 1e18
        self.resupply_utilization = 0
        if self.resupply_borrow_limit > 0:
            self.resupply_utilization = self.resupply_total_debt / self.resupply_borrow_limit
        rate_info = pair.previewAddInterest()['_newCurrentRateInfo'].dict()
        self.resupply_borrow_rate = rate_info['ratePerSec'] * 365 * 86400 / 1e18
        self.resupply_total_collateral = pair.totalCollateral() / 1e18
        oracle = pair.exchangeRateInfo()['oracle']
        price = Contract(oracle).getPrices(self.market) / 1e18
        self.resupply_pps = price
        self.resupply_total_supplied = self.resupply_total_collateral * price
        if self.protocol_id == 0:
            self.resupply_total_collateral /= 1_000
        self.resupply_ltv = 0
        if self.resupply_total_supplied > 0:
            self.resupply_ltv = self.resupply_total_debt / self.resupply_total_supplied
        _, rates = utils.getPairRsupRate(pair.address)
        self.resupply_lend_rate = 0
        if borrow[1] > 0 and borrow[0] > 0:
            price_of_deposit = borrow[0] / borrow[1]
            self.resupply_lend_rate = utils.apr(rates[0] / 1e36, rsup_price * 1e18, price_of_deposit * 1e18) / 1e18
        
        if self.protocol_id == 0:
            self.market_name = 'CurveLend'
            market = Contract(self.market)
            self.collat_token = market.collateral_token()
            collat_token = Contract(self.collat_token)
            self.collateral_token_decimals = collat_token.decimals()
            self.deposit_token = market.asset()
            deposit_token = Contract(self.deposit_token)
            self.deposit_token_symbol = deposit_token.symbol()
            self.collateral_token_symbol = collat_token.symbol()
            asset = Contract(self.deposit_token)
            controller = Contract(market.controller())
            self.controller = controller.address
            self.total_debt = controller.total_debt() / 1e18
            self.liquidity = asset.balanceOf(controller.address) / 1e18
            self.total_supplied = market.totalAssets() / 1e18
            self.utilization = 0
            if self.total_supplied > 0:
                self.utilization = self.total_debt / self.total_supplied
            oracle = Contract(controller.amm())
            self.lend_rate = market.lend_apr() / 1e18
            self.borrow_rate = oracle.rate() * 356 * 86400 / 1e18
            self.interest_rate_contract = oracle.address
            collat_value = collat_token.balanceOf(oracle.address) / 10 ** self.collateral_token_decimals * oracle.price_oracle() / 1e18
            debt_value = controller.total_debt() / 1e18
            self.global_ltv = 0
            if collat_value > 0:
                self.global_ltv = debt_value / collat_value
            self.deposit_token_logo = get_token_logo_url(self.deposit_token)
            self.collateral_token_logo = get_token_logo_url(self.collat_token)
            
        elif self.protocol_id == 1:
            self.market_name = 'FraxLend'
            market = Contract(self.market)
            self.collat_token = market.collateralContract()
            collat_token = Contract(self.collat_token)
            self.deposit_token = market.asset()
            asset = Contract(self.deposit_token)
            self.deposit_token_symbol = asset.symbol()
            self.collateral_token_symbol = collat_token.symbol()
            self.collateral_token_decimals = collat_token.decimals()
            rate_info = market.exchangeRateInfo()
            oracle = Contract(rate_info['oracle'])
            price_data = oracle.getPrices().dict()
            price = price_data['_priceLow'] if '_priceLow' in price_data else price_data['priceLow']
            price = 10 ** self.collateral_token_decimals / price
            collat_value = market.totalCollateral() / 10 ** self.collateral_token_decimals * price
            self.total_debt = market.totalBorrow()[0] / 1e18
            self.global_ltv = self.total_debt / collat_value
            self.liquidity = asset.balanceOf(market.address) / 1e18
            self.total_supplied = market.totalAssets() / 1e18
            self.utilization = 0
            if self.total_supplied > 0:
                self.utilization = self.total_debt / self.total_supplied
            rate_info = market.previewAddInterest()['_newCurrentRateInfo'].dict()
            self.borrow_rate = rate_info['ratePerSec'] * 365 * 86400 / 1e18
            fee = rate_info['feeToProtocolRate'] / market.FEE_PRECISION()
            self.lend_rate = self.borrow_rate * (1 - fee) * self.utilization 
            self.interest_rate_contract = market.rateContract()
            self.controller = "0x0000000000000000000000000000000000000000"
            self.deposit_token_logo = get_token_logo_url(self.deposit_token)
            self.collateral_token_logo = get_token_logo_url(self.collat_token)

def get_resupply_pairs_and_collaterals():
    global rsup_price 
    rsup_price = get_prices([GOV_TOKEN])[GOV_TOKEN]
    pairs = registry.getAllPairAddresses()
    market_data = []
    for pair in pairs:
        data = MarketData(pair)
        market_data.append(data.to_json())
    return market_data

def stringify_dicts(data):
    if isinstance(data, dict):
        return {key: stringify_dicts(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [stringify_dicts(item) for item in data]
    return data

def save_data_as_json(data):
    json_file_path = get_json_path(RESUPPLY_JSON_FILE)
    os.makedirs(os.path.dirname(json_file_path), exist_ok=True)
    
    # Remove None values from the data
    def remove_none_values(d):
        if isinstance(d, dict):
            return {k: remove_none_values(v) for k, v in d.items() if v is not None}
        elif isinstance(d, list):
            return [remove_none_values(v) for v in d if v is not None]
        return d
    
    cleaned_data = remove_none_values(data)
    
    with open(json_file_path, 'w') as file:
        json.dump(cleaned_data, file, indent=4)

def get_coingecko_tokens():
    global COINGECKO_TOKENS
    if COINGECKO_TOKENS is not None:
        return COINGECKO_TOKENS
        
    url = f"https://tokens.coingecko.com/uniswap/all.json"
    max_retries = 3
    base_delay = 2  # Start with 2 second delay
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=5)
            
            if response.status_code == 429:  # Rate limited
                delay = base_delay * (2 ** attempt)  # Exponential backoff
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

def main():
    # Initialize CoinGecko tokens cache
    get_coingecko_tokens()
    
    # Get market data
    market_data = get_resupply_pairs_and_collaterals()
    
    # Add metadata
    current_time = int(time.time())
    current_height = chain.height
    
    data = {
        'data': market_data,
        'last_update': current_time,
        'last_update_block': current_height,
    }
    
    # Stringify any Contract objects and save
    data_str = stringify_dicts(data)
    save_data_as_json(data_str)
    
    print("Resupply market data saved successfully.")

if __name__ == "__main__":
    main()
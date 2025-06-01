from brownie import Contract, chain
import json
import os
import time
from config import RESUPPLY_JSON_FILE, RESUPPLY_REGISTRY, RESUPPLY_DEPLOYER, get_json_path

registry = Contract(RESUPPLY_REGISTRY)
deployer = Contract(RESUPPLY_DEPLOYER)

class MarketData:
    pair: str
    name: str  # collat token symbol / borrow token symbol
    collat_token: str
    deposit_token: str
    deposit_token_logo: str
    collateral_token_logo: str
    deposit_token_symbol: str
    collateral_token_symbol: str
    utilization: float
    liquidity: float
    interest_rate: float
    interest_rate_contract: str
    global_ltv: float
    total_debt: float
    total_supplied: float

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
        if self.protocol_id == 0:
            self.market_name = 'CurveLend'
            market = Contract(self.market)
            self.collat_token = market.collateral_token()
            collat_token = Contract(self.collat_token)
            self.deposit_token = market.asset()
            deposit_token = Contract(self.deposit_token)
            self.deposit_token_symbol = deposit_token.symbol()
            self.collateral_token_symbol = collat_token.symbol()
            asset = Contract(self.deposit_token)
            controller = Contract(market.controller())
            self.total_debt = controller.total_debt() / 1e18
            self.liquidity = asset.balanceOf(controller.address) / 1e18
            self.total_supplied = market.totalAssets() / 1e18
            self.utilization = self.total_debt / self.total_supplied
            oracle = Contract(controller.amm())
            self.interest_rate = oracle.rate() * 356 * 86400 / 1e18
            self.interest_rate_contract = oracle.address
            
            collat_value = collat_token.balanceOf(oracle.address) * oracle.price_oracle() / 1e36
            debt_value = controller.total_debt() / 1e18
            self.global_ltv = debt_value / collat_value
            
        elif self.protocol_id == 1:
            self.market_name = 'FraxLend'
            market = Contract(self.market)
            self.collat_token = market.collateralContract()
            collat_token = Contract(self.collat_token)
            self.deposit_token = market.asset()
            asset = Contract(self.deposit_token)
            self.deposit_token_symbol = asset.symbol()
            self.collateral_token_symbol = collat_token.symbol()
            rate_info = market.exchangeRateInfo()
            oracle = Contract(rate_info['oracle'])
            price_data = oracle.getPrices().dict()
            price = price_data['_priceLow'] if '_priceLow' in price_data else price_data['priceLow']
            price = 1e18 / price
            collat_value = market.totalCollateral() / 1e18 * price
            self.total_debt = market.totalBorrow()[0] / 1e18
            self.global_ltv = self.total_debt / collat_value
            self.liquidity = asset.balanceOf(market.address) / 1e18
            self.total_supplied = market.totalAssets() / 1e18
            self.utilization = self.total_debt / self.total_supplied
            rate_info = market.currentRateInfo().dict()
            self.borrow_rate = rate_info['ratePerSec'] * 356 * 86400 / 1e18
            fee = rate_info['feeToProtocolRate'] / market.FEE_PRECISION()
            self.lend_rate = self.borrow_rate * (1 - fee) * 1e18
            self.interest_rate_contract = market.rateContract()

def get_curvelend_market_data(market):
    collat_token = Contract(market.collateral_token())
    market = Contract(market)
    asset = Contract(market.asset())
    controller = Contract(market.controller())
    total_debt = controller.total_debt()
    liquidity = asset.balanceOf(controller.address)
    utilization = total_debt / (total_debt + liquidity)
    oracle = Contract(controller.amm())
    borrow_rate = oracle.rate() * 356 * 86400 / 1e18
    lending_rate = market.lend_apr() / 1e18
    collat_value = collat_token.balanceOf(oracle.address) * oracle.price_oracle()
    debt_value = controller.total_debt()
    global_ltv = debt_value / collat_value
    return 0

def get_fraxlend_market_data(market): 
    market = Contract(market)
    return 0

def get_resupply_pairs_and_collaterals():
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
    elif isinstance(data, Contract):
        return data.address
    return data

def save_data_as_json(data):
    json_file_path = get_json_path(RESUPPLY_JSON_FILE)
    os.makedirs(os.path.dirname(json_file_path), exist_ok=True)
    
    with open(json_file_path, 'w') as file:
        json.dump(data, file, indent=4)

def main():
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
from brownie import Contract, chain, ZERO_ADDRESS, interface
import json
import os
import time
from config import (
    RESUPPLY_JSON_FILE,
    RESUPPLY_REGISTRY,
    RESUPPLY_DEPLOYER,
    GOV_TOKEN,
    STABLECOIN,
    get_json_path,
    INSURANCE_POOL,
    RETENTION_PROGRAM,
    WEEK,
    DAY,
    UTILITIES,
    LOAN_REPAYER,
    LOAN_CONVERTER,
    BAD_DEBT_REPAYER
)
import requests
from utils.utils import get_prices, closest_block_before_timestamp
from .authorizations import get_all_selectors

registry = Contract(RESUPPLY_REGISTRY)
deployer = Contract(RESUPPLY_DEPLOYER)
utils = Contract(UTILITIES)
rsup_price = 0
ir_samples_to_check = []

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
    resupply_historical_borrow_rates: list[float]
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
        self.resupply_borrow_rate = utils.getPairInterestRate.call(pair) * 365 * 86400 / 1e18
        self.resupply_historical_borrow_rates = []
        for i in range(len(ir_samples_to_check)):
            historical_rate = utils.getPairInterestRate.call(pair, block_identifier=ir_samples_to_check[i]['block']) * 365 * 86400 / 1e18
            self.resupply_historical_borrow_rates.append(
                {
                    'block': ir_samples_to_check[i]['block'],
                    'ts': ir_samples_to_check[i]['ts'],
                    'borrow_rate': historical_rate
                }
            )
        self.resupply_total_collateral = pair.totalCollateral() / 1e18
        oracle = pair.exchangeRateInfo()['oracle']
        if oracle != ZERO_ADDRESS:
            price = Contract(oracle).getPrices(self.market) / 1e18
        else:
            market.convertToAssets(1e18) / 1e18
            price = 1e18
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
    global rsup_price, ir_samples_to_check
    start_ts = chain.time() - (7 * DAY)
    step_size = DAY / 2
    steps = 7 * 2
    for i in range(steps):
        block = closest_block_before_timestamp(start_ts + i * step_size)
        ts = start_ts + i * step_size
        ir_samples_to_check.append(
            {
                'block': block,
                'ts': ts
            }
        )
        print(f"Added sample {i}: block {block}, ts {ts}")
    print(f"Total samples: {len(ir_samples_to_check)}")
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

def load_retention_snapshot_data():
    snapshot_path = os.path.join(os.path.dirname(__file__), 'data/ip_retention_snapshot.json')
    with open(snapshot_path, 'r') as f:
        snapshot_data = json.load(f)
    return snapshot_data

def get_retention_program_data(current_height):
    ts = chain.time()
    LAUNCH_TS = 1752807599
    remaining_rsup = 2_500_000
    time_remaining = 52 * WEEK
    ip = Contract(INSURANCE_POOL)
    
    snapshot_data = load_retention_snapshot_data()
    
    # Calculate total supply original (sum of all original balances)
    total_supply_original = sum(snapshot_data.values())
    total_assets_original = ip.convertToAssets(total_supply_original) / 1e18
    total_supply_original /= 10 ** 18
    
    # TODO: Need retention contract address to get current balances
    # For now, we'll use the original balances as a placeholder
    retention_contract = Contract(RETENTION_PROGRAM)
    total_supply_remaining = retention_contract.totalSupply() / 1e18
    total_assets_remaining = ip.convertToAssets(total_supply_remaining * 10 ** 18) / 1e18
    
    all_tokens = [GOV_TOKEN, STABLECOIN]
    reward_tokens, reward_rates = utils.getInsurancePoolRewardRates()
    all_tokens.extend(reward_tokens)
    
    prices = get_prices(all_tokens) # API call to defi llama
    rsup_price = prices[GOV_TOKEN]
    stablecoin_price = prices[STABLECOIN]
    
    # Calculate base APR from insurance pool rewards
    base_apr = 0
    for i, token in enumerate(reward_tokens):
        if token in prices and prices[token] > 0:
            token_apr = utils.apr(
                reward_rates[i], 
                prices[token] * 1e18,    # price of reward token
                ip.convertToAssets(stablecoin_price * 1e18)  # price of deposit (stablecoin)
            ) / 1e18 / 1e36
            print(f"Token: {token}, APR: {token_apr}")
            base_apr += token_apr
    print(f"Base APR: {base_apr}")

    retention_apr = 0
    if total_assets_remaining > 0:
        if ts < LAUNCH_TS:
            retention_apr = (stablecoin_price * remaining_rsup / total_assets_remaining * rsup_price * time_remaining) / (52 * WEEK)
        else:
            period_finish = retention_contract.periodFinish()
            if period_finish > ts:
                rewards_per_year = retention_contract.rewardRate() * 365 * DAY
                retention_apr = (rewards_per_year * rsup_price) / (total_assets_remaining * stablecoin_price) / 1e18
    
    data = {
        'remaining_rsup': remaining_rsup,
        'rsup_price': rsup_price,
        'time_remaining': time_remaining,
        'apr': retention_apr,
        'base_apr': base_apr,
        'total_assets_original': total_assets_original,
        'total_assets_remaining': total_assets_remaining,
        'total_supply_original': total_supply_original,
        'total_supply_remaining': total_supply_remaining,
        'withdrawal_feed': build_withdrawal_feed(current_height)
    }
    return data

def build_withdrawal_feed(current_height):
    if not isinstance(current_height, int):
        current_height = 0
    
    SNAPSHOT_BLOCK = 22830880
    FEED_CACHE_FILE = 'withdrawal_feed_cache.json'
    
    # Use /data directory at root of project
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    feed_cache_path = os.path.join(project_root, 'data', FEED_CACHE_FILE)
    
    # Load existing feed cache
    cached_feed = []
    last_processed_block = SNAPSHOT_BLOCK
    
    if os.path.exists(feed_cache_path):
        try:
            with open(feed_cache_path, 'r') as f:
                cache_data = json.load(f)
                cached_feed = cache_data.get('feed', [])
                last_processed_block = cache_data.get('last_processed_block', SNAPSHOT_BLOCK)
        except (json.JSONDecodeError, FileNotFoundError):
            current_height = 0
    else:
        current_height = 0
    
    # Get current height if not provided
    if not current_height:
        current_height = chain.height
    
    # Get new events since last processed block
    new_feed_entries = []
    if last_processed_block < current_height:
        ip = Contract(INSURANCE_POOL)
        snapshot_data = load_retention_snapshot_data()
        
        logs = ip.events.Withdraw.get_logs(fromBlock=last_processed_block + 1, toBlock=current_height)
        for log in logs:
            if log.args['owner'] in snapshot_data:
                new_feed_entries.append({
                    'user': log.args['owner'],
                    'amount': log.args['assets'] / 1e18,
                    'shares': log.args['shares'] / 1e18,
                    'timestamp': chain[log.blockNumber].timestamp,
                    'txn_hash': log.transactionHash.hex(),
                    'ts': log.blockNumber
                })
    
    # Create a set of existing entries to avoid duplicates
    existing_entries = set()
    for entry in cached_feed:
        duplicate_key = (entry['txn_hash'], entry['user'], entry['amount'])
        existing_entries.add(duplicate_key)
    
    # Only add truly new entries
    truly_new_entries = []
    for entry in new_feed_entries:
        duplicate_key = (entry['txn_hash'], entry['user'], entry['amount'])
        if duplicate_key not in existing_entries:
            truly_new_entries.append(entry)
    
    # Combine cached and new entries, sort by newest first
    complete_feed = cached_feed + truly_new_entries
    complete_feed.sort(key=lambda x: x['timestamp'], reverse=True)
    
    # Save updated cache
    cache_data = {
        'feed': complete_feed,
        'last_processed_block': current_height
    }
    
    os.makedirs(os.path.dirname(feed_cache_path), exist_ok=True)
    with open(feed_cache_path, 'w') as f:
        json.dump(cache_data, f, indent=4)
    
    print(f"Withdrawal feed: {len(complete_feed)} total entries, {len(truly_new_entries)} new entries")
    return complete_feed

def filter_redundant_checkpoints(history, threshold=150, preserve_latest=True):
    """
    Filter out checkpoints where amount change is below threshold, keeping only significant changes.
    Always preserves the most recent checkpoint if preserve_latest=True.
    """
    if not history:
        return history
    
    filtered = []
    last_amount = None
    
    # Sort by timestamp to ensure chronological order
    sorted_history = sorted(history, key=lambda x: x['timestamp'])
    
    for entry in sorted_history:
        current_amount = entry['amount']
        
        # Always add the first entry
        if last_amount is None:
            filtered.append(entry)
            last_amount = current_amount
            continue
        
        # Add entry if amount change exceeds threshold
        if abs(current_amount - last_amount) > threshold:
            filtered.append(entry)
            last_amount = current_amount
    
    # If preserve_latest is True and the latest entry isn't already included,
    # add it regardless of whether amount changed significantly
    if preserve_latest and sorted_history:
        latest_entry = sorted_history[-1]
        if not filtered or filtered[-1]['block'] != latest_entry['block']:
            filtered.append(latest_entry)
    
    return filtered

def get_loan_repayment_data(current_height):
    """Get loan repayment data with caching for efficiency"""
    if not isinstance(current_height, int):
        current_height = chain.height
    
    DEPLOY_BLOCK = 22833775
    CACHE_FILE = 'loan_repayment_cache.json'
    
    # Use /data directory at root of project
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    cache_path = os.path.join(project_root, 'data', CACHE_FILE)
    
    # Load existing cache
    cached_repayments = []
    cached_bad_debt_payments = []
    cached_bad_debt_history = []
    cached_yearn_loan_history = []
    last_processed_block = DEPLOY_BLOCK
    
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                cache_data = json.load(f)
                cached_repayments = cache_data.get('repayments', [])
                cached_bad_debt_payments = cache_data.get('bad_debt_payments', [])
                cached_bad_debt_history = cache_data.get('bad_debt_history', [])
                cached_yearn_loan_history = cache_data.get('yearn_loan_history', [])
                last_processed_block = cache_data.get('last_processed_block', DEPLOY_BLOCK)
        except (json.JSONDecodeError, FileNotFoundError):
            last_processed_block = DEPLOY_BLOCK
    
    # Get current contract states
    repayer = Contract(LOAN_REPAYER)
    remaining_debt = repayer.remainingLoan() / 1e18
    total_repaid = repayer.totalRepaid() / 1e18
    
    bad_debt_repayer = Contract(BAD_DEBT_REPAYER)
    remaining_bad_debt = bad_debt_repayer.remainingBadDebt() / 1e18
    bad_debt_paid = max(10_000_000 - remaining_bad_debt, 0)
    
    loan_converter = Contract(LOAN_CONVERTER)
    
    # Hardcoded bad debt repayments
    hardcoded_bad_debt_payments = [
        {
            'block': 22792135,
            'txn': '0x18884d0a608f6431fb4d5efa308afc1920d0f09d9691e5e22e849de61719b626',
            'payer': '0xAAc0aa431c237C2C0B5f041c8e59B3f1a43aC78F',
            'amount': 1407736.69,
            'shares': 1407736.69,  # Assuming 1:1 ratio for hardcoded data
            'timestamp': 1750984019  # Jun-27-2025 12:16:11 AM UTC
        },
        {
            'block': 22796352,
            'txn': '0x1c6c24cbe0d090a953dc1df7ecae8403f6d5b317e0127048f9aacf22e2e5336e',
            'payer': '0xa3C5A1e09150B75ff251c1a7815A07182c3de2FB',
            'amount': 818360.45,
            'shares': 818360.45,  # Assuming 1:1 ratio for hardcoded data
            'timestamp': 1751028251  # Jun-27-2025 02:24:11 PM UTC
        },
        {
            'block': 22789147,
            'txn': '0x7225c1e2793368234c6f133924906e9bea336dabbc363c7513f886eaa812c55c',
            'payer': '0xFE11a5009f2121622271e7dd0FD470264e076af6',
            'amount': 643051.79,
            'shares': 643051.79,  # Assuming 1:1 ratio for hardcoded data
            'timestamp': 1750930499  # Jun-26-2025 02:14:59 PM UTC
        },
        {
            'block': 22830881,
            'txn': '0x355eb285b2d1de1b8a6f6799f9694e4bcfe2886d59e4ef9a1f37581939ad7e1e',
            'payer': '0x00000000efe883b3304aFf71eaCf72Dbc3e1b577',
            'amount': 6000000.0,
            'shares': 6000000.0,  # Assuming 1:1 ratio for hardcoded data
            'timestamp': 1751444159  # Jul-02-2025 10:15:59 AM UTC
        }
    ]
    
    # Get new events since last processed block
    new_repayments = []
    new_bad_debt_payments = []
    
    if last_processed_block < current_height:
        # Get repayment events
        repayment_logs = repayer.events.Repayment.get_logs(fromBlock=last_processed_block + 1, toBlock=current_height)
        for log in repayment_logs:
            owed_before = repayer.remainingLoan(block_identifier=log.blockNumber-1) / 1e18
            owed_after = repayer.remainingLoan(block_identifier=log.blockNumber) / 1e18
            new_repayments.append({
                'block': log.blockNumber,
                'txn': log.transactionHash.hex(),
                'repayer': log.args.repayer,
                'amount': log.args.amount / 1e18,
                'owed_before': owed_before,
                'owed_after': owed_after,
                'timestamp': chain[log.blockNumber].timestamp
            })
        
        # Get bad debt repayment events
        bad_debt_logs = bad_debt_repayer.events.BadDebtPaid.get_logs(fromBlock=last_processed_block + 1, toBlock=current_height)
        for log in bad_debt_logs:
            new_bad_debt_payments.append({
                'block': log.blockNumber,
                'txn': log.transactionHash.hex(),
                'payer': log.args.payer,
                'amount': log.args.amount / 1e18,
                'shares': log.args.shares / 1e18,
                'timestamp': chain[log.blockNumber].timestamp
            })
    
    # Build bad debt history (load from cache, then add new entries)
    bad_debt_history = cached_bad_debt_history.copy()
    pair = Contract('0x6e90c85a495d54c6d7E1f3400FEF1f6e59f86bd6')
    blocks_in_day = 7200
    max_block = max((entry['block'] for entry in bad_debt_history), default=0)
    start_block = max(22784988, max_block) # Attack block
    i = start_block
    while i < current_height:
        ts = chain[i].timestamp
        bad_debt_history.append(
            {
                'amount': pair.totalBorrow(block_identifier=i)['amount']/1e18,
                'timestamp': ts,
                'block': i
            }
        )
        # More granular sampling before the target timestamp
        if ts < 1750984019:
            i += 300  # Hourly sampling (7200/24 = 300 blocks)
        else:
            i += blocks_in_day  # Daily sampling
    bad_debt_history.append({'amount': pair.totalBorrow(block_identifier=current_height)['amount']/1e18, 'timestamp': chain[current_height].timestamp, 'block': current_height})
        
    # Filter out redundant checkpoints for bad debt history
    bad_debt_history = filter_redundant_checkpoints(bad_debt_history)

    # Build yearn loan history (load from cache, then add new entries)
    yearn_loan_history = cached_yearn_loan_history.copy()
    max_block = max((entry['block'] for entry in yearn_loan_history), default=0)
    start_block = max(23024118, max_block) # Deploy block
    i = start_block
    while i < current_height:
        yearn_loan_history.append(
            {
                'amount': repayer.remainingLoan(block_identifier=i) / 1e18,
                'timestamp': chain[i].timestamp,
                'block': i
            }
        )
        i += blocks_in_day
    yearn_loan_history.append({'amount': repayer.remainingLoan(block_identifier=current_height) / 1e18, 'timestamp': chain[current_height].timestamp, 'block': current_height})
    
    # Filter out redundant checkpoints for yearn loan history
    yearn_loan_history = filter_redundant_checkpoints(yearn_loan_history)

    # Combine cached and new entries, sort by newest first
    complete_repayments = cached_repayments + new_repayments
    complete_repayments.sort(key=lambda x: x['timestamp'], reverse=True)
    
    # Combine cached and new bad debt payments
    complete_bad_debt_payments = cached_bad_debt_payments + new_bad_debt_payments
    
    # Add hardcoded payments only if they don't already exist
    existing_txns = {entry['txn'] for entry in complete_bad_debt_payments}
    for hardcoded_payment in hardcoded_bad_debt_payments:
        if hardcoded_payment['txn'] not in existing_txns:
            complete_bad_debt_payments.append(hardcoded_payment)
    
    complete_bad_debt_payments.sort(key=lambda x: x['timestamp'], reverse=True)
    
    # Save updated cache
    cache_data = {
        'repayments': complete_repayments,
        'bad_debt_payments': complete_bad_debt_payments,
        'last_processed_block': current_height,
        'bad_debt_history': bad_debt_history,
        'yearn_loan_history': yearn_loan_history
    }
    
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, 'w') as f:
        json.dump(cache_data, f, indent=4)
    
    print(f"Loan repayments: {len(complete_repayments)} total entries, {len(new_repayments)} new entries")
    print(f"Bad debt payments: {len(complete_bad_debt_payments)} total entries, {len(new_bad_debt_payments)} new entries")
    
    return {
        'repayments': complete_repayments,
        'bad_debt_payments': complete_bad_debt_payments,
        'current_state': {
            'remaining_debt': remaining_debt,
            'total_repaid': total_repaid,
            'remaining_bad_debt': remaining_bad_debt,
            'bad_debt_paid': bad_debt_paid
        },
        'bad_debt_history': bad_debt_history,
        'yearn_loan_history': yearn_loan_history
    }
    
def main():
    # Initialize CoinGecko tokens cache
    get_coingecko_tokens()
    
    # Get market data
    market_data = get_resupply_pairs_and_collaterals()
    
    # Get Retention Program data
    current_height = chain.height
    retention_data = get_retention_program_data(current_height)
    
    # Get Authorizations data
    authorizations_data = get_all_selectors(current_height)
    
    # Get Loan Repayment data
    loan_repayment_data = get_loan_repayment_data(current_height)
    
    # Add metadata
    current_time = int(time.time())
    
    
    data = {
        'data': market_data,
        'retention_program': retention_data,
        'authorizations': authorizations_data,
        'loan_repayment': loan_repayment_data,
        'last_update': current_time,
        'last_update_block': current_height,
    }
    
    # Stringify any Contract objects and save
    data_str = stringify_dicts(data)
    save_data_as_json(data_str)
    
    print("Resupply market data saved successfully.")

if __name__ == "__main__":
    main()
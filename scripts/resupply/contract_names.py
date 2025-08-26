import requests
import json
from pathlib import Path
from typing import Dict, Optional

CONTRACT_NAMES = None
CACHE_FILE = 'contract_names_cache.json'

# Hardcoded contract names for addresses not in the GitHub JSON
HARDCODED_CONTRACTS = {
    '0xfdce0267803c6a0d209d3721d2f01fd618e9cbf8': 'PRISMA_FEE_RECEIVER',
}

def get_cache_path() -> Path:
    """Get the path to the cache file in the data directory."""
    current_dir = Path(__file__).parent
    data_dir = current_dir / 'data'
    return data_dir / CACHE_FILE

def load_cache() -> Dict[str, str]:
    """Load contract names from cache file."""
    cache_path = get_cache_path()
    if not cache_path.exists():
        return {}
    
    try:
        with open(cache_path, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_cache(contract_names: Dict[str, str]) -> None:
    """Save contract names to cache file."""
    cache_path = get_cache_path()
    cache_path.parent.mkdir(exist_ok=True)
    
    try:
        with open(cache_path, 'w') as f:
            json.dump(contract_names, f, indent=2)
    except:
        pass

def fetch_from_api() -> Dict[str, str]:
    """Fetch contract names from API and return address->name mapping."""
    url = "https://raw.githubusercontent.com/resupplyfi/resupply/refs/heads/main/deployment/contracts.json"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        contracts_data = response.json()
        
        # Invert mapping: address -> name
        contract_names = {}
        for name, address in contracts_data.items():
            contract_names[address.lower()] = name
        
        return contract_names
    except:
        return {}

def get_contract_names() -> Dict[str, str]:
    """Get contract names, using cache first then API if needed."""
    global CONTRACT_NAMES
    if CONTRACT_NAMES is not None:
        return CONTRACT_NAMES
    
    # Start with cache
    CONTRACT_NAMES = load_cache()
    
    # If cache is empty, fetch from API
    if not CONTRACT_NAMES:
        CONTRACT_NAMES = fetch_from_api()
        if CONTRACT_NAMES:
            save_cache(CONTRACT_NAMES)
    
    return CONTRACT_NAMES

def get_contract_name(address: str) -> Optional[str]:
    """Get contract name for address, fetching from API if not in cache."""
    if not address:
        return None
    
    # Check hardcoded contracts first
    hardcoded_result = HARDCODED_CONTRACTS.get(address.lower())
    if hardcoded_result:
        return hardcoded_result
    
    # Try cache first
    contract_names = get_contract_names()
    result = contract_names.get(address.lower())
    
    # If not found and we have cache, try API to get updated data
    if result is None and contract_names:
        api_data = fetch_from_api()
        if api_data:
            # Update cache with any new items
            contract_names.update(api_data)
            save_cache(contract_names)
            result = contract_names.get(address.lower())
    
    return result

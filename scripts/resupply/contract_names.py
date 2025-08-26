import requests
import json
import logging
from pathlib import Path
from typing import Dict, Optional

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

CONTRACT_NAMES = None
CACHE_FILE = 'contract_names_cache.json'

def get_cache_path() -> Path:
    """Get the path to the cache file in the data directory."""
    current_dir = Path(__file__).parent
    data_dir = current_dir / 'data'
    return data_dir / CACHE_FILE

def load_cache() -> Dict[str, str]:
    """Load contract names from cache file."""
    cache_path = get_cache_path()
    if not cache_path.exists():
        logger.info("No cache file found")
        return {}
    
    try:
        with open(cache_path, 'r') as f:
            data = json.load(f)
            logger.info(f"Loaded {len(data)} contract names from cache")
            return data
    except Exception as e:
        logger.warning(f"Failed to load cache: {e}")
        return {}

def save_cache(contract_names: Dict[str, str]) -> None:
    """Save contract names to cache file."""
    cache_path = get_cache_path()
    cache_path.parent.mkdir(exist_ok=True)
    
    try:
        with open(cache_path, 'w') as f:
            json.dump(contract_names, f, indent=2)
        logger.info(f"Saved {len(contract_names)} contract names to cache")
    except Exception as e:
        logger.warning(f"Failed to save cache: {e}")

def fetch_from_api() -> Dict[str, str]:
    """Fetch contract names from API and return address->name mapping."""
    url = "https://raw.githubusercontent.com/resupplyfi/resupply/refs/heads/main/deployment/contracts.json"
    
    logger.info(f"Fetching contract names from API: {url}")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        contracts_data = response.json()
        
        # Invert mapping: address -> name
        contract_names = {}
        for name, address in contracts_data.items():
            contract_names[address.lower()] = name
        
        logger.info(f"Successfully fetched {len(contract_names)} contract names from API")
        return contract_names
    except Exception as e:
        logger.error(f"Failed to fetch from API: {e}")
        return {}

def get_contract_names() -> Dict[str, str]:
    """Get contract names, using cache first then API if needed."""
    global CONTRACT_NAMES
    if CONTRACT_NAMES is not None:
        logger.debug("Using in-memory cache")
        return CONTRACT_NAMES
    
    # Start with cache
    CONTRACT_NAMES = load_cache()
    
    # If cache is empty, fetch from API
    if not CONTRACT_NAMES:
        logger.info("Cache is empty, fetching from API")
        CONTRACT_NAMES = fetch_from_api()
        if CONTRACT_NAMES:
            save_cache(CONTRACT_NAMES)
    
    return CONTRACT_NAMES

def get_contract_name(address: str) -> Optional[str]:
    """Get contract name for address, fetching from API if not in cache."""
    if not address:
        logger.warning("Empty address provided")
        return None
    
    logger.info(f"Looking up contract name for address: {address}")
    
    # Try cache first
    contract_names = get_contract_names()
    result = contract_names.get(address.lower())
    
    if result:
        logger.info(f"Found in cache: {address} -> {result}")
    else:
        logger.info(f"Not found in cache: {address}")
        
        # If not found and we have cache, try API to get updated data
        if contract_names:
            logger.info("Cache exists but address not found, fetching from API for updates")
            api_data = fetch_from_api()
            if api_data:
                # Update cache with any new items
                contract_names.update(api_data)
                save_cache(contract_names)
                result = contract_names.get(address.lower())
                
                if result:
                    logger.info(f"Found after API update: {address} -> {result}")
                else:
                    logger.warning(f"Still not found after API update: {address}")
            else:
                logger.warning("API fetch failed, cannot update cache")
        else:
            logger.warning("No cache available and address not found")
    
    return result

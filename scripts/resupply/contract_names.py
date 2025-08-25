import requests
import json
import os
from pathlib import Path
from typing import Dict, Optional

CONTRACT_NAMES = None
CACHE_FILE = 'contract_names_cache.json'

def get_cache_path() -> Path:
    """Get the path to the cache file in the data directory."""
    # Get the data directory path (scripts/resupply/data/)
    current_dir = Path(__file__).parent
    data_dir = current_dir / 'data'
    return data_dir / CACHE_FILE

def load_cached_contract_names() -> Dict[str, str]:
    """Load contract names from local cache file."""
    cache_path = get_cache_path()
    
    if not cache_path.exists():
        return {}
    
    try:
        with open(cache_path, 'r') as f:
            cached_data = json.load(f)
            print(f"Loaded {len(cached_data)} contract names from cache")
            return cached_data
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Warning: Failed to load cached contract names: {str(e)}")
        return {}

def save_contract_names_to_cache(contract_names: Dict[str, str]) -> None:
    """Save contract names to local cache file."""
    cache_path = get_cache_path()
    
    # Ensure data directory exists
    cache_path.parent.mkdir(exist_ok=True)
    
    try:
        with open(cache_path, 'w') as f:
            json.dump(contract_names, f, indent=2)
        print(f"Saved {len(contract_names)} contract names to cache")
    except Exception as e:
        print(f"Warning: Failed to save contract names to cache: {str(e)}")

def get_contract_names() -> Dict[str, str]:
    """
    Fetch contract names from Resupply repository and return inverted mapping (address -> name).
    Uses local cache for persistence and to avoid repeated API calls.
    """
    global CONTRACT_NAMES
    if CONTRACT_NAMES is not None:
        return CONTRACT_NAMES
    
    # First try to load from cache
    CONTRACT_NAMES = load_cached_contract_names()
    
    # If cache is empty, try to fetch from API
    if not CONTRACT_NAMES:
        url = "https://raw.githubusercontent.com/resupplyfi/resupply/refs/heads/main/deployment/contracts.json"
        
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            contracts_data = response.json()
            
            # Invert the mapping: address -> name
            CONTRACT_NAMES = {}
            for name, address in contracts_data.items():
                # Normalize address to lowercase for consistent lookup
                CONTRACT_NAMES[address.lower()] = name
            
            # Save to cache for future use
            save_contract_names_to_cache(CONTRACT_NAMES)
            
            print(f"Fetched and cached {len(CONTRACT_NAMES)} contract names from Resupply repository")
            return CONTRACT_NAMES
            
        except requests.exceptions.RequestException as e:
            print(f"Warning: Failed to fetch contract names from API: {str(e)}")
            print("Using empty contract names mapping")
            CONTRACT_NAMES = {}
            return CONTRACT_NAMES
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse contract names JSON: {str(e)}")
            print("Using empty contract names mapping")
            CONTRACT_NAMES = {}
            return CONTRACT_NAMES
    
    return CONTRACT_NAMES

def get_contract_name(address: str) -> Optional[str]:
    """
    Get contract name for a given address.
    Returns None if not found.
    """
    if not address:
        return None
    
    contract_names = get_contract_names()
    return contract_names.get(address.lower(), None)

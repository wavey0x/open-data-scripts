from pathlib import Path
import json
from web3 import Web3
from typing import Optional, Dict
from brownie import web3, ZERO_ADDRESS, chain, interface
from .constants import CONTRACTS
from .contract_names import get_contract_name
from utils.utils import contract_creation_block

SELECTORS = None

def get_selectors() -> Dict[str, str]:
    """Load the selectors from selectors.json, caching in memory."""
    global SELECTORS
    if SELECTORS is not None:
        return SELECTORS
    # Determine project root
    project_root = Path(__file__).resolve().parents[2]
    selectors_file = project_root / "data/selectors.json"
    if not selectors_file.exists():
        SELECTORS = {}
        print("No selectors file found.")
        return SELECTORS
    with open(selectors_file, 'r') as f:
        SELECTORS = json.load(f)
    print(f"Loaded {len(SELECTORS)} selectors from {selectors_file}")
    return SELECTORS

def get_function_selector(signature: str) -> str:
    """Generate function selector from function signature"""
    return Web3.keccak(text=signature)[:4].hex()

def lookup_selector(selector_hex: str) -> Optional[str]:
    """Look up a function signature by its selector in selectors.json"""
    selectors = get_selectors()

    # Normalize selector format
    if not selector_hex.startswith('0x'):
        selector_hex = f"0x{selector_hex}"
    selector_hex = selector_hex.lower()

    return selectors.get(selector_hex, None)

def generate_selectors() -> Dict[str, str]:
    """Generate and save selectors from interface JSONs"""
    global SELECTORS
    project_root = Path(__file__).resolve().parents[2]
    interfaces_dir = project_root / "interfaces/resupply"
    selectors_file = project_root / "data/selectors.json"

    selectors = {}

    # Ensure interfaces directory exists
    if not interfaces_dir.exists():
        print(f"Interfaces directory not found at {interfaces_dir}")
        return selectors

    # Process each .json file in the interfaces directory
    for json_file in interfaces_dir.glob("*.json"):
        contract_name = json_file.stem

        with open(json_file, 'r') as f:
            abi = json.load(f)

            # Find all function entries in the ABI
            for item in abi:
                if item.get('type') == 'function':
                    name = item.get('name')
                    inputs = item.get('inputs', [])

                    # Build function signature
                    param_types = [inp['type'] for inp in inputs]
                    signature = f"{name}({','.join(param_types)})"

                    # Generate and store selector
                    selector = get_function_selector(signature)
                    selectors[selector] = f"{signature}"

    # Save selectors to JSON file
    with open(selectors_file, 'w') as f:
        json.dump(selectors, f, indent=2)

    print(f"Generated selectors file at {selectors_file}")
    print(f"Found {len(selectors)} function selectors")

    # Update global cache
    SELECTORS = selectors

    return selectors

# Only generate selectors if run directly
if __name__ == "__main__":
    generate_selectors()

def get_active_authorizations(logs):
    """Return only currently active authorizations from the log list."""
    # Map: (selector_hex, caller, target) -> last log
    last_state = {}
    for entry in logs:
        key = (entry['selector'][0], entry['caller'], entry['target'])
        # Since logs are sorted newest first, only set if not already set
        if key not in last_state:
            last_state[key] = entry
    # Only keep those where authorized is True
    active = [entry for entry in last_state.values() if entry['authorized']]
    return active

def get_all_selectors(current_height=None):
    """Get all authorization selectors with caching for efficiency. Returns dict with 'all' and 'active'."""
    if not isinstance(current_height, int):
        current_height = chain.height
    
    CORE_DEPLOY_BLOCK = 22034863  # Start from this block when no cache exists
    CACHE_FILE = 'authorizations_cache.json'
    
    # Use /data directory at root of project
    project_root = Path(__file__).parent.parent.parent
    cache_path = project_root / 'data' / CACHE_FILE
    
    # Load existing cache
    cached_authorizations = []
    last_processed_block = CORE_DEPLOY_BLOCK
    
    if cache_path.exists():
        try:
            with open(cache_path, 'r') as f:
                cache_data = json.load(f)
                cached_authorizations = cache_data.get('authorizations', [])
                last_processed_block = cache_data.get('last_processed_block', CORE_DEPLOY_BLOCK)
        except (json.JSONDecodeError, FileNotFoundError):
            last_processed_block = CORE_DEPLOY_BLOCK

    # Load selectors (will use cached version if already loaded)
    get_selectors()

    # Get new events since last processed block
    new_authorizations = []
    if last_processed_block < current_height:
        core = interface.ICore(CONTRACTS["CORE"])
        
        logs = core.events.OperatorSet.get_logs(fromBlock=last_processed_block + 1, toBlock=current_height)
        for log in logs:
            selector_hex = web3.to_hex(log.args.selector)
            new_authorizations.append({
                'block': log.blockNumber,
                'txn': '0x' + log.transactionHash.hex(),
                'selector': (selector_hex, ""),
                'caller': log.args.caller,
                'auth_hook': log.args.authHook,
                'authorized': log.args.authorized,
                'target': log.args.target,
                'timestamp': chain[log.blockNumber].timestamp
            })
    
    # Create a set of existing entries to avoid duplicates
    existing_entries = set()
    for entry in cached_authorizations:
        duplicate_key = (entry['txn'], entry['block'], entry['caller'], entry['target'])
        existing_entries.add(duplicate_key)
    
    # Only add truly new entries
    truly_new_entries = []
    for entry in new_authorizations:
        duplicate_key = (entry['txn'], entry['block'], entry['caller'], entry['target'])
        if duplicate_key not in existing_entries:
            truly_new_entries.append(entry)
    
    # Combine cached and new entries, sort by newest first
    complete_authorizations = cached_authorizations + truly_new_entries
    complete_authorizations.sort(key=lambda x: x['timestamp'], reverse=True)
    
    # Lookup selectors and add contract names
    missing_selectors = set()
    for entry in complete_authorizations:
        selector_hex = entry['selector'][0]
        signature = lookup_selector(selector_hex)
        if signature is None:
            missing_selectors.add(selector_hex)
            signature = ""
        entry['selector'] = (selector_hex, signature)

        # Add contract names for caller and target
        entry['caller_name'] = get_contract_name(entry['caller'])
        entry['target_name'] = get_contract_name(entry['target'])

    if missing_selectors:
        print(f"Warning: {len(missing_selectors)} selectors not found in local cache or 4byte.directory")
    
    # Save updated cache
    cache_data = {
        'authorizations': complete_authorizations,
        'last_processed_block': current_height
    }
    
    cache_path.parent.mkdir(exist_ok=True)
    with open(cache_path, 'w') as f:
        json.dump(cache_data, f, indent=4)
    
    print(f"Authorizations: {len(complete_authorizations)} total entries, {len(truly_new_entries)} new entries")
    active_authorizations = get_active_authorizations(complete_authorizations)
    print(f"Active authorizations: {len(active_authorizations)}")
    return {'all': complete_authorizations, 'active': active_authorizations}

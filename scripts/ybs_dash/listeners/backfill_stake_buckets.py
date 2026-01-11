import json
import os
import sys
import time
import signal
import threading
import requests
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from utils import db as db_utils

DEFAULT_WEB3_PROVIDER_URI = "https://guest:guest@eth.wavey.info"
CHECKPOINT_FILE = Path(__file__).with_name("backfill_stake_buckets.checkpoint.json")
BLOCK_CHUNK_SIZE = 75_000


def ensure_web3_provider():
    if not os.getenv("WEB3_PROVIDER_URI"):
        os.environ["WEB3_PROVIDER_URI"] = DEFAULT_WEB3_PROVIDER_URI
        print("WEB3_PROVIDER_URI not set; using default provider.")


def apply_lifo_unstake(token, week, amount, max_weeks):
    remaining = amount
    for target_week in range(week + max_weeks, week, -1):
        bucket_amount = db_utils.get_stake_bucket_amount(token, target_week)
        if bucket_amount <= 0:
            continue
        if bucket_amount >= remaining:
            db_utils.upsert_stake_bucket(token, target_week, -remaining)
            remaining = 0
            break
        remaining -= bucket_amount
        db_utils.upsert_stake_bucket(token, target_week, -bucket_amount)
    if remaining > 0:
        print(f"Warning: {token} unstake of {remaining} could not be fully bucketed.")

def load_checkpoint():
    if not CHECKPOINT_FILE.exists():
        return {}
    try:
        with CHECKPOINT_FILE.open("r") as handle:
            return json.load(handle)
    except Exception:
        return {}

def save_checkpoint(checkpoint):
    tmp_path = CHECKPOINT_FILE.with_suffix(".tmp")
    with tmp_path.open("w") as handle:
        json.dump(checkpoint, handle, indent=2, sort_keys=True)
    tmp_path.replace(CHECKPOINT_FILE)

def log_status(message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[{timestamp}] {message}", flush=True)

def probe_provider():
    provider = os.getenv("WEB3_PROVIDER_URI")
    if not provider:
        return
    payload = {"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}
    response = requests.post(provider, json=payload, timeout=5)
    response.raise_for_status()
    chain_id_hex = response.json().get("result")
    log_status(f"Provider reachable ({provider}), chainId={chain_id_hex}")

def connect_network():
    log_status("Connecting to Brownie network...")
    probe_provider()
    from brownie import network

    if network.is_connected():
        log_status("Already connected.")
        return

    stop = threading.Event()

    def _heartbeat():
        elapsed = 0
        while not stop.is_set():
            time.sleep(10)
            elapsed += 10
            if not stop.is_set():
                log_status(f"Still connecting... {elapsed}s")

    thread = threading.Thread(target=_heartbeat, daemon=True)
    thread.start()

    def _timeout_handler(signum, frame):
        raise TimeoutError("Network connect timed out after 60s.")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(60)
    try:
        if network.is_connected():
            return
        network.disconnect()
        network.connect("mainnet", launch_rpc=False)
    finally:
        signal.alarm(0)
        stop.set()

    if not network.is_connected():
        raise RuntimeError("Brownie network did not connect.")
    log_status("Connected.")

def backfill_token(token, info, height):
    ybs = info["ybs"]
    decimals = info["decimals"]
    max_weeks = ybs.MAX_STAKE_GROWTH_WEEKS()
    base_start_block = info.get("ybs_deploy_block") or db_utils.DEPLOY_BLOCK

    checkpoint = load_checkpoint()
    token_state = checkpoint.get(token, {})
    resumed = bool(token_state)
    initialized = token_state.get("initialized", False)
    last_block = token_state.get("last_block")

    status_note = "resuming" if resumed else "starting"
    log_status(f"{status_note} backfill for {token} from block {base_start_block} to {height}")

    if not initialized:
        db_utils.clear_stake_buckets(token)
        db_utils.backfill_unlock_week(token, max_weeks)
        token_state["initialized"] = True
        token_state["last_block"] = base_start_block - 1
        checkpoint[token] = token_state
        save_checkpoint(checkpoint)

    start_block = max(base_start_block, (last_block or base_start_block - 1) + 1)
    total_blocks = height - start_block + 1
    processed_blocks = 0

    for chunk_start in range(start_block, height + 1, BLOCK_CHUNK_SIZE):
        chunk_end = min(chunk_start + BLOCK_CHUNK_SIZE - 1, height)
        log_status(f"{token}: fetching logs for blocks {chunk_start}-{chunk_end}")
        chunk_timer = time.time()
        staked_logs = ybs.events.Staked.get_logs(fromBlock=chunk_start, toBlock=chunk_end)
        unstaked_logs = ybs.events.Unstaked.get_logs(fromBlock=chunk_start, toBlock=chunk_end)
        fetch_elapsed = time.time() - chunk_timer
        events = [(True, log) for log in staked_logs] + [(False, log) for log in unstaked_logs]
        events.sort(key=lambda item: (item[1].blockNumber, item[1].logIndex))
        log_status(f"{token}: fetched {len(events)} events in {fetch_elapsed:.1f}s")

        event_count = len(events)
        for idx, (is_stake, log) in enumerate(events, start=1):
            amount = log["args"]["amount"] / 10 ** decimals
            week = log["args"]["week"]
            unlock_week = week + max_weeks

            if is_stake:
                db_utils.upsert_stake_bucket(token, unlock_week, amount)
            else:
                apply_lifo_unstake(token, week, amount, max_weeks)
            if idx % 20 == 0 or idx == event_count:
                log_status(f"{token}: processed {idx}/{event_count} events in current chunk")

        processed_blocks += (chunk_end - chunk_start + 1)
        token_state["last_block"] = chunk_end
        checkpoint[token] = token_state
        save_checkpoint(checkpoint)
        progress = (processed_blocks / total_blocks * 100) if total_blocks else 100.0
        log_status(
            f"{token}: blocks {chunk_start}-{chunk_end} "
            f"({processed_blocks}/{total_blocks}, {progress:.1f}%) "
            f"events={len(events)}"
        )

    log_status(f"Backfill complete for {token}")

def load_staker_info():
    from brownie import Contract
    from config import YBS_REGISTRY
    from utils import utils as utilities

    registry = Contract(YBS_REGISTRY)
    num_tokens = registry.numTokens()
    deprecated = {
        "0xe3668873D944E4A949DA05fc8bDE419eFF543882",
    }
    result = {}
    for i in range(num_tokens):
        token = registry.tokens(i)
        if token in deprecated:
            continue
        deployment = registry.deployments(token)
        ybs_addr = deployment["yearnBoostedStaker"]
        data = {
            "token": Contract(token),
            "ybs": Contract(ybs_addr),
            "decimals": Contract(token).decimals(),
            "symbol": Contract(token).symbol(),
            "rewards": Contract(deployment["rewardDistributor"]),
            "utils": Contract(deployment["utilities"]),
            "ybs_deploy_block": utilities.contract_creation_block(ybs_addr),
        }
        result[token] = data
    return result


def main():
    load_dotenv()
    log_status("Starting YBS stake bucket backfill...")
    ensure_web3_provider()

    connect_network()
    from brownie import chain
    log_status("Connected. Loading registry and tokens...")
    db_utils.ensure_ybs_schema()
    staker_info = load_staker_info()
    height = chain.height
    log_status(f"Loaded {len(staker_info)} tokens at block {height}.")

    for token, info in staker_info.items():
        backfill_token(token, info, height)
    log_status("Backfill finished for all tokens.")


if __name__ == "__main__":
    main()

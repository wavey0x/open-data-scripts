from brownie import Contract, chain, web3, interface
from web3._utils.events import construct_event_topic_set
from utils.utils import get_prices, get_block_timestamp, get_block_before_timestamp
import matplotlib
import os

# Use non-interactive backend on server, interactive on dev
if os.getenv("ENVIRONMENT") != "dev":
    matplotlib.use("Agg")

import matplotlib.pyplot as plt
from datetime import datetime
import json
import logging
import time
from functools import lru_cache

START_BLOCK = 24205787
SAMPLE_INTERVAL = 60 * 60  # 1 hour in seconds
GOV_PROPOSAL_16_BLOCK = 24387728
REDEMPTION_LOG_CHUNK_BLOCKS = 50_000  # provider-friendly; also gives visible progress
PROGRESS_LOG_INTERVAL_SECS = 15.0  # rate-limit progress logs to avoid spam
CONFIG = {
    "user": "0xe5BcBdf9452af0AB4b042D9d8a3c1E527E26419f",
    "reusd": "0x57aB1E0003F623289CD798B1824Be09a793e4Bec",
    "reward_tokens": {
        "0x419905009e4656fdC02418C7Df35B1E61Ed5F726": "rsup",
        "0x4e3FBD56CD56c3e72c1403e103b45Db9da5B9D2B": "cvx",
        "0xD533a949740bb3306d119CC777fa900bA034cd52": "crv",
    },
    "pair_reward_tokens": {
        # DOLA rewards are currently emitted only by the sdola pair.
        "0x27AB448a75d548ECfF73f8b4F36fCc9496768797": {
            "0x865377367054516e17014CcdED1e7d814EDC9ce4": "dola",
        },
    },
    "pairs": {
        "16x (wsteth)": "0x4A7c64932d1ef0b4a2d430ea10184e3B87095E33",
        "10x (wbtc)": "0x2d8ecd48b58e53972dBC54d8d0414002B41Abc9D",
        "4x (sdola)": "0x27AB448a75d548ECfF73f8b4F36fCc9496768797",
    },
    "pools": {
        "borrow_oracle": "0xc522a6606bba746d7960404f22a3db936b6f4f50",
        "collateral_oracle": "0x4DEcE678ceceb27446b35C672dC7d61F30bAD69E",
    },
    "cache_file": "resupply_position_cache.json",
}

# Toggle to show/hide price overlays on charts
SHOW_CRVUSD_PRICE = False
SHOW_REUSD_PRICE = True

def main(output_path=None, meta_path=None, now_ts=None):
    # Brownie sometimes doesn't configure logging handlers for script loggers.
    # Configure a simple root handler only if nothing is set up yet.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    log = logging.getLogger(__name__)
    force_regen = os.getenv("FORCE_CHART_REGEN", "").lower() in ("true", "1", "yes")
    force_cache_rebuild = os.getenv("FORCE_CACHE_REBUILD", "").lower() in ("true", "1", "yes")
    if meta_path and not force_regen:
        now_ts = now_ts or datetime.utcnow().timestamp()
        window_start = int(now_ts // SAMPLE_INTERVAL) * SAMPLE_INTERVAL
        if not _should_run_for_window(meta_path, output_path, window_start):
            log.info("Chart generation skipped; window_start %s already processed.", window_start)
            return {"skipped": True, "window_start": window_start}

    start = time.monotonic()
    current_block = chain.height
    log.info("position_monitor start: START_BLOCK=%s current_block=%s", START_BLOCK, current_block)
    user = CONFIG["user"]
    reusd = _normalize_address(CONFIG["reusd"])
    reward_tokens = CONFIG["reward_tokens"]
    pair_reward_tokens = CONFIG.get("pair_reward_tokens", {})
    pairs = CONFIG["pairs"]
    pools = CONFIG["pools"]
    reward_tokens_by_pair, all_reward_tokens, normalized_pair_reward_tokens = _build_reward_tokens_by_pair(
        reward_tokens,
        pair_reward_tokens,
        pairs,
    )
    pair_reward_symbols = {
        pair_name: list(dict.fromkeys(reward_tokens_by_pair[_normalize_address(pair_address)].values()))
        for pair_name, pair_address in pairs.items()
    }

    cache_path = _get_open_data_path(CONFIG["cache_file"])
    cache_config = _cache_config(
        user,
        reusd,
        all_reward_tokens,
        normalized_pair_reward_tokens,
        pairs,
        pools,
    )
    if force_cache_rebuild:
        log.info("FORCE_CACHE_REBUILD enabled; ignoring existing cache")
        cache = {}
    else:
        cache = _load_cache(cache_path, cache_config)
    cached_blocks = cache.get("sample_blocks", [])
    cached_data = cache.get("historical_data", {})
    cached_redemptions = cache.get("redemptions", [])
    last_redemption_block = cache.get("last_redemption_block", START_BLOCK - 1)
    log.info(
        "Cache loaded: cached_blocks=%s cached_redemptions=%s last_redemption_block=%s",
        len(cached_blocks),
        len(cached_redemptions),
        last_redemption_block,
    )

    new_redemptions = fetch_redemptions(pairs, log, last_redemption_block + 1)
    redemptions = cached_redemptions + new_redemptions
    log.info("Redemptions total: %s (new=%s)", len(redemptions), len(new_redemptions))
    sample_blocks = build_sample_blocks(redemptions, log, cached_blocks)
    log.info("Sample blocks total: %s", len(sample_blocks))
    prices = load_prices(all_reward_tokens, reusd, pairs, log)
    historical_data = {}
    for pair_name, pair_address in pairs.items():
        cached_records = cached_data.get(pair_name, [])
        log.info(
            "Fetching historical data for %s: cached_records=%s sample_blocks=%s",
            pair_name,
            len(cached_records),
            len(sample_blocks),
        )
        historical_data[pair_name] = fetch_pair_history(
            pair_address,
            user,
            reward_tokens_by_pair[_normalize_address(pair_address)],
            prices,
            sample_blocks,
            pools,
            cached_records,
            log=log,
        )

    # Print summary table for current block
    print_position_summary(
        historical_data,
        all_reward_tokens,
        prices,
        pair_reward_symbols=pair_reward_symbols,
    )

    # Create stacked area charts with redemption overlays
    fig = render_chart(
        historical_data,
        all_reward_tokens,
        redemptions,
        pairs,
        prices,
        pair_reward_symbols=pair_reward_symbols,
    )
    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        fig.savefig(output_path, dpi=150)
        latest_block = sample_blocks[-1][0] if sample_blocks else chain.height
        _write_meta(
            meta_path,
            now_ts or datetime.utcnow().timestamp(),
            output_path,
            user,
            pairs,
            all_reward_tokens,
            normalized_pair_reward_tokens,
            prices,
            latest_block,
        )
        plt.close(fig)
    else:
        plt.show()

    if os.getenv("ENVIRONMENT") != "dev":
        _save_cache(cache_path, cache_config, sample_blocks, historical_data, redemptions)
    log.info("position_monitor complete in %.2fs", time.monotonic() - start)
    return historical_data


def _normalize_address(address):
    return str(address).lower()


def _normalize_reward_token_map(reward_tokens):
    return {_normalize_address(addr): symbol for addr, symbol in reward_tokens.items()}


def _build_reward_tokens_by_pair(default_reward_tokens, pair_reward_tokens, pairs):
    normalized_default = _normalize_reward_token_map(default_reward_tokens)
    normalized_pair_overrides = {}
    for pair_ref, token_map in (pair_reward_tokens or {}).items():
        pair_address = pairs.get(pair_ref, pair_ref)
        normalized_pair_overrides[_normalize_address(pair_address)] = _normalize_reward_token_map(token_map)

    reward_tokens_by_pair = {}
    all_reward_tokens = dict(normalized_default)
    for pair_address in pairs.values():
        pair_key = _normalize_address(pair_address)
        merged = dict(normalized_default)
        merged.update(normalized_pair_overrides.get(pair_key, {}))
        reward_tokens_by_pair[pair_key] = merged
        all_reward_tokens.update(merged)

    return reward_tokens_by_pair, all_reward_tokens, normalized_pair_overrides


def build_sample_blocks(redemptions, log, cached_blocks):
    redemption_blocks = [(r["block"], r["timestamp"]) for r in redemptions]
    start_ts = _start_timestamp(cached_blocks)
    new_blocks = _get_sample_blocks(start_ts=start_ts, extra_blocks=redemption_blocks, log=log)
    merged = cached_blocks + new_blocks
    sample_blocks = sorted(set((b, t) for b, t in merged), key=lambda x: x[0])
    log.info("Sampling %s blocks from %s to %s", len(sample_blocks), START_BLOCK, chain.height)
    return sample_blocks


@lru_cache(maxsize=50_000)
def _block_timestamp_cached(height: int) -> int:
    # Brownie's chain[] is convenient but can be slow with repeated remote lookups; memoize per run.
    return chain[int(height)].timestamp


def _closest_block_after_timestamp_bounded(timestamp: int, lo: int, hi: int) -> int:
    """Return the smallest block number in (lo, hi] whose timestamp is strictly > timestamp."""
    lo = int(lo)
    hi = int(hi)
    while hi - lo > 1:
        mid = lo + (hi - lo) // 2
        if _block_timestamp_cached(mid) > timestamp:
            hi = mid
        else:
            lo = mid
    if _block_timestamp_cached(hi) < timestamp:
        raise Exception("timestamp is in the future")
    return hi


def _get_sample_blocks(start_ts, extra_blocks=None, log=None):
    """Generate list of (block, timestamp) tuples from start_ts to now, every hour.

    Args:
        extra_blocks: Optional list of (block, timestamp) tuples to merge in (e.g., redemption blocks)
    """
    log = log or logging.getLogger(__name__)
    current_block = chain.height
    current_timestamp = _block_timestamp_cached(current_block)

    sample_blocks = []
    t = start_ts
    # Because timestamps are monotonically increasing, each successive search can be bounded
    # by the previously found block to reduce RPC calls.
    lo_block = max(0, START_BLOCK - 1)
    total_steps = int(max(0, (current_timestamp - start_ts)) // SAMPLE_INTERVAL) + 1
    steps_done = 0
    last_progress = time.monotonic()
    while t <= current_timestamp:
        block_after = _closest_block_after_timestamp_bounded(int(t), lo_block, current_block)
        block = block_after - 1
        sample_blocks.append((block, t))
        t += SAMPLE_INTERVAL
        lo_block = block_after
        steps_done += 1

        now = time.monotonic()
        if (now - last_progress) >= PROGRESS_LOG_INTERVAL_SECS:
            pct = 100.0 * steps_done / max(1, total_steps)
            log.info(
                "Sample block progress: %s/%s (%.1f%%) last_block=%s last_ts=%s",
                steps_done,
                total_steps,
                pct,
                block,
                int(t - SAMPLE_INTERVAL),
            )
            last_progress = now

    # Ensure current block is included as final sample
    if sample_blocks and sample_blocks[-1][0] != current_block:
        sample_blocks.append((current_block, current_timestamp))

    # Merge extra blocks (e.g., redemption blocks)
    if extra_blocks:
        sample_blocks.extend(extra_blocks)
        # Sort by block number and deduplicate
        sample_blocks = sorted(set(sample_blocks), key=lambda x: x[0])

    return sample_blocks


def fetch_redemptions(pairs, log, from_block):
    log.info("Fetching redemption events...")
    return _get_redemption_events(list(pairs.values()), from_block, chain.height)


def _get_redemption_events(pair_addresses, from_block, to_block):
    """Fetch redemption events for specified pairs from from_block to to_block."""
    contract = web3.eth.contract(pair_addresses[0], abi=interface.IResupplyPair.abi)
    topics = construct_event_topic_set(
        contract.events.Redeemed().abi,
        web3.codec,
        {}
    )

    logger = logging.getLogger(__name__)
    if from_block > to_block:
        logger.info("No redemption scan needed (from_block %s > to_block %s)", from_block, to_block)
        return []

    # Chunk the log scan so we show steady progress and avoid provider timeouts on huge ranges.
    all_events = []
    scan_start = time.monotonic()
    start_block = int(from_block)
    end_block = int(to_block)
    window = int(REDEMPTION_LOG_CHUNK_BLOCKS)
    last_progress = 0.0

    cur = start_block
    while cur <= end_block:
        chunk_to = min(cur + window - 1, end_block)
        t0 = time.monotonic()
        logs = web3.eth.get_logs({
            'fromBlock': cur,
            'toBlock': chunk_to,
            'topics': topics,
            'address': pair_addresses
        })
        events = contract.events.Redeemed().process_receipt({'logs': logs})
        all_events.extend(events)

        now = time.monotonic()
        if (now - last_progress) >= PROGRESS_LOG_INTERVAL_SECS:
            pct = 100.0 * (chunk_to - start_block + 1) / (end_block - start_block + 1)
            logger.info(
                "Redemption scan progress: blocks %s-%s/%s (%.1f%%) chunk_logs=%s chunk_events=%s elapsed=%.1fs",
                cur,
                chunk_to,
                end_block,
                pct,
                len(logs),
                len(events),
                now - scan_start,
            )
            last_progress = now

        # If an individual chunk is slow, give a hint about which window is the culprit.
        dt = time.monotonic() - t0
        if dt >= 10.0:
            logger.info("Redemption scan chunk blocks %s-%s took %.1fs", cur, chunk_to, dt)

        cur = chunk_to + 1

    redemptions = []
    block_ts_cache = {}
    for event in all_events:
        bn = int(event.blockNumber)
        ts = block_ts_cache.get(bn)
        if ts is None:
            ts = chain[bn].timestamp
            block_ts_cache[bn] = ts
        redemptions.append({
            'timestamp': ts,
            'datetime': datetime.fromtimestamp(ts),
            'block': bn,
            'pair_address': str(event.address),  # Ensure string, not HexBytes
            'amount': event.args['_amount'] / 1e18,
        })

    logger.info(
        "Found %s redemption events (scan %.1fs, range %s-%s, %s pairs)",
        len(redemptions),
        time.monotonic() - scan_start,
        from_block,
        to_block,
        len(pair_addresses),
    )

    # Debug: show redemption counts per pair address
    from collections import Counter
    addr_counts = Counter(r['pair_address'].lower() for r in redemptions)
    for addr, count in addr_counts.items():
        logger.info("  %s: %s redemptions", addr, count)

    return redemptions


def load_prices(reward_tokens, reusd, pairs, log):
    all_tokens = set(reward_tokens.keys())
    all_tokens.add(_normalize_address(reusd))
    for pair_address in pairs.values():
        pair = Contract(pair_address)
        collateral_contract = Contract(pair.collateral())
        all_tokens.add(_normalize_address(collateral_contract.asset()))
    prices = get_prices(tokens=list(all_tokens))
    prices = {_normalize_address(addr): value for addr, value in prices.items()}
    log.info("Fetched prices for %s tokens", len(prices))
    return prices


def fetch_pair_history(pair_address, user, reward_tokens, prices, sample_blocks, pools, cached_records, log=None):
    """Fetch position data at each sample block for a single pair."""
    log = log or logging.getLogger(__name__)
    pair = Contract(pair_address)
    collateral_contract = Contract(pair.collateral())
    asset = _normalize_address(collateral_contract.asset())

    pool = Contract(pools["borrow_oracle"])
    crvusd_usdc_pool = Contract(pools["collateral_oracle"])
    collateral_price = prices.get(asset, 1.0)  # Collateral at market price
    # borrow_price = prices.get(reusd, 1.0)  # reUSD at market price
    # collateral_price = 1
    # borrow_price = 1

    data = list(cached_records)
    seen_blocks = {d["block"] for d in data}
    total_targets = len(sample_blocks)
    to_fetch = total_targets - len(seen_blocks)
    log.info("History scan %s: need=%s (seen=%s total=%s)", pair_address, max(0, to_fetch), len(seen_blocks), total_targets)
    last_progress = time.monotonic()
    fetched = 0
    unknown_reward_tokens_seen = set()
    for block, timestamp in sample_blocks:
        if block in seen_blocks:
            continue
        borrow_price = 1e18 / pool.price_oracle(0, block_identifier=block)
        collateral_price = 1#crvusd_usdc_pool.price_oracle(block_identifier=block) / 1e18
        # print(collateral_price)
        # Collateral value at block
        collateral_balance = pair.userCollateralBalance.call(user, block_identifier=block)
        collateral_amount = collateral_contract.convertToAssets(collateral_balance, block_identifier=block) / 1e18
        collateral_usd = collateral_amount * collateral_price

        # Borrow value at block
        borrow_shares = pair.userBorrowShares.call(user, block_identifier=block)
        borrow_amount = pair.toBorrowAmount.call(borrow_shares, True, True, block_identifier=block) / 1e18
        borrow_usd = borrow_amount * borrow_price

        net_collateral = collateral_usd - borrow_usd

        # Rewards at block
        earned = pair.earned.call(user, block_identifier=block)
        rewards = {}
        for i in range(len(earned)):
            token_address, amount = earned[i]
            token_address = _normalize_address(token_address)
            symbol = reward_tokens.get(token_address)
            if symbol is None:
                if token_address not in unknown_reward_tokens_seen:
                    log.warning(
                        "Skipping unknown reward token %s for pair %s at block %s",
                        token_address,
                        pair_address,
                        block,
                    )
                    unknown_reward_tokens_seen.add(token_address)
                continue
            reward_price = prices.get(token_address, 0)
            rewards[symbol] = (amount / 1e18) * reward_price

        total_usd = net_collateral + sum(rewards.values())

        data.append({
            'block': block,
            'timestamp': timestamp,
            'datetime': datetime.fromtimestamp(timestamp),
            'collateral_amount': collateral_amount,
            'collateral_usd': collateral_usd,
            'collateral_price': collateral_price,
            'borrow_amount': borrow_amount,
            'borrow_usd': borrow_usd,
            'borrow_price': borrow_price,
            'net_collateral': net_collateral,
            'rewards': rewards,
            'total_usd': total_usd,
        })
        fetched += 1

        now = time.monotonic()
        if (now - last_progress) >= PROGRESS_LOG_INTERVAL_SECS:
            pct = 100.0 * fetched / max(1, to_fetch)
            log.info(
                "History progress %s: fetched=%s/%s (%.1f%%) last_block=%s",
                pair_address,
                fetched,
                max(0, to_fetch),
                pct,
                block,
            )
            last_progress = now

    return data


def render_chart(historical_data, reward_tokens, redemptions, pairs, prices, pair_reward_symbols=None):
    return create_historical_charts(
        historical_data,
        reward_tokens,
        redemptions,
        pairs,
        prices,
        pair_reward_symbols=pair_reward_symbols,
    )


def print_position_summary(historical_data, reward_tokens, prices, pair_reward_symbols=None):
    """Print a colorized table showing current position breakdown."""
    # ANSI color codes
    BLUE = '\033[94m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    ORANGE = '\033[93m'
    GREEN = '\033[92m'
    PURPLE = '\033[95m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

    reward_colors = [ORANGE, GREEN, PURPLE]
    default_reward_symbols = list(dict.fromkeys(reward_tokens.values()))
    symbol_to_address = {symbol: addr for addr, symbol in reward_tokens.items()}

    print(f"\n{BOLD}{'='*75}{RESET}")
    print(f"{BOLD}POSITION SUMMARY (Current Block){RESET}")
    print(f"{'='*75}\n")

    for pair_name, data in historical_data.items():
        latest = data[-1]
        reward_symbols = pair_reward_symbols.get(pair_name, default_reward_symbols) if pair_reward_symbols else default_reward_symbols
        print(f"{BOLD}{pair_name}{RESET}")

        # Collateral breakdown
        print(f"  {BLUE}Collateral:{RESET}      ${latest['collateral_usd']:>12,.2f}  ({latest['collateral_amount']:,.4f} @ ${latest['collateral_price']:.4f})")
        print(f"  {RED}Debt:{RESET}            ${latest['borrow_usd']:>12,.2f}  ({latest['borrow_amount']:,.4f} @ ${latest['borrow_price']:.4f})")
        print(f"  {CYAN}Net Collateral:{RESET}  ${latest['net_collateral']:>12,.2f}")

        # Rewards breakdown
        for i, symbol in enumerate(reward_symbols):
            color = reward_colors[i % len(reward_colors)]
            usd_value = latest['rewards'].get(symbol, 0)
            addr = symbol_to_address.get(symbol)
            price = prices.get(addr, 0) if addr else 0
            amount = usd_value / price if price > 0 else 0
            print(f"  {color}{symbol.upper():14}{RESET}  ${usd_value:>12,.4f}  ({amount:,.4f} @ ${price:.4f})")

        print(f"  {BOLD}{'─'*45}{RESET}")
        print(f"  {BOLD}Total:{RESET}            ${latest['total_usd']:>12,.2f}\n")

    print(f"{'='*75}\n")


def create_historical_charts(historical_data, reward_tokens, redemptions, pairs, prices, pair_reward_symbols=None):
    """Create 3 stacked area charts, one per position, with redemption overlays."""
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    # Chart palette (requested)
    # Stack order is: net collateral, then rewards in CONFIG["reward_tokens"] insertion order.
    NET_COLLATERAL_COLOR = '#E6E6E6'  # very light gray
    REWARD_COLOR_BY_SYMBOL = {
        'rsup': '#9B6CF0',  # lighter purple
        'crv': '#FFA24A',   # lighter orange
        'cvx': '#E35D5B',   # lighter red
        'dola': '#5BA7B0',  # muted teal
    }
    REUSD_PRICE_COLOR = '#4CAF50'  # lighter green for contrast
    REDEMPTION_VLINE_COLOR = '#555555'  # dark grey
    GOV_PROPOSAL_16_VLINE_COLOR = '#1F77B4'  # blue

    # Build reward symbols list and labels with prices
    default_reward_symbols = list(dict.fromkeys(reward_tokens.values()))
    symbol_to_address = {symbol: addr for addr, symbol in reward_tokens.items()}
    legend_reward_symbols = default_reward_symbols
    if pair_reward_symbols:
        legend_reward_symbols = list(dict.fromkeys(
            symbol
            for pair_name in historical_data.keys()
            for symbol in pair_reward_symbols.get(pair_name, default_reward_symbols)
        ))

    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    fig.patch.set_facecolor('#FAFAFA')

    # Calculate global y-axis bounds across all positions for consistent comparison
    global_min_net_collateral = min(
        min(d['net_collateral'] for d in data)
        for data in historical_data.values()
    )
    global_max_total = max(
        max(d['total_usd'] for d in data)
        for data in historical_data.values()
    )
    value_range = global_max_total - global_min_net_collateral
    y_padding = (value_range * 0.01) if value_range else 1.0  # 1% padding
    y_min = global_min_net_collateral - y_padding
    y_max = global_max_total + y_padding

    # Calculate price y-axis bounds (auto-scaled to actual data, shared for both overlays)
    price_y_min, price_y_max = None, None
    if SHOW_CRVUSD_PRICE or SHOW_REUSD_PRICE:
        all_prices = []
        if SHOW_CRVUSD_PRICE:
            all_prices.extend([
                d['collateral_price']
                for data in historical_data.values()
                for d in data
            ])
        if SHOW_REUSD_PRICE:
            all_prices.extend([
                d['borrow_price']
                for data in historical_data.values()
                for d in data
            ])
        # Keep the 1.0 peg line in-frame even if prices drift slightly away.
        all_prices.append(1.0)
        price_min = min(all_prices)
        price_max = max(all_prices)
        price_range = price_max - price_min or 0.001  # Avoid zero range
        price_padding = price_range * 0.01  # 1% padding
        price_y_min = price_min - price_padding
        price_y_max = price_max + price_padding

    # Starting value for % change calculation
    STARTING_VALUE = 1000

    # Sort pairs by current value descending
    sorted_pairs = sorted(
        historical_data.items(),
        key=lambda x: x[1][-1]['total_usd'],
        reverse=True
    )

    legend_handles = [
        Patch(facecolor=NET_COLLATERAL_COLOR, alpha=0.8, label='Net Collateral')
    ]
    legend_labels = ['Net Collateral']
    for symbol in legend_reward_symbols:
        addr = symbol_to_address.get(symbol)
        price = prices.get(addr, 0) if addr else 0
        legend_handles.append(
            Patch(
                facecolor=REWARD_COLOR_BY_SYMBOL.get(symbol.lower(), '#999999'),
                alpha=0.8,
                label=f'{symbol.upper()} (${price:.4f})',
            )
        )
        legend_labels.append(f'{symbol.upper()} (${price:.4f})')
    if SHOW_CRVUSD_PRICE:
        price_line = Line2D([0], [0], color='#888888', linestyle='--',
                            linewidth=1.2, alpha=0.9, label='crvUSD Price')
        legend_handles.append(price_line)
        legend_labels.append('crvUSD Price')
    if SHOW_REUSD_PRICE:
        price_line = Line2D([0], [0], color=REUSD_PRICE_COLOR, linestyle='-',
                            linewidth=1.2, alpha=0.9, label='reUSD Price')
        legend_handles.append(price_line)
        legend_labels.append('reUSD Price')

    # Governance proposal marker (draw on each chart).
    gov16_dt = datetime.fromtimestamp(get_block_timestamp(GOV_PROPOSAL_16_BLOCK))

    for idx, (pair_name, data) in enumerate(sorted_pairs):
        ax = axes[idx]
        ax.set_facecolor('#FAFAFA')
        pair_address = pairs[pair_name].lower()

        dates = [d['datetime'] for d in data]
        net_collateral = [d['net_collateral'] for d in data]

        pair_symbols = pair_reward_symbols.get(pair_name, default_reward_symbols) if pair_reward_symbols else default_reward_symbols
        reward_labels_with_prices = []
        for symbol in pair_symbols:
            addr = symbol_to_address.get(symbol)
            price = prices.get(addr, 0) if addr else 0
            reward_labels_with_prices.append(f'{symbol.upper()} (${price:.4f})')
        stack_colors = [NET_COLLATERAL_COLOR] + [
            REWARD_COLOR_BY_SYMBOL.get(symbol.lower(), '#999999') for symbol in pair_symbols
        ]

        # Build stacked arrays: net_collateral + each reward configured for this pair
        y_data = [net_collateral]
        stack_labels = ['Net Collateral'] + reward_labels_with_prices

        for symbol in pair_symbols:
            values = [d['rewards'].get(symbol, 0) for d in data]
            y_data.append(values)

        # Stacked area chart
        ax.stackplot(dates, *y_data, labels=stack_labels, colors=stack_colors, alpha=0.8)

        # Remove x-axis padding (eliminate gap between y-axis and data start)
        ax.set_xlim(dates[0], dates[-1])

        # Secondary y-axis for price overlays (crvUSD and/or reUSD)
        if SHOW_CRVUSD_PRICE or SHOW_REUSD_PRICE:
            ax2 = ax.twinx()
            # Ensure ax2 renders on top of ax
            ax2.set_zorder(ax.get_zorder() + 1)
            ax2.patch.set_visible(False)  # Keep background transparent

            if SHOW_CRVUSD_PRICE:
                collateral_prices = [d['collateral_price'] for d in data]
                ax2.plot(dates, collateral_prices, color='#888888', linestyle='--',
                         linewidth=1.2, alpha=0.9, label='crvUSD Price', zorder=10)

            if SHOW_REUSD_PRICE:
                borrow_prices = [d['borrow_price'] for d in data]
                ax2.plot(dates, borrow_prices, color=REUSD_PRICE_COLOR, linestyle='-',
                         linewidth=1.2, alpha=0.9, label='reUSD Price', zorder=10)

            ax2.set_ylim(price_y_min, price_y_max)
            ax2.ticklabel_format(useOffset=False, style='plain', axis='y')

            # Set axis label based on which overlays are enabled (always dark gray)
            if SHOW_CRVUSD_PRICE and SHOW_REUSD_PRICE:
                ax2.set_ylabel('Price', fontsize=8, color='#555555', fontweight='light')
            elif SHOW_REUSD_PRICE:
                ax2.set_ylabel('reUSD Price', fontsize=8, color='#555555', fontweight='light')
            else:
                ax2.set_ylabel('crvUSD Price', fontsize=8, color='#555555', fontweight='light')
            ax2.tick_params(axis='y', labelsize=7, colors='#555555', width=0.5)
            ax2.spines['right'].set_color('#555555')

            ax2.spines['right'].set_linewidth(0.5)
            # Reference line at 1.0 (peg)
            ax2.axhline(y=1.0, color='#AAAAAA', linestyle=':', linewidth=0.5, alpha=0.4, zorder=9)

        # Title with current value, total return from $1000, and APR annualized over the observed period.
        latest_total = data[-1]['total_usd']
        pct_change = ((latest_total - STARTING_VALUE) / STARTING_VALUE) * 100
        elapsed_seconds = max(0.0, (dates[-1] - dates[0]).total_seconds())
        elapsed_years = elapsed_seconds / (365.25 * 24 * 60 * 60) if elapsed_seconds else 0.0
        apr = (pct_change / elapsed_years) if elapsed_years > 0 else 0.0
        pct_str = f'({pct_change:+.1f}% | {apr:+.1f}% APR)'
        ax.set_title(
            f'{pair_name}   ·   Current Value ${latest_total:,.2f} {pct_str}',
            fontsize=11, fontweight='medium', loc='center', pad=8, color='#333'
        )

        ax.set_ylabel('USD', fontsize=8, color='#888', fontweight='light')
        ax.ticklabel_format(useOffset=False, style='plain', axis='y')

        # Use global y-axis bounds for consistent comparison across charts
        ax.set_ylim(bottom=y_min, top=y_max)

        # Draw redemption event lines for this pair
        pair_redemptions = [r for r in redemptions if r['pair_address'].lower() == pair_address]
        for redemption in pair_redemptions:
            ax.axvline(x=redemption['datetime'], color=REDEMPTION_VLINE_COLOR, linestyle='--',
                       linewidth=0.7, alpha=0.8)

        # Draw governance proposal #16 execution marker (same on every chart)
        ax.axvline(x=gov16_dt, color=GOV_PROPOSAL_16_VLINE_COLOR, linestyle='-',
                   linewidth=1, alpha=0.9)

        # Clean up spines
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_color('#ddd')
        ax.spines['bottom'].set_color('#ddd')
        ax.spines['left'].set_linewidth(0.5)
        ax.spines['bottom'].set_linewidth(0.5)

        # Minimal grid
        ax.grid(True, axis='y', alpha=0.3, linestyle='-', linewidth=0.5, color='#ccc')
        ax.set_axisbelow(True)

        # Typography: lighter axis labels
        ax.tick_params(axis='y', labelsize=8, colors='#666', width=0.5)
        ax.tick_params(axis='x', labelsize=7, colors='#666', width=0.5)


    # Custom date formatter: stacked date/time
    def stacked_date_formatter(x, pos):
        dt = mdates.num2date(x)
        return f"{dt.strftime('%b %d')}\n{dt.strftime('%H:%M')}"

    axes[-1].xaxis.set_major_formatter(FuncFormatter(stacked_date_formatter))
    axes[-1].xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
    plt.setp(axes[-1].xaxis.get_majorticklabels(), ha='center', fontsize=7, color='#666')

    fig.suptitle(
        'Position Values Over Time',
        fontsize=14, fontweight='medium', color='#333', y=0.99
    )

    # Add figure-level legend above all charts
    redemption_line = Line2D([0], [0], color=REDEMPTION_VLINE_COLOR, linestyle='--',
                             linewidth=0.7, alpha=0.8, label='Redemption')
    legend_handles.append(redemption_line)
    legend_labels.append('Redemption')

    gov16_line = Line2D([0], [0], color=GOV_PROPOSAL_16_VLINE_COLOR, linestyle='-',
                        linewidth=1, alpha=0.9, label='Gov Proposal #16')
    legend_handles.append(gov16_line)
    legend_labels.append('Gov Proposal #16')
    fig.legend(
        legend_handles, legend_labels,
        loc='upper center', ncol=len(legend_labels), fontsize=8, frameon=True,
        facecolor='white', edgecolor='#ddd', framealpha=0.95,
        borderpad=0.5, labelspacing=0.3, bbox_to_anchor=(0.5, 0.96)
    )

    plt.tight_layout(rect=[0, 0.02, 1, 0.92])
    plt.subplots_adjust(hspace=0.25)
    return fig


def _get_open_data_path(filename):
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    open_data_root = os.path.abspath(os.path.join(repo_root, "..", "open-data"))
    return os.path.join(open_data_root, filename)


def _cache_config(user, reusd, reward_tokens, pair_reward_tokens, pairs, pools):
    return {
        "user": user,
        "reusd": reusd,
        "reward_tokens": reward_tokens,
        "pair_reward_tokens": pair_reward_tokens,
        "pairs": pairs,
        "pools": pools,
        "start_block": START_BLOCK,
        "sample_interval": SAMPLE_INTERVAL,
    }


def _load_cache(cache_path, cache_config):
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r") as handle:
            payload = json.load(handle)
        if payload.get("cache_version") != 1:
            return {}
        if payload.get("config") != cache_config:
            return {}
        historical_data = _deserialize_history(payload.get("historical_data", {}))
        redemptions = _deserialize_redemptions(payload.get("redemptions", []))
        sample_blocks = sorted(payload.get("sample_blocks", []), key=lambda x: x[0])
        return {
            "sample_blocks": sample_blocks,
            "historical_data": historical_data,
            "redemptions": redemptions,
            "last_redemption_block": payload.get("last_redemption_block", START_BLOCK - 1),
        }
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _save_cache(cache_path, cache_config, sample_blocks, historical_data, redemptions):
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    last_redemption_block = max((r["block"] for r in redemptions), default=START_BLOCK - 1)
    payload = {
        "cache_version": 1,
        "config": cache_config,
        "sample_blocks": [[b, t] for b, t in sample_blocks],
        "historical_data": _serialize_history(historical_data),
        "redemptions": _serialize_redemptions(redemptions),
        "last_redemption_block": last_redemption_block,
    }
    with open(cache_path, "w") as handle:
        json.dump(payload, handle, indent=2)


def _serialize_history(historical_data):
    serialized = {}
    for pair_name, records in historical_data.items():
        serialized[pair_name] = [_serialize_record(record) for record in records]
    return serialized


def _deserialize_history(historical_data):
    deserialized = {}
    for pair_name, records in historical_data.items():
        deserialized[pair_name] = [_deserialize_record(record) for record in records]
    return deserialized


def _serialize_record(record):
    clean = dict(record)
    clean.pop("datetime", None)
    return clean


def _deserialize_record(record):
    rebuilt = dict(record)
    rebuilt["datetime"] = datetime.fromtimestamp(record["timestamp"])
    return rebuilt


def _serialize_redemptions(redemptions):
    serialized = []
    for redemption in redemptions:
        clean = dict(redemption)
        clean.pop("datetime", None)
        serialized.append(clean)
    return serialized


def _deserialize_redemptions(redemptions):
    deserialized = []
    for redemption in redemptions:
        rebuilt = dict(redemption)
        rebuilt["datetime"] = datetime.fromtimestamp(redemption["timestamp"])
        deserialized.append(rebuilt)
    return deserialized


def _start_timestamp(cached_blocks):
    if not cached_blocks:
        return get_block_timestamp(START_BLOCK)
    last_timestamp = cached_blocks[-1][1]
    return last_timestamp + SAMPLE_INTERVAL


def _should_run_for_window(meta_path, output_path, window_start):
    if output_path:
        try:
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                return True
        except OSError:
            return True
    if not os.path.exists(meta_path):
        return True
    try:
        with open(meta_path, "r") as handle:
            payload = json.load(handle)
        return payload.get("window_start") != window_start
    except (json.JSONDecodeError, OSError, ValueError):
        return True


def _write_meta(meta_path, now_ts, output_path, user, pairs, reward_tokens, pair_reward_tokens, prices, latest_block):
    if not meta_path:
        return
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    window_start = int(now_ts // SAMPLE_INTERVAL) * SAMPLE_INTERVAL
    payload = {
        "last_refresh": datetime.utcfromtimestamp(now_ts).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_start": window_start,
        "window_interval": SAMPLE_INTERVAL,
        "image_path": os.path.basename(output_path),
        "latest_block": latest_block,
        "user": user,
        "pairs": pairs,
        "reward_tokens": reward_tokens,
        "pair_reward_tokens": pair_reward_tokens,
        "prices": prices,
    }
    with open(meta_path, "w") as handle:
        json.dump(payload, handle, indent=2)

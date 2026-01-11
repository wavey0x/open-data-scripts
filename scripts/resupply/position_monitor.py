from brownie import Contract, chain, web3, interface
from web3._utils.events import construct_event_topic_set
from utils.utils import get_prices, get_block_timestamp, get_block_before_timestamp
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from datetime import datetime
import json
import os
import logging
import time

START_BLOCK = 24205787
SAMPLE_INTERVAL = 6 * 60 * 60  # 6 hours in seconds

def main(output_path=None, meta_path=None, now_ts=None):
    log = logging.getLogger(__name__)
    if meta_path:
        now_ts = now_ts or datetime.utcnow().timestamp()
        window_start = int(now_ts // SAMPLE_INTERVAL) * SAMPLE_INTERVAL
        if not _should_run_for_window(meta_path, window_start):
            log.info("Chart generation skipped; window_start %s already processed.", window_start)
            return {"skipped": True, "window_start": window_start}

    start = time.monotonic()
    user = '0xe5BcBdf9452af0AB4b042D9d8a3c1E527E26419f'
    reusd = '0x57aB1E0003F623289CD798B1824Be09a793e4Bec'

    reward_tokens = {
        '0x419905009e4656fdC02418C7Df35B1E61Ed5F726': 'rsup',
        '0x4e3FBD56CD56c3e72c1403e103b45Db9da5B9D2B': 'cvx',
        '0xD533a949740bb3306d119CC777fa900bA034cd52': 'crv',
    }

    pairs = {
        '16x (wsteth)': '0x4A7c64932d1ef0b4a2d430ea10184e3B87095E33',
        '10x (wbtc)': '0x2d8ecd48b58e53972dBC54d8d0414002B41Abc9D',
        '4x (sdola)': '0x27AB448a75d548ECfF73f8b4F36fCc9496768797',
    }

    # Fetch redemption events FIRST (need blocks for sampling)
    log.info("Fetching redemption events...")
    redemptions = get_redemption_events(list(pairs.values()))

    # Get sample blocks (6-hour intervals + redemption blocks)
    redemption_blocks = [(r['block'], r['timestamp']) for r in redemptions]
    sample_blocks = get_sample_blocks(extra_blocks=redemption_blocks)
    log.info("Sampling %s blocks from %s to %s", len(sample_blocks), START_BLOCK, chain.height)

    # Collect all token addresses for price lookup
    all_tokens = set(reward_tokens.keys())
    all_tokens.add(reusd)
    for pair_address in pairs.values():
        pair = Contract(pair_address)
        collateral_contract = Contract(pair.collateral())
        all_tokens.add(collateral_contract.asset())

    # Fetch current prices (used for all historical data)
    prices = get_prices(tokens=list(all_tokens))
    log.info("Fetched prices for %s tokens", len(prices))

    # Fetch historical data for each position
    historical_data = {}
    for pair_name, pair_address in pairs.items():
        log.info("Fetching historical data for %s...", pair_name)
        historical_data[pair_name] = fetch_historical_data(
            pair_address, user, reward_tokens, reusd, prices, sample_blocks
        )

    # Print summary table for current block
    print_position_summary(historical_data, reward_tokens, prices)

    # Create stacked area charts with redemption overlays
    fig = create_historical_charts(historical_data, reward_tokens, redemptions, pairs, prices)
    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        fig.savefig(output_path, dpi=150)
        _write_meta(meta_path, now_ts or datetime.utcnow().timestamp(), output_path)
        plt.close(fig)
    else:
        plt.show()

    log.info("position_monitor complete in %.2fs", time.monotonic() - start)
    return historical_data


def get_sample_blocks(extra_blocks=None):
    """Generate list of (block, timestamp) tuples from START_BLOCK to now, every 6 hours.

    Args:
        extra_blocks: Optional list of (block, timestamp) tuples to merge in (e.g., redemption blocks)
    """
    start_timestamp = get_block_timestamp(START_BLOCK)
    current_block = chain.height
    current_timestamp = get_block_timestamp(current_block)

    sample_blocks = []
    t = start_timestamp
    while t <= current_timestamp:
        block = get_block_before_timestamp(t)
        sample_blocks.append((block, t))
        t += SAMPLE_INTERVAL

    # Ensure current block is included as final sample
    if sample_blocks[-1][0] != current_block:
        sample_blocks.append((current_block, current_timestamp))

    # Merge extra blocks (e.g., redemption blocks)
    if extra_blocks:
        sample_blocks.extend(extra_blocks)
        # Sort by block number and deduplicate
        sample_blocks = sorted(set(sample_blocks), key=lambda x: x[0])

    return sample_blocks


def get_redemption_events(pair_addresses):
    """Fetch redemption events for specified pairs from START_BLOCK to now."""
    contract = web3.eth.contract(pair_addresses[0], abi=interface.IResupplyPair.abi)
    topics = construct_event_topic_set(
        contract.events.Redeemed().abi,
        web3.codec,
        {}
    )

    logs = web3.eth.get_logs({
        'fromBlock': START_BLOCK,
        'toBlock': chain.height,
        'topics': topics,
        'address': pair_addresses
    })
    events = contract.events.Redeemed().process_receipt({'logs': logs})

    redemptions = []
    for event in events:
        block = chain[event.blockNumber]
        redemptions.append({
            'timestamp': block.timestamp,
            'datetime': datetime.fromtimestamp(block.timestamp),
            'block': event.blockNumber,
            'pair_address': str(event.address),  # Ensure string, not HexBytes
            'amount': event.args['_amount'] / 1e18,
        })

    logging.getLogger(__name__).info("Found %s redemption events", len(redemptions))

    # Debug: show redemption counts per pair address
    from collections import Counter
    addr_counts = Counter(r['pair_address'].lower() for r in redemptions)
    for addr, count in addr_counts.items():
        logging.getLogger(__name__).info("  %s: %s redemptions", addr, count)

    return redemptions


def fetch_historical_data(pair_address, user, reward_tokens, reusd, prices, sample_blocks):
    """Fetch position data at each sample block for a single pair."""
    pair = Contract(pair_address)
    collateral_contract = Contract(pair.collateral())
    asset = collateral_contract.asset()

    pool = Contract('0xc522a6606bba746d7960404f22a3db936b6f4f50')
    collateral_price = prices.get(asset, 1.0)  # Collateral at market price
    # borrow_price = prices.get(reusd, 1.0)  # reUSD at market price
    # collateral_price = 1
    # borrow_price = 1

    data = []
    for block, timestamp in sample_blocks:
        borrow_price = 1e18 / pool.price_oracle(0, block_identifier=block)
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
            symbol = reward_tokens[token_address]
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

    return data


def print_position_summary(historical_data, reward_tokens, prices):
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
    reward_symbols = list(reward_tokens.values())

    print(f"\n{BOLD}{'='*75}{RESET}")
    print(f"{BOLD}POSITION SUMMARY (Current Block){RESET}")
    print(f"{'='*75}\n")

    for pair_name, data in historical_data.items():
        latest = data[-1]
        print(f"{BOLD}{pair_name}{RESET}")

        # Collateral breakdown
        print(f"  {BLUE}Collateral:{RESET}      ${latest['collateral_usd']:>12,.2f}  ({latest['collateral_amount']:,.4f} @ ${latest['collateral_price']:.4f})")
        print(f"  {RED}Debt:{RESET}            ${latest['borrow_usd']:>12,.2f}  ({latest['borrow_amount']:,.4f} @ ${latest['borrow_price']:.4f})")
        print(f"  {CYAN}Net Collateral:{RESET}  ${latest['net_collateral']:>12,.2f}")

        # Rewards breakdown
        for i, symbol in enumerate(reward_symbols):
            color = reward_colors[i % len(reward_colors)]
            usd_value = latest['rewards'].get(symbol, 0)
            addr = [k for k, v in reward_tokens.items() if v == symbol][0]
            price = prices.get(addr, 0)
            amount = usd_value / price if price > 0 else 0
            print(f"  {color}{symbol.upper():14}{RESET}  ${usd_value:>12,.4f}  ({amount:,.4f} @ ${price:.4f})")

        print(f"  {BOLD}{'─'*45}{RESET}")
        print(f"  {BOLD}Total:{RESET}            ${latest['total_usd']:>12,.2f}\n")

    print(f"{'='*75}\n")


def create_historical_charts(historical_data, reward_tokens, redemptions, pairs, prices):
    """Create 3 stacked area charts, one per position, with redemption overlays."""
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter

    # Enhanced color palette with better contrast
    colors = ['#3D7A9E', '#D4912E', '#4AA366', '#8B6AAF']

    # Build reward symbols list and labels with prices
    reward_symbols = list(reward_tokens.values())
    reward_labels_with_prices = []
    for addr, symbol in reward_tokens.items():
        price = prices.get(addr, 0)
        reward_labels_with_prices.append(f'{symbol.upper()} (${price:.4f})')

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
    y_padding = value_range * 0.1 or 1
    natural_y_min = global_min_net_collateral - y_padding
    y_min = min(1000, natural_y_min)  # Floor at 1000 unless data goes lower
    y_max = global_max_total + y_padding

    # Starting value for % change calculation
    STARTING_VALUE = 1000

    legend_handles, legend_labels = None, None

    for idx, (pair_name, data) in enumerate(historical_data.items()):
        ax = axes[idx]
        ax.set_facecolor('#FAFAFA')
        pair_address = pairs[pair_name].lower()

        dates = [d['datetime'] for d in data]
        net_collateral = [d['net_collateral'] for d in data]

        # Build stacked arrays: net_collateral + each reward
        y_data = [net_collateral]
        stack_labels = ['Net Collateral'] + reward_labels_with_prices

        for symbol in reward_symbols:
            values = [d['rewards'].get(symbol, 0) for d in data]
            y_data.append(values)

        # Stacked area chart
        ax.stackplot(dates, *y_data, labels=stack_labels, colors=colors, alpha=0.85)

        # Remove x-axis padding (eliminate gap between y-axis and data start)
        ax.set_xlim(dates[0], dates[-1])

        # Capture legend handles once
        if legend_handles is None:
            legend_handles, legend_labels = ax.get_legend_handles_labels()

        # Title with current value and % change from $1000 (inline)
        latest_total = data[-1]['total_usd']
        pct_change = ((latest_total - STARTING_VALUE) / STARTING_VALUE) * 100
        pct_color = '#2E7D32' if pct_change > 0 else '#C62828' if pct_change < 0 else '#333'
        pct_arrow = '+' if pct_change > 0 else ''
        # Title: pair name (left-aligned, set via set_title), then value + % via text
        ax.set_title(f'{pair_name}', fontsize=11, fontweight='medium', loc='left', pad=10, color='#333')
        # Inline value (black) and % change (colorized) next to title
        title_x = 0.22  # Position after title text
        ax.text(title_x, 1.02, f'${latest_total:,.0f}', transform=ax.transAxes,
                fontsize=11, fontweight='bold', ha='left', va='bottom', color='#333')
        ax.text(title_x + 0.08, 1.02, f'{pct_arrow}{pct_change:.1f}%', transform=ax.transAxes,
                fontsize=11, fontweight='bold', ha='left', va='bottom', color=pct_color)

        ax.set_ylabel('USD', fontsize=8, color='#888', fontweight='light')
        ax.ticklabel_format(useOffset=False, style='plain', axis='y')

        # Use global y-axis bounds for consistent comparison across charts
        ax.set_ylim(bottom=y_min, top=y_max)

        # Draw redemption event lines for this pair
        pair_redemptions = [r for r in redemptions if r['pair_address'].lower() == pair_address]
        for redemption in pair_redemptions:
            ax.axvline(x=redemption['datetime'], color='#E74C3C', linestyle='--',
                       linewidth=1, alpha=0.8)

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

        # Add legend to top-right of first chart only
        if idx == 0:
            ax.legend(
                loc='upper right', fontsize=8, frameon=True,
                facecolor='white', edgecolor='#ddd', framealpha=0.95,
                borderpad=0.5, labelspacing=0.3
            )

    # Custom date formatter: stacked date/time
    def stacked_date_formatter(x, pos):
        dt = mdates.num2date(x)
        return f"{dt.strftime('%b %d')}\n{dt.strftime('%H:%M')}"

    axes[-1].xaxis.set_major_formatter(FuncFormatter(stacked_date_formatter))
    axes[-1].xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5, maxticks=10))
    plt.setp(axes[-1].xaxis.get_majorticklabels(), ha='center', fontsize=7, color='#666')

    fig.suptitle(
        'Position Values Over Time',
        fontsize=14, fontweight='medium', color='#333', y=0.98
    )
    plt.tight_layout(rect=[0, 0.02, 1, 0.95])
    plt.subplots_adjust(hspace=0.25)
    return fig


def _should_run_for_window(meta_path, window_start):
    if not os.path.exists(meta_path):
        return True
    try:
        with open(meta_path, "r") as handle:
            payload = json.load(handle)
        return payload.get("window_start") != window_start
    except (json.JSONDecodeError, OSError, ValueError):
        return True


def _write_meta(meta_path, now_ts, output_path):
    if not meta_path:
        return
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    window_start = int(now_ts // SAMPLE_INTERVAL) * SAMPLE_INTERVAL
    payload = {
        "last_refresh": datetime.utcfromtimestamp(now_ts).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_start": window_start,
        "window_interval": SAMPLE_INTERVAL,
        "image_path": os.path.basename(output_path),
    }
    with open(meta_path, "w") as handle:
        json.dump(payload, handle, indent=2)

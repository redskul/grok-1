"""CLI proof-of-work miner.

Pure stdlib HTTP (urllib) so anyone can mine against a node with zero
extra installs:

    python -m aitoken miner --node http://localhost:8545 --address AIT...

Loop: fetch a block template, grind the nonce, submit the solved block.
Every couple of seconds of hashing it re-checks the chain height and
abandons stale work.
"""

import json
import time
import urllib.error
import urllib.request

from .block import Block, mine_header
from .config import COIN

CHECK_INTERVAL_HASHES = 200_000  # re-check chain height roughly every ~2s of hashing


class MinerError(Exception):
    pass


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def _post(url: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def mine_one_block(node_url: str, address: str) -> dict | None:
    """Fetch a template, solve it, submit it. Returns the acceptance response,
    or None if the work went stale and the caller should retry."""
    template = _get(f"{node_url}/api/mining/template?address={address}")
    block = Block.from_dict(template)
    base_height = template["index"] - 1

    start = time.time()
    nonce = 0
    while True:
        if mine_header(block, start_nonce=nonce, max_iterations=CHECK_INTERVAL_HASHES):
            break
        nonce += CHECK_INTERVAL_HASHES
        rate = nonce / max(time.time() - start, 1e-9)
        print(f"  ... {nonce:,} hashes ({rate:,.0f} H/s), difficulty {block.difficulty_bits} bits")
        chain_height = _get(f"{node_url}/api/status")["height"]
        if chain_height != base_height:
            print("  chain advanced, refetching template")
            return None

    elapsed = max(time.time() - start, 1e-9)
    print(f"  solved nonce {block.nonce:,} in {elapsed:.1f}s ({block.nonce / elapsed:,.0f} H/s)")
    status, result = _post(f"{node_url}/api/mining/submit", block.to_dict())
    if status == 200:
        return result
    print(f"  rejected ({result.get('detail', 'unknown reason')}), refetching")
    return None


def run_miner(node_url: str, address: str, max_blocks: int | None = None) -> int:
    node_url = node_url.rstrip("/")
    print(f"[miner] mining to {address} via {node_url}")
    mined = 0
    while max_blocks is None or mined < max_blocks:
        try:
            result = mine_one_block(node_url, address)
        except (urllib.error.URLError, OSError) as e:
            print(f"[miner] node unreachable ({e}), retrying in 3s")
            time.sleep(3)
            continue
        if result is None:
            continue
        mined += 1
        reward = _get(f"{node_url}/api/block/{result['height']}")["transactions"][0]["amount"]
        print(
            f"✓ block {result['height']} mined, reward {reward / COIN:g} AIT "
            f"(hash {result['hash'][:16]}…)"
        )
    return mined

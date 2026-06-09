"""Command-line entry points: node, miner, wallet."""

import argparse

from .config import NodeConfig


def main() -> None:
    parser = argparse.ArgumentParser(prog="aitoken", description="AI Token (AIT) platform")
    sub = parser.add_subparsers(dest="command", required=True)

    p_node = sub.add_parser("node", help="run the blockchain node + dashboard")
    p_node.add_argument("--host", default=None)
    p_node.add_argument("--port", type=int, default=None)
    p_node.add_argument("--db", default=None, help="SQLite database path")
    p_node.add_argument("--owner", default=None, help="platform owner address (collects fees)")
    p_node.add_argument("--fee-percent", type=float, default=None, help="exchange fee percent")

    p_miner = sub.add_parser("miner", help="run the CLI proof-of-work miner")
    p_miner.add_argument("--node", default="http://localhost:8545")
    p_miner.add_argument("--address", required=True, help="address that receives block rewards")
    p_miner.add_argument("--max-blocks", type=int, default=None)

    p_wallet = sub.add_parser("wallet", help="wallet utilities")
    wallet_sub = p_wallet.add_subparsers(dest="wallet_command", required=True)
    p_new = wallet_sub.add_parser("new", help="create a wallet keyfile")
    p_new.add_argument("--out", required=True, help="output keyfile path")

    args = parser.parse_args()

    if args.command == "node":
        cfg = NodeConfig()
        if args.host is not None:
            cfg.host = args.host
        if args.port is not None:
            cfg.port = args.port
        if args.db is not None:
            cfg.db_path = args.db
        if args.owner is not None:
            cfg.owner_address = args.owner
        if args.fee_percent is not None:
            cfg.fee_percent = args.fee_percent
        from .api import run_node

        run_node(cfg)
    elif args.command == "miner":
        from .miner import run_miner

        run_miner(args.node, args.address, max_blocks=args.max_blocks)
    elif args.command == "wallet":
        from .wallet import Wallet

        wallet = Wallet.create()
        wallet.save(args.out)
        print(f"Wallet created: {wallet.address}")
        print(f"Keyfile saved to {args.out} (plaintext private key — demo grade, guard it).")


if __name__ == "__main__":
    main()

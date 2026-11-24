import argparse
import logging
import os
import time

from services.order_service.app.transactions import reconcile_once


logger = logging.getLogger("order-reconciler")


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover stale order transactions")
    parser.add_argument("--once", action="store_true", help="Run one bounded reconciliation pass")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

    if args.once:
        recovered = reconcile_once()
        logger.info("reconciliation pass completed recovered=%s", recovered)
        return

    interval = max(5, int(os.getenv("RECONCILIATION_INTERVAL_SECONDS", "15")))
    while True:
        try:
            recovered = reconcile_once()
            logger.info("reconciliation pass completed recovered=%s", recovered)
        except Exception:
            logger.exception("reconciliation pass failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()

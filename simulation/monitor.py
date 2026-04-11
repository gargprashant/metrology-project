"""
Live pipeline monitor — watches blob containers and shows real-time progress.

Usage:
    python simulation/monitor.py
"""

import os
import time
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT_URL = os.environ.get(
    "STORAGE_ACCOUNT_URL",
    "https://metrologyprojectstorage.blob.core.windows.net",
)

credential = DefaultAzureCredential()
blob_service = BlobServiceClient(account_url=STORAGE_ACCOUNT_URL, credential=credential)

CONTAINERS = ["rawscan", "compensated", "aligned", "results", "reports"]


def count_blobs(container_name: str) -> int:
    try:
        container = blob_service.get_container_client(container_name)
        return sum(1 for _ in container.list_blobs())
    except Exception:
        return 0


def monitor(interval=3):
    print("Live Pipeline Monitor (Ctrl+C to stop)")
    print("=" * 65)

    prev_counts = {c: 0 for c in CONTAINERS}

    while True:
        counts = {c: count_blobs(c) for c in CONTAINERS}

        # Build progress bar for each stage
        raw = counts["rawscan"]
        lines = []
        for container in CONTAINERS:
            count = counts[container]
            delta = count - prev_counts[container]
            delta_str = f" (+{delta})" if delta > 0 else ""
            pct = (count / raw * 100) if raw > 0 else 0

            bar_len = 30
            filled = int(bar_len * pct / 100) if raw > 0 else 0
            bar = "█" * filled + "░" * (bar_len - filled)

            lines.append(f"  {container:<14} [{bar}] {count:>4}/{raw:<4} ({pct:5.1f}%){delta_str}")

        # Clear and reprint
        print(f"\033[{len(CONTAINERS) + 2}A", end="")  # Move cursor up
        print(f"  {'Stage':<14} {'Progress':<36} {'Count':>10}  {'Rate'}")
        print("-" * 65)
        for line in lines:
            print(f"{line:<65}")

        prev_counts = counts.copy()
        time.sleep(interval)


if __name__ == "__main__":
    try:
        # Print initial blank lines for cursor movement
        print()
        print()
        for _ in CONTAINERS:
            print()
        monitor()
    except KeyboardInterrupt:
        print("\nStopped.")

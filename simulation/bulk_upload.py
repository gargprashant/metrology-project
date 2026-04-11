"""
Bulk upload script — pushes N simulated CMM scans to rawscan/ in parallel.
Triggers the full async pipeline for each scan.

Usage:
    python simulation/bulk_upload.py --count 100 --workers 10
"""

import os
import sys
import json
import uuid
import time
import argparse
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(__file__))
import feature_generator

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

STORAGE_ACCOUNT_URL = os.environ.get(
    "STORAGE_ACCOUNT_URL",
    "https://metrologyprojectstorage.blob.core.windows.net",
)

credential = DefaultAzureCredential()
blob_service = BlobServiceClient(account_url=STORAGE_ACCOUNT_URL, credential=credential)
container_client = blob_service.get_container_client("rawscan")

SHAPES = ["cylinder", "sphere", "cone", "circle", "taper"]
CMM_IDS = [f"CMM_{str(i).zfill(2)}" for i in range(1, 11)]  # 10 simulated CMMs


def upload_one_feature(index: int) -> dict:
    """Generate and upload a single simulated scan."""
    shape = random.choice(SHAPES)
    cmm_id = random.choice(CMM_IDS)
    num_points = random.randint(200, 800)
    probe_diameter = round(random.uniform(1.0, 3.0), 1)
    noise_sigma = round(random.uniform(0.02, 0.10), 3)

    # Generate
    nominal = feature_generator.generate_features(shape=shape, num_points=num_points)
    noisy = feature_generator.add_noise(nominal, sigma=noise_sigma)
    tip_points, normals = feature_generator.simulate_probe_tip(noisy, probe_radius=probe_diameter / 2.0)

    # Build payload
    scan_id = f"{cmm_id}_{shape}_{uuid.uuid4().hex[:8]}"
    blob_name = f"{scan_id}.json"

    payload = json.loads(feature_generator.export_json(cmm_id, probe_diameter, tip_points, normals))
    payload["nominalPoints"] = [
        {"x": float(x), "y": float(y), "z": float(z)}
        for x, y, z in nominal
    ]
    payload["scanId"] = scan_id
    payload["shape"] = shape

    # Upload
    start = time.time()
    container_client.upload_blob(
        name=blob_name,
        data=json.dumps(payload),
        overwrite=True,
    )
    elapsed = time.time() - start

    return {
        "index": index,
        "blob_name": blob_name,
        "cmm_id": cmm_id,
        "shape": shape,
        "points": num_points,
        "upload_time": round(elapsed, 3),
    }


def main():
    parser = argparse.ArgumentParser(description="Bulk upload CMM scans")
    parser.add_argument("--count", type=int, default=100, help="Number of scans to upload")
    parser.add_argument("--workers", type=int, default=10, help="Parallel upload threads")
    args = parser.parse_args()

    print(f"Uploading {args.count} scans with {args.workers} parallel workers...")
    print(f"Storage: {STORAGE_ACCOUNT_URL}")
    print()

    start = time.time()
    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(upload_one_feature, i): i
            for i in range(args.count)
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                completed += 1
                if completed % 10 == 0 or completed == args.count:
                    print(f"  [{completed}/{args.count}] {result['blob_name']} "
                          f"({result['shape']}, {result['points']} pts, {result['upload_time']}s)")
            except Exception as e:
                failed += 1
                print(f"  FAILED [{futures[future]}]: {e}")

    elapsed = time.time() - start
    print()
    print(f"Done in {elapsed:.1f}s")
    print(f"  Uploaded: {completed}")
    print(f"  Failed:   {failed}")
    print(f"  Rate:     {completed / elapsed:.1f} scans/sec")
    print()
    print("Pipeline is now processing all scans asynchronously.")
    print("Check blob containers to monitor progress:")
    print("  rawscan/      -> uploaded scans")
    print("  compensated/  -> probe compensation done")
    print("  aligned/      -> alignment done")
    print("  results/      -> GD&T evaluation done")
    print("  reports/      -> final reports")


if __name__ == "__main__":
    main()

"""
Targeted upload — 10 CMMs x 10 features each = 100 scans with predictable naming.

Usage:
    python simulation/targeted_upload.py
"""

import os
import sys
import json
import time
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

SHAPES = ["cylinder", "sphere", "cone", "circle", "taper",
          "cylinder", "sphere", "cone", "circle", "taper"]
NUM_CMMS = 10
FEATURES_PER_CMM = 10


def upload_feature(cmm_idx: int, feature_idx: int) -> str:
    cmm_id = f"CMM_{str(cmm_idx + 1).zfill(2)}"
    shape = SHAPES[feature_idx]
    blob_name = f"{cmm_id}_F{str(feature_idx + 1).zfill(2)}.json"

    nominal = feature_generator.generate_features(shape=shape, num_points=300)
    noisy = feature_generator.add_noise(nominal, sigma=0.05)
    tip_points, normals = feature_generator.simulate_probe_tip(noisy, probe_radius=1.0)

    payload = json.loads(feature_generator.export_json(cmm_id, 2.0, tip_points, normals))
    payload["nominalPoints"] = [
        {"x": float(x), "y": float(y), "z": float(z)}
        for x, y, z in nominal
    ]
    payload["scanId"] = f"{cmm_id}_F{str(feature_idx + 1).zfill(2)}"
    payload["shape"] = shape
    payload["featureIndex"] = feature_idx
    payload["cmmIndex"] = cmm_idx

    container = blob_service.get_container_client("rawscan")
    container.upload_blob(name=blob_name, data=json.dumps(payload), overwrite=True)
    return blob_name


def main():
    print(f"Uploading {NUM_CMMS} CMMs x {FEATURES_PER_CMM} features = {NUM_CMMS * FEATURES_PER_CMM} scans")

    start = time.time()
    uploaded = 0

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for cmm_idx in range(NUM_CMMS):
            for feat_idx in range(FEATURES_PER_CMM):
                f = executor.submit(upload_feature, cmm_idx, feat_idx)
                futures[f] = (cmm_idx, feat_idx)

        for future in as_completed(futures):
            cmm_idx, feat_idx = futures[future]
            try:
                blob_name = future.result()
                uploaded += 1
                print(f"  [{uploaded:>3}/100] {blob_name}")
            except Exception as e:
                print(f"  FAILED CMM_{cmm_idx+1} F{feat_idx+1}: {e}")

    print(f"\nDone in {time.time() - start:.1f}s ({uploaded} uploaded)")


if __name__ == "__main__":
    main()

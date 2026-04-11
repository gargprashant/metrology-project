"""
Shared Azure clients for Event Grid (pull model) and Blob Storage.
All services use this module to receive events and read/write blobs.
"""

import os
import json
import logging
import time
from azure.identity import DefaultAzureCredential
from azure.eventgrid import EventGridConsumerClient
from azure.storage.blob import BlobServiceClient

logger = logging.getLogger(__name__)

# Environment variables (set per service)
EVENT_GRID_ENDPOINT = os.environ["EVENT_GRID_ENDPOINT"]       # e.g. https://MetrologyEventNamespace.eastus-1.eventgrid.azure.net
STORAGE_ACCOUNT_URL = os.environ["STORAGE_ACCOUNT_URL"]       # e.g. https://metrologyprojectstorage.blob.core.windows.net
TOPIC_NAME = os.environ["TOPIC_NAME"]                         # e.g. feature-scanned
SUBSCRIPTION_NAME = os.environ["SUBSCRIPTION_NAME"]           # e.g. probe-compensation-sub

credential = DefaultAzureCredential()

consumer_client = EventGridConsumerClient(
    endpoint=EVENT_GRID_ENDPOINT,
    credential=credential,
    namespace_topic=TOPIC_NAME,
    subscription=SUBSCRIPTION_NAME,
)

blob_service = BlobServiceClient(
    account_url=STORAGE_ACCOUNT_URL,
    credential=credential,
)


def read_blob(container: str, blob_path: str) -> dict:
    """Download and parse a JSON blob."""
    container_client = blob_service.get_container_client(container)
    blob_data = container_client.download_blob(blob_path).readall()
    return json.loads(blob_data)


def write_blob(container: str, blob_path: str, data: dict) -> str:
    """Upload a JSON blob. Returns the full blob path."""
    container_client = blob_service.get_container_client(container)
    container_client.upload_blob(
        name=blob_path,
        data=json.dumps(data, indent=2),
        overwrite=True,
    )
    logger.info(f"Wrote blob: {container}/{blob_path}")
    return f"{container}/{blob_path}"


def extract_blob_name(event) -> str:
    """Extract blob name from a BlobCreated CloudEvent.
    The subject field has format: /blobServices/default/containers/{container}/blobs/{blob_name}
    """
    subject = getattr(event, "subject", "") or ""
    if "/blobs/" in subject:
        return subject.split("/blobs/", 1)[-1]
    # Fallback: check event data for url
    data = event.data if hasattr(event, "data") else event
    if isinstance(data, dict):
        url = data.get("url", "")
        if "/" in url:
            # URL format: https://account.blob.core.windows.net/container/blob_name
            parts = url.split("/")
            if len(parts) >= 5:
                return "/".join(parts[4:])
    return ""


def poll_and_process(handler, max_events=1, max_wait_time=10):
    """
    Continuously poll the Event Grid namespace topic subscription for events.
    Calls handler(event_data) for each event received.
    On success, acknowledges the event. On failure, releases it for retry.
    max_wait_time: seconds to wait for events (min 10, max 120).
    """
    logger.info(f"Starting poll loop: topic={TOPIC_NAME}, subscription={SUBSCRIPTION_NAME}")

    while True:
        try:
            details_list = consumer_client.receive(
                max_events=max_events,
                max_wait_time=max_wait_time,
            )

            for detail in details_list:
                event = detail.event
                lock_token = detail.broker_properties.lock_token
                logger.info(f"Received event: type={event.type}, id={event.id}, subject={getattr(event, 'subject', None)}")

                try:
                    handler(event)
                    consumer_client.acknowledge(
                        lock_tokens=[lock_token],
                    )
                    logger.info(f"Acknowledged event: {event.id}")
                except Exception as e:
                    logger.error(f"Handler failed for event {event.id}: {e}", exc_info=True)
                    consumer_client.release(
                        lock_tokens=[lock_token],
                    )
                    logger.info(f"Released event for retry: {event.id}")

        except Exception as e:
            logger.error(f"Poll error: {e}", exc_info=True)
            time.sleep(10)

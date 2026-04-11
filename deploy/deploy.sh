#!/bin/bash
# Deploy Metrology Microservices to Azure Container Apps
#
# Prerequisites:
#   - Azure CLI installed and logged in (az login)
#   - Docker installed (for building images)
#
# Usage: ./deploy/deploy.sh

set -euo pipefail

# ---------- Configuration ----------
RESOURCE_GROUP="Metrology"
LOCATION="eastus"
ACR_NAME="metrologyacr"
ENVIRONMENT_NAME="metrology-env"
STORAGE_ACCOUNT="metrologyprojectstorage"
EVENT_GRID_ENDPOINT="https://MetrologyEventNamespace.eastus-1.eventgrid.azure.net"
STORAGE_ACCOUNT_URL="https://${STORAGE_ACCOUNT}.blob.core.windows.net"

# ---------- Step 1: Create Azure Container Registry ----------
echo "=== Creating Azure Container Registry ==="
az acr create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ACR_NAME" \
  --sku Basic \
  --admin-enabled true

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
echo "ACR: $ACR_LOGIN_SERVER"

# ---------- Step 2: Build and push images ----------
echo "=== Building and pushing container images ==="
az acr login --name "$ACR_NAME"

# Build from microservices/ context (shared module needed)
for SERVICE in probe_compensation alignment gdt_evaluation reporting; do
  echo "Building $SERVICE..."
  docker build \
    -t "$ACR_LOGIN_SERVER/$SERVICE:latest" \
    -f "microservices/$SERVICE/Dockerfile" \
    microservices/
  docker push "$ACR_LOGIN_SERVER/$SERVICE:latest"
done

# Dashboard (separate context)
echo "Building dashboard..."
docker build -t "$ACR_LOGIN_SERVER/dashboard:latest" dashboard/
docker push "$ACR_LOGIN_SERVER/dashboard:latest"

# ---------- Step 3: Create Container Apps Environment ----------
echo "=== Creating Container Apps Environment ==="
az containerapp env create \
  --name "$ENVIRONMENT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION"

# ---------- Step 4: Get ACR credentials ----------
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" -o tsv)

# ---------- Step 5: Deploy microservices ----------
echo "=== Deploying microservices ==="

declare -A TOPICS=(
  ["probe_compensation"]="feature-scanned"
  ["alignment"]="feature-compensated"
  ["gdt_evaluation"]="feature-aligned"
  ["reporting"]="feature-evaluated"
)

declare -A SUBS=(
  ["probe_compensation"]="probe-compensation-sub"
  ["alignment"]="alignment-sub"
  ["gdt_evaluation"]="gdt-evaluation-sub"
  ["reporting"]="reporting-sub"
)

for SERVICE in probe_compensation alignment gdt_evaluation reporting; do
  echo "Deploying $SERVICE..."
  az containerapp create \
    --name "${SERVICE//_/-}" \
    --resource-group "$RESOURCE_GROUP" \
    --environment "$ENVIRONMENT_NAME" \
    --image "$ACR_LOGIN_SERVER/$SERVICE:latest" \
    --registry-server "$ACR_LOGIN_SERVER" \
    --registry-username "$ACR_NAME" \
    --registry-password "$ACR_PASSWORD" \
    --target-port 8080 \
    --ingress internal \
    --min-replicas 0 \
    --max-replicas 20 \
    --env-vars \
      EVENT_GRID_ENDPOINT="$EVENT_GRID_ENDPOINT" \
      STORAGE_ACCOUNT_URL="$STORAGE_ACCOUNT_URL" \
      TOPIC_NAME="${TOPICS[$SERVICE]}" \
      SUBSCRIPTION_NAME="${SUBS[$SERVICE]}" \
    --system-assigned \
    --scale-rule-name "eventgrid-queue" \
    --scale-rule-type "azure-event-grid" \
    --scale-rule-metadata \
      "subscriptionName=${SUBS[$SERVICE]}" \
      "topicName=${TOPICS[$SERVICE]}" \
      "eventGridNamespaceEndpoint=$EVENT_GRID_ENDPOINT" \
    --scale-rule-auth "connection=managed-identity"
done

# Deploy dashboard
echo "Deploying dashboard..."
az containerapp create \
  --name "dashboard" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$ENVIRONMENT_NAME" \
  --image "$ACR_LOGIN_SERVER/dashboard:latest" \
  --registry-server "$ACR_LOGIN_SERVER" \
  --registry-username "$ACR_NAME" \
  --registry-password "$ACR_PASSWORD" \
  --target-port 8501 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 1 \
  --env-vars \
    STORAGE_ACCOUNT_URL="$STORAGE_ACCOUNT_URL" \
  --system-assigned

# ---------- Step 6: Assign Managed Identity roles ----------
echo "=== Assigning Managed Identity roles ==="

# Get subscription ID
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
STORAGE_RESOURCE_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT"
EVENT_GRID_RESOURCE_ID="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.EventGrid/namespaces/MetrologyEventNamespace"

for SERVICE in probe-compensation alignment gdt-evaluation reporting dashboard; do
  PRINCIPAL_ID=$(az containerapp show \
    --name "$SERVICE" \
    --resource-group "$RESOURCE_GROUP" \
    --query "identity.principalId" -o tsv)

  echo "Assigning roles for $SERVICE (principal: $PRINCIPAL_ID)"

  # Storage Blob Data Contributor — read/write blobs
  az role assignment create \
    --assignee "$PRINCIPAL_ID" \
    --role "Storage Blob Data Contributor" \
    --scope "$STORAGE_RESOURCE_ID" \
    2>/dev/null || true

  # EventGrid Data Receiver — pull events from namespace topics
  az role assignment create \
    --assignee "$PRINCIPAL_ID" \
    --role "EventGrid Data Receiver" \
    --scope "$EVENT_GRID_RESOURCE_ID" \
    2>/dev/null || true
done

# ---------- Step 7: Get dashboard URL ----------
DASHBOARD_URL=$(az containerapp show \
  --name "dashboard" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)

echo ""
echo "=== Deployment Complete ==="
echo "Dashboard URL: https://$DASHBOARD_URL"
echo ""
echo "Services deployed:"
for SERVICE in probe-compensation alignment gdt-evaluation reporting; do
  echo "  - $SERVICE (internal)"
done

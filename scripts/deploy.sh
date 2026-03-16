#!/usr/bin/env bash
# ORÁCULO — Quick manual deploy to Cloud Run
# Use this if Cloud Build triggers aren't configured yet.
#
# Usage: ./scripts/deploy.sh

set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-oraculo-hackathon}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="oraculo"
REPO_NAME="oraculo"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}"
TAG="manual-$(date +%Y%m%d-%H%M%S)"

echo "Building image: ${IMAGE}:${TAG}"
docker build -t "${IMAGE}:${TAG}" .

echo "Pushing to Artifact Registry..."
docker push "${IMAGE}:${TAG}"

echo "Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}:${TAG}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 3600 \
  --session-affinity \
  --min-instances 0 \
  --max-instances 3 \
  --concurrency 10 \
  --set-env-vars "ENVIRONMENT=production,LOG_LEVEL=INFO,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION}" \
  --set-secrets "GOOGLE_API_KEY=oraculo-gemini-key:latest,ALPHA_VANTAGE_API_KEY=oraculo-av-key:latest"

echo ""
echo "Deployed!"
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --format "value(status.url)")
echo "Service URL: ${SERVICE_URL}"
echo ""
echo "Test it:"
echo "  curl ${SERVICE_URL}/health"
echo "  open ${SERVICE_URL}"

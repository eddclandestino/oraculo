#!/usr/bin/env bash
# ORÁCULO — GCP Project Setup Script
# Run once to configure all Google Cloud resources.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - A GCP project created (or this script creates one)
#
# Usage:
#   chmod +x scripts/setup_gcp.sh
#   ./scripts/setup_gcp.sh

set -euo pipefail

# ── Configuration ──
# Override these via environment variables if needed
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-oraculo-hackathon}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="oraculo"
REPO_NAME="oraculo"

echo "==============================================="
echo "  ORÁCULO — GCP Project Setup"
echo "  Project: ${PROJECT_ID}"
echo "  Region:  ${REGION}"
echo "==============================================="
echo ""

# ── Step 1: Set project ──
echo "→ Setting active project to ${PROJECT_ID}..."
gcloud config set project "${PROJECT_ID}"

# ── Step 2: Enable required APIs ──
echo "→ Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com \
  --quiet

echo "  Done: Cloud Run, Artifact Registry, Cloud Build, Firestore, Secret Manager, Vertex AI"

# ── Step 3: Create Artifact Registry repository ──
echo ""
echo "→ Creating Artifact Registry repository..."
gcloud artifacts repositories create "${REPO_NAME}" \
  --repository-format=docker \
  --location="${REGION}" \
  --description="ORÁCULO container images" \
  --quiet 2>/dev/null || echo "  (repository already exists)"

# ── Step 4: Set up Firestore (Native mode) ──
echo ""
echo "→ Setting up Firestore in Native mode..."
gcloud firestore databases create \
  --location="${REGION}" \
  --type=firestore-native \
  --quiet 2>/dev/null || echo "  (Firestore already initialized)"

# ── Step 5: Create secrets in Secret Manager ──
echo ""
echo "→ Setting up Secret Manager..."

create_secret() {
  local secret_name="$1"
  local prompt_msg="$2"

  if gcloud secrets describe "${secret_name}" --quiet 2>/dev/null; then
    echo "  Secret '${secret_name}' already exists."
    read -p "  Update it? (y/N): " update
    if [[ "${update}" == "y" || "${update}" == "Y" ]]; then
      read -sp "  Enter new value for ${secret_name}: " secret_value
      echo ""
      echo -n "${secret_value}" | gcloud secrets versions add "${secret_name}" --data-file=-
      echo "  Updated ${secret_name}"
    fi
  else
    read -sp "  ${prompt_msg}: " secret_value
    echo ""
    if [[ -n "${secret_value}" ]]; then
      echo -n "${secret_value}" | gcloud secrets create "${secret_name}" --data-file=- --replication-policy="automatic"
      echo "  Created ${secret_name}"
    else
      echo "  Skipped ${secret_name} (empty value)"
    fi
  fi
}

create_secret "oraculo-gemini-key" "Enter your Gemini API key (from aistudio.google.com/apikey)"
create_secret "oraculo-av-key" "Enter your Alpha Vantage API key (from alphavantage.co)"

# ── Step 6: Grant Cloud Run access to secrets ──
echo ""
echo "→ Granting Cloud Run service account access to secrets..."
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")
SA_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for secret in oraculo-gemini-key oraculo-av-key; do
  gcloud secrets add-iam-policy-binding "${secret}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet 2>/dev/null || true
done
echo "  Service account can access secrets"

# ── Step 7: Grant Cloud Build permissions ──
echo ""
echo "→ Granting Cloud Build permissions..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/run.admin" \
  --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser" \
  --quiet 2>/dev/null || true

echo "  Cloud Build can deploy to Cloud Run"

# ── Summary ──
echo ""
echo "==============================================="
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "    1. Deploy manually:  gcloud builds submit --config cloudbuild.yaml"
echo "    2. Or use: ./scripts/deploy.sh"
echo ""
echo "  Service will be available at:"
echo "    https://${SERVICE_NAME}-${PROJECT_NUMBER}.${REGION}.run.app"
echo "==============================================="

#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
# build-allinone.sh
# Builds the single-container VAYU image and (optionally) pushes it to a
# registry so a friend can `docker pull` it directly.
#
# Usage:
#   ./build-allinone.sh                          # just build locally
#   ./build-allinone.sh yourname/vayu:latest     # build + tag
#   ./build-allinone.sh yourname/vayu:latest push # build + tag + push
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

TAG="${1:-vayu:local}"
ACTION="${2:-}"

echo "── Checking backend/.env exists ────────────────────────────────────────"
if [ ! -f "backend/.env" ]; then
  echo "ERROR: backend/.env not found."
  echo "Create it with your real GROQ_API_KEY, GOOGLE_APPLICATION_CREDENTIALS_JSON,"
  echo "etc. before building — see backend/.env.example for the full list."
  exit 1
fi

echo "── Building image: $TAG ────────────────────────────────────────────────"
docker build -f Dockerfile.allinone -t "$TAG" .

echo ""
echo "Build complete: $TAG"
echo ""
echo "Test it locally with:"
echo "  docker run -d -p 8080:80 --name vayu-test $TAG"
echo "  open http://localhost:8080"
echo ""

if [ "$ACTION" = "push" ]; then
  echo "── Pushing to registry ──────────────────────────────────────────────"
  docker push "$TAG"
  echo "Pushed. Your friend can now run:"
  echo "  docker pull $TAG"
  echo "  docker run -d -p 8080:80 $TAG"
fi

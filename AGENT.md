# AGENT.md — gpu-container

## Overview
Serverless GPU container running on RunPod. Executes ComfyUI workflows for image/video generation (Deforum, AnimateDiff, Wan, Uprez, Qwen).

## Stack
- **Runtime:** Python, Docker (linux/amd64)
- **GPU framework:** ComfyUI + PyTorch
- **Serverless:** RunPod SDK
- **Storage:** boto3 (S3/R2 uploads)
- **Based on:** runpod-worker-comfy v3.4.0

## Project Structure
```
src/              # Main application code (RunPod handler, ComfyUI integration)
tests/            # Unit tests
data/             # ComfyUI workflow templates
test_resources/   # Test fixtures
Dockerfile        # Multi-stage Docker build
docker-compose.yml # Local dev setup
```

## Commands
```bash
docker build -t comfy:dev-base --target base --platform linux/amd64 .
docker-compose up          # Local dev (ComfyUI: 8188, API: 8000)
python -m unittest discover  # Run tests
```

## Key Patterns
- RunPod handler receives job requests with ComfyUI workflow parameters
- ComfyUI executes the workflow on GPU
- Results are uploaded to S3/R2 via boto3
- Docker image includes model weights and ComfyUI custom nodes
- Different model endpoints share the same container with different workflow configs

## Deployment
RunPod — Docker Hub via GitHub Actions.

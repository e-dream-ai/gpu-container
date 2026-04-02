# AGENTS.md — gpu-container

## Overview

Docker-based ComfyUI serverless worker for RunPod. Provides GPU-accelerated image/video generation with support for multiple diffusion models.

## Stack

- **Base Image:** nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04
- **Language:** Python 3.10
- **Framework:** ComfyUI + RunPod SDK
- **Dependencies:** runpod, boto3, requests, websocket-client

## Project Structure

```
src/
  rp_handler.py              # RunPod job handler
  start.sh                   # Container startup script
  restore_snapshot.sh         # Custom node/model restoration
  extra_model_paths.yaml     # Model path configuration
  test_input.json            # Test workflow input
tests/
  test_rp_handler.py         # Handler tests
Dockerfile                   # Multi-stage build (base + model download)
```

## Commands

```bash
# Build with specific model type
docker build --build-arg MODEL_TYPE=sdxl --target base --platform linux/amd64 -t comfy:dev-base .

# Local development
docker-compose up            # ComfyUI: 8188, API: 8000

# Tests
python -m unittest discover
```

## Key Patterns

- Multi-stage Docker builds: base (ComfyUI setup) + final (model downloads)
- Model selection via `MODEL_TYPE` build arg (sdxl, sd3, flux1-schnell, flux1-dev)
- Snapshot-based custom node management
- RunPod serverless handler pattern
- Optional Cloudflare R2 integration for result storage

## Deployment

RunPod Serverless endpoints. Built via GitHub Actions, pushed to Docker Hub.

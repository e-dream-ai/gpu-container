# Stage 1: Base image with common dependencies
FROM nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04 AS base

# Prevents prompts from packages asking for user input during installation
ENV DEBIAN_FRONTEND=noninteractive
# Prefer binary wheels over source distributions for faster pip installations
ENV PIP_PREFER_BINARY=1
# Ensures output from python is printed immediately to the terminal without buffering
ENV PYTHONUNBUFFERED=1 
# Speed up some cmake builds
ENV CMAKE_BUILD_PARALLEL_LEVEL=8

# Install Python, git and other necessary tools
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    git \
    wget \
    libgl1 \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && ln -sf /usr/bin/python3.10 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip

# Clean up to reduce image size
RUN apt-get autoremove -y && apt-get clean -y && rm -rf /var/lib/apt/lists/*

# Install comfy-cli
RUN pip install comfy-cli

# Install ComfyUI
RUN /usr/bin/yes | comfy --workspace /comfyui install --cuda-version 11.8 --nvidia --version 0.3.27

# disable tracking
RUN comfy tracking disable

# Change working directory to ComfyUI
WORKDIR /comfyui

# Install runpod
RUN pip install runpod requests

# free up space
RUN pip cache info && pip cache purge

# Support for the network volume
ADD src/extra_model_paths.yaml ./

# Go back to the root
WORKDIR /

# Add scripts
ADD src/start.sh src/restore_snapshot.sh src/rp_handler.py test_input.json ./
RUN chmod +x /start.sh /restore_snapshot.sh

# Optionally copy the snapshot file
ADD *snapshot*.json /

# Restore the snapshot to install custom nodes
RUN /restore_snapshot.sh

# Start container
CMD ["/start.sh"]

# Stage 2: Download models
FROM base AS final

ARG HUGGINGFACE_ACCESS_TOKEN
ARG MODEL_TYPE

# Change working directory to ComfyUI
WORKDIR /comfyui

# Create necessary directories
RUN mkdir -p models/checkpoints models/vae/sd1 models/animatediff_models/sd1 models/checkpoints/sd1 models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer models/text_encoders/openai/clip-vit-large-patch14/

# Download checkpoints/vae/LoRA to include in image based on model type
RUN if [ "$MODEL_TYPE" = "sdxl" ]; then \
      wget -nv -O models/checkpoints/sd_xl_base_1.0.safetensors https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors && \
      wget -nv -O models/vae/sdxl_vae.safetensors https://huggingface.co/stabilityai/sdxl-vae/resolve/main/sdxl_vae.safetensors && \
      wget -nv -O models/vae/sdxl-vae-fp16-fix.safetensors https://huggingface.co/madebyollin/sdxl-vae-fp16-fix/resolve/main/sdxl_vae.safetensors && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/checkpoints/sd1/dreamshaper_8.safetensors https://huggingface.co/digiplay/DreamShaper_8/resolve/main/dreamshaper_8.safetensors && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/vae/sd1/vae-ft-mse-840000-ema-pruned.safetensors https://huggingface.co/stabilityai/sd-vae-ft-mse-original/resolve/main/vae-ft-mse-840000-ema-pruned.safetensors && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/animatediff_models/sd1/mm_sd_v15_v2.ckpt https://huggingface.co/guoyww/animatediff/resolve/main/mm_sd_v15_v2.ckpt && \
      wget -nv -O models/animatediff_models/temporaldiff-v1-animatediff.safetensors https://huggingface.co/CiaraRowles/TemporalDiff/resolve/main/temporaldiff-v1-animatediff.safetensors; \
    elif [ "$MODEL_TYPE" = "sd3" ]; then \
      wget --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/checkpoints/sd3_medium_incl_clips_t5xxlfp8.safetensors https://huggingface.co/stabilityai/stable-diffusion-3-medium/resolve/main/sd3_medium_incl_clips_t5xxlfp8.safetensors; \
    elif [ "$MODEL_TYPE" = "hunyuan" ]; then \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/diffusion_models/hunyuan_video_720_cfgdistill_bf16.safetensors https://huggingface.co/Kijai/HunyuanVideo_comfy/resolve/main/hunyuan_video_720_cfgdistill_bf16.safetensors && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/vae/hunyuan_video_vae_bf16.safetensors https://huggingface.co/Kijai/HunyuanVideo_comfy/resolve/main/hunyuan_video_vae_bf16.safetensors && \

      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer/config.json https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/config.json && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer/generation_config.json https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/generation_config.json && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer/model-00001-of-00004.safetensors https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/model-00001-of-00004.safetensors && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer/model-00002-of-00004.safetensors https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/model-00002-of-00004.safetensors && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer/model-00003-of-00004.safetensors https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/model-00003-of-00004.safetensors && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer/model-00004-of-00004.safetensors https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/model-00004-of-00004.safetensors && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer/model.safetensors.index.json https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/model.safetensors.index.json && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer/special_tokens_map.json https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/special_tokens_map.json && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer/tokenizer.json https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/tokenizer.json && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/LLM/Kijai/llava-llama-3-8b-text-encoder-tokenizer/tokenizer_config.json https://huggingface.co/Kijai/llava-llama-3-8b-text-encoder-tokenizer/resolve/main/tokenizer_config.json && \

      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/config.json https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/config.json && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/flax_model.msgpack https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/flax_model.msgpack && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/merges.txt https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/merges.txt && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/model.safetensors https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/model.safetensors && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/preprocessor_config.json https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/preprocessor_config.json && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/pytorch_model.bin https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/pytorch_model.bin && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/special_tokens_map.json https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/special_tokens_map.json && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/tf_model.h5 https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/tf_model.h5 && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/tokenizer.json https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/tokenizer.json && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/tokenizer_config.json https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/tokenizer_config.json && \
      wget -nv --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/text_encoders/openai/clip-vit-large-patch14/vocab.json https://huggingface.co/openai/clip-vit-large-patch14/resolve/main/vocab.json ; \
    elif [ "$MODEL_TYPE" = "flux1-schnell" ]; then \
      wget -O models/unet/flux1-schnell.safetensors https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/flux1-schnell.safetensors && \
      wget -O models/clip/clip_l.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors && \
      wget -O models/clip/t5xxl_fp8_e4m3fn.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors && \
      wget -O models/vae/ae.safetensors https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors; \
    elif [ "$MODEL_TYPE" = "flux1-dev" ]; then \
      wget --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/unet/flux1-dev.safetensors https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/flux1-dev.safetensors && \
      wget -O models/clip/clip_l.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors && \
      wget -O models/clip/t5xxl_fp8_e4m3fn.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors && \
      wget --header="Authorization: Bearer ${HUGGINGFACE_ACCESS_TOKEN}" -O models/vae/ae.safetensors https://huggingface.co/black-forest-labs/FLUX.1-dev/resolve/main/ae.safetensors; \
    fi

# Start container
CMD ["/start.sh"]
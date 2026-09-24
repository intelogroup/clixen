FROM runpod/worker-comfyui:5.8.6-base

# Hunyuan3D-2.1 wrapper node — not in comfy-registry, clone direct (pattern matches runpod-cogvideox's kijai node install)
RUN cd /comfyui/custom_nodes && \
    git clone --recursive https://github.com/visualbruno/ComfyUI-Hunyuan3d-2-1.git && \
    pip install --no-cache-dir -r ComfyUI-Hunyuan3d-2-1/requirements.txt

# custom_rasterizer + DifferentiableRenderer — precompiled wheels in repo are win_amd64 only,
# this base is Linux so build from source. worker-comfyui base has CUDA *runtime* libs only,
# no nvcc. apt's nvidia-cuda-toolkit installs CUDA 12.0 — mismatches this image's torch (cu13),
# confirmed live: "detected CUDA version (12.0) mismatches ... PyTorch (13.0)". Use the matching
# pip nvcc wheel instead so the version always tracks whatever torch/cuXY this base ships.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential python3-dev && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir nvidia-cuda-nvcc nvidia-nvvm nvidia-cuda-runtime nvidia-cuda-crt "nvidia-cuda-cccl-cu12==12.9.27" && \
    NVCC_BIN=$(find / -xdev -name nvcc -type f 2>/dev/null | head -1) && \
    NVCC_DIR=$(dirname $(dirname "$NVCC_BIN")) && \
    CCCL_DIR=$(dirname $(find / -xdev -path "*/cuda_cccl/include" -type d 2>/dev/null | head -1)) && \
    CUDART_DIR=$(dirname $(find / -xdev -name "libcudart.so*" -type f 2>/dev/null | head -1)) && \
    rm -rf /usr/local/cuda && mkdir -p /usr/local/cuda/lib && \
    cp -rf "$NVCC_DIR/." /usr/local/cuda/ && \
    cp -rf "$CCCL_DIR/include/." /usr/local/cuda/include/ && \
    cp -rf "$CUDART_DIR/." /usr/local/cuda/lib/ && \
    for f in /usr/local/cuda/lib/*.so.*; do \
        base=$(echo "$f" | sed -E 's/\.so\.[0-9].*/.so/'); \
        [ -e "$base" ] || ln -sf "$f" "$base"; \
    done && \
    /usr/local/cuda/bin/nvcc --version
ENV CUDA_HOME=/usr/local/cuda
ENV PATH="/usr/local/cuda/bin:${PATH}"

# no GPU present at image-build time, so torch's cpp_extension can't auto-detect an arch via
# nvidia-smi (empty arch_list -> "IndexError: list index out of range" in _get_cuda_arch_flags).
# Set explicitly: 8.0 = A100, 8.9 = L40/L40S — the tiers this workload targets (see model-download
# note below on VRAM). Add more archs here if deploying to other GPU tiers.
ENV TORCH_CUDA_ARCH_LIST="8.0;8.9"

# ponytail: build-from-source at image build time, ~5-10min layer, no upgrade path needed unless
# node updates its extension source — pin the git clone above if that ever breaks the build.
RUN cd /comfyui/custom_nodes/ComfyUI-Hunyuan3d-2-1/hy3dpaint/custom_rasterizer && \
    python setup.py install && \
    cd ../DifferentiableRenderer && \
    python setup.py install

# Hunyuan3D-2.1 checkpoints (shape DiT + VAE) — texturing stage needs 20GB+ VRAM at runtime,
# pick an A100/L40 runpod GPU tier, not 3090/4090 class, or texture pass OOMs (geometry-only still works)
RUN comfy model download \
    --url https://huggingface.co/tencent/Hunyuan3D-2.1/resolve/main/hunyuan3d-dit-v2-1/model.fp16.ckpt \
    --relative-path models/diffusion_models \
    --filename hunyuan3d-dit-v2-1.ckpt

RUN comfy model download \
    --url https://huggingface.co/tencent/Hunyuan3D-2.1/resolve/main/hunyuan3d-vae-v2-1/model.fp16.ckpt \
    --relative-path models/vae \
    --filename hunyuan3d-vae-v2-1.ckpt

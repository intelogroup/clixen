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
RUN pip install --no-cache-dir nvidia-cuda-nvcc && \
    NVCC_DIR=$(python -c "import nvidia.cuda_nvcc, os; print(os.path.dirname(nvidia.cuda_nvcc.__file__))") && \
    mkdir -p /usr/local/cuda/bin /usr/local/cuda/include && \
    ln -sf "$NVCC_DIR/bin/"* /usr/local/cuda/bin/ && \
    ln -sf "$NVCC_DIR/include/"* /usr/local/cuda/include/
ENV CUDA_HOME=/usr/local/cuda
ENV PATH="/usr/local/cuda/bin:${PATH}"

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

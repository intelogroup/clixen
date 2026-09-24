FROM runpod/worker-comfyui:5.8.6-base

# ComfyUI nodes — kijai CogVideoX (video) + flux support (image, proven on 5iv2v0hqgtrg4x)
RUN comfy-node-install comfyui-cogvideoxwrapper
# fallback for newer CLI alias
RUN comfy node install comfyui-cogvideoxwrapper || true

# CogVideoX1.5-5B (text-to-video), bf16 — fits 24GB
RUN comfy model download \
    --url https://huggingface.co/Kijai/CogVideoX-comfy/resolve/main/CogVideoX_1_5_5b_T2V_bf16.safetensors \
    --relative-path models/CogVideo \
    --filename CogVideoX_1_5_5b_T2V_bf16.safetensors

# VAE
RUN comfy model download \
    --url https://huggingface.co/Kijai/CogVideoX-comfy/resolve/main/cogvideox_vae_bf16.safetensors \
    --relative-path models/vae \
    --filename cogvideox_vae_bf16.safetensors

# T5 text encoder (shared across kijai's wrappers)
RUN comfy model download \
    --url https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn_scaled.safetensors \
    --relative-path models/clip \
    --filename t5xxl_fp8_e4m3fn_scaled.safetensors

# Flux image model (working model on live endpoint 5iv2v0hqgtrg4x — keeps image gen functional even before CogVideoX video tested)
RUN comfy model download \
    --url https://huggingface.co/Comfy-Org/flux1-dev/resolve/main/flux1-dev-fp8.safetensors \
    --relative-path models/checkpoints \
    --filename flux1-dev-fp8.safetensors

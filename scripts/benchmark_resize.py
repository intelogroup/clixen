#!/usr/bin/env python3
import os
import sys
import time
import base64
import subprocess
from pathlib import Path

try:
    import ollama
    from PIL import Image
except ImportError as e:
    print(f"Error: Missing libraries: {e}. Run pip install ollama pillow")
    sys.exit(1)

def capture_and_resize(output_path: str) -> bool:
    print(f"Capturing screenshot...")
    png_path = output_path + ".png"
    res = subprocess.run(["/usr/sbin/screencapture", "-x", png_path])
    if res.returncode != 0:
        print("Capture failed")
        return False
        
    try:
        img = Image.open(png_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        print(f"Original resolution: {img.size}")
        
        # Resize to max 800px
        max_size = 800
        w, h = img.size
        if w > max_size or h > max_size:
            if w > h:
                new_w = max_size
                new_h = int(h * (max_size / w))
            else:
                new_h = max_size
                new_w = int(w * (max_size / h))
            img = img.resize((new_w, new_h), resample=Image.Resampling.LANCZOS)
            
        print(f"Resized resolution: {img.size}")
        img.save(output_path, "JPEG", quality=70)
        os.remove(png_path)
        return True
    except Exception as e:
        print("Resizing failed:", e)
        return False

def run_model(model_name: str, image_path: str, prompt: str) -> dict:
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
        
    start_time = time.time()
    
    resp = ollama.chat(
        model=model_name,
        messages=[{
            "role": "user",
            "content": prompt,
            "images": [b64]
        }],
        keep_alive=-1,
        options={"temperature": 0.2, "num_predict": 512}
    )
    
    end_time = time.time()
    
    resp_dict = dict(resp) if hasattr(resp, "__iter__") else {}
    
    return {
        "wall_time": end_time - start_time,
        "prompt_eval_time": resp_dict.get("prompt_eval_duration", 0) / 1e9,
        "eval_time": resp_dict.get("eval_duration", 0) / 1e9,
        "eval_count": resp_dict.get("eval_count", 0),
        "response": resp.message.content or getattr(resp.message, "thinking", "")
    }

def main():
    image_path = "test_resized.jpg"
    if not capture_and_resize(image_path):
        sys.exit(1)
        
    try:
        prompt = "Describe what is on my screen in detail."
        model = "qwen3-vl:4b"
        
        print(f"Running qwen3-vl:4b on resized image...")
        res = run_model(model, image_path, prompt)
        
        print("\n" + "="*50)
        print("OPTIMIZATION BENCHMARK RESULT")
        print("="*50)
        print(f"Wall-clock Time:     {res['wall_time']:.2f} s  (was 52.71 s!)")
        print(f"Prompt Eval Time:    {res['prompt_eval_time']:.2f} s  (was 32.37 s!)")
        print(f"Generation Time:     {res['eval_time']:.2f} s")
        print(f"Output Tokens:       {res['eval_count']}")
        print(f"Speedup Factor:      {52.71 / res['wall_time']:.2f} faster!")
        print("-" * 50)
        print("Response Preview:")
        print(res["response"][:300] + "...")
        print("="*50)
        
    finally:
        if os.path.exists(image_path):
            os.remove(image_path)

if __name__ == "__main__":
    main()

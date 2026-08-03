#!/usr/bin/env python3
import os
import sys
import time
import base64
import subprocess
from pathlib import Path

try:
    import ollama
except ImportError:
    print("Error: 'ollama' library not found. Run pip install ollama")
    sys.exit(1)

def capture_screen(output_path: str) -> bool:
    print(f"Capturing screen to {output_path}...", end="", flush=True)
    cmd = ["/usr/sbin/screencapture", "-x", output_path]
    res = subprocess.run(cmd)
    if res.returncode == 0:
        print(" Success.")
        return True
    else:
        print(" Failed.")
        return False

def encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def run_model_benchmark(model_name: str, image_b64: str, prompt: str) -> dict:
    print(f"\nRunning benchmark for model: {model_name}...")
    start_time = time.time()
    
    metrics = {
        "model": model_name,
        "success": False,
        "error": None,
        "response": "",
        "total_wall_time_s": 0.0,
        "total_duration_s": 0.0,
        "load_duration_s": 0.0,
        "prompt_eval_duration_s": 0.0,
        "eval_duration_s": 0.0,
        "eval_count": 0,
        "tokens_per_sec": 0.0
    }
    
    try:
        # Call ollama.chat
        resp = ollama.chat(
            model=model_name,
            messages=[{
                "role": "user",
                "content": prompt,
                "images": [image_b64]
            }],
            options={
                "temperature": 0.2,
                "num_predict": 512
            }
        )
        
        end_time = time.time()
        metrics["success"] = True
        metrics["response"] = resp.message.content
        metrics["total_wall_time_s"] = end_time - start_time
        
        # Extract Ollama internal metrics if available (ollama returns nanoseconds)
        # We can safely use getattr or access dictionary keys
        resp_dict = dict(resp) if hasattr(resp, "__iter__") else {}
        
        if "total_duration" in resp_dict:
            metrics["total_duration_s"] = resp_dict["total_duration"] / 1e9
        if "load_duration" in resp_dict:
            metrics["load_duration_s"] = resp_dict["load_duration"] / 1e9
        if "prompt_eval_duration" in resp_dict:
            metrics["prompt_eval_duration_s"] = resp_dict["prompt_eval_duration"] / 1e9
        if "eval_duration" in resp_dict:
            metrics["eval_duration_s"] = resp_dict["eval_duration"] / 1e9
        if "eval_count" in resp_dict:
            metrics["eval_count"] = resp_dict["eval_count"]
            if metrics["eval_duration_s"] > 0:
                metrics["tokens_per_sec"] = metrics["eval_count"] / metrics["eval_duration_s"]
                
    except Exception as e:
        metrics["error"] = str(e)
        print(f"Error running model {model_name}: {e}")
        
    return metrics

def main():
    screenshot_path = "screenshot_bench.png"
    if not capture_screen(screenshot_path):
        print("Failed to capture screenshot. Exiting.")
        sys.exit(1)
        
    try:
        print("Encoding image to Base64...")
        image_b64 = encode_image(screenshot_path)
        
        prompt = "Describe the main items, windows, or applications visible on this screen. Be concise."
        
        # Benchmark both models
        results = []
        for model in ["qwen3-vl:4b", "qwen3-vl:4b"]:
            res = run_model_benchmark(model, image_b64, prompt)
            results.append(res)
            
        # Print summary report
        print("\n" + "="*60)
        print("BENCHMARK REPORT")
        print("="*60)
        
        for res in results:
            print(f"\nModel: {res['model']}")
            if not res["success"]:
                print(f"  Status: FAILED ({res['error']})")
                continue
                
            print(f"  Wall-clock Time:     {res['total_wall_time_s']:.2f} s")
            print(f"  Ollama Total Time:   {res['total_duration_s']:.2f} s")
            print(f"  Load Time:           {res['load_duration_s']:.2f} s")
            print(f"  Prompt Eval Time:    {res['prompt_eval_duration_s']:.2f} s")
            print(f"  Generation Time:     {res['eval_duration_s']:.2f} s")
            print(f"  Output Tokens:       {res['eval_count']}")
            print(f"  Tokens / Second:     {res['tokens_per_sec']:.2f}")
            print("-" * 30)
            print("Response:")
            print(res["response"].strip())
            print("-" * 30)
            
    finally:
        # Cleanup screenshot
        if os.path.exists(screenshot_path):
            os.remove(screenshot_path)
            print(f"Cleaned up {screenshot_path}")

if __name__ == "__main__":
    main()

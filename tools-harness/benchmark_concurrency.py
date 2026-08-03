import time
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
import ollama

MODEL = "gemma4:e2b"
PROMPT = "Explain the concept of quantum entanglement in one paragraph."

def get_vram_usage():
    try:
        # On macOS, ollama ps shows the memory usage of loaded models
        res = subprocess.run(["ollama", "ps"], capture_output=True, text=True)
        return res.stdout
    except Exception:
        return "N/A"

def single_call(call_id):
    start = time.time()
    try:
        # Use a fresh client for each thread to avoid connection pooling issues
        client = ollama.Client()
        response = client.chat(model=MODEL, messages=[{'role': 'user', 'content': PROMPT}])
        elapsed = time.time() - start
        return {"id": call_id, "elapsed": elapsed, "status": "success"}
    except Exception as e:
        print(f"Call {call_id} failed: {e}")
        return {"id": call_id, "elapsed": time.time() - start, "status": f"failed: {e}"}

def run_benchmark(concurrency):
    print(f"\n--- Testing Concurrency: {concurrency} ---")
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(single_call, i) for i in range(concurrency)]
        results = [f.result() for f in futures]
    
    total_time = time.time() - start_time
    vram = get_vram_usage()
    
    successes = [r for r in results if r["status"] == "success"]
    avg_lat = sum(r["elapsed"] for r in successes) / len(successes) if successes else 0
    
    print(f"Total Time: {total_time:.2f}s")
    print(f"Avg Latency: {avg_lat:.2f}s")
    print(f"Successes: {len(successes)}/{concurrency}")
    print(f"Ollama State:\n{vram}")
    
    return {
        "concurrency": concurrency,
        "total_time": total_time,
        "avg_latency": avg_lat,
        "success_rate": len(successes) / concurrency
    }

if __name__ == "__main__":
    results = []
    # Test 1, 2, 4, 6 concurrent calls
    # 6 calls of Gemma 4 Q4_0 should be ~15-18GB, pushing near the 24GB limit if other models are loaded.
    for c in [1, 2, 4, 6]:
        res = run_benchmark(c)
        results.append(res)
        if res["success_rate"] < 0.5:
            print("Significant failure rate detected. Stopping.")
            break
        time.sleep(1) # Cooldown
    
    print("\n--- Final Results Summary ---")
    print(json.dumps(results, indent=2))

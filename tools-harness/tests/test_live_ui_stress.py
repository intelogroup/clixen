import asyncio
import os
import sys
import time
import httpx
from dotenv import load_dotenv

# Load env variables from tools-harness/.env
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv_path = os.path.join(parent_dir, ".env")
load_dotenv(dotenv_path)

# Add parent dir to path so we can import if needed
sys.path.insert(0, parent_dir)

DEV_EMAIL = os.environ.get("DEV_EMAIL", "t@t.com")
DEV_PASSWORD = os.environ.get("DEV_PASSWORD", "G4LDemo12356$#*!")
BASE_URL = "http://localhost:9234"
MODEL = os.environ.get("STRESS_MODEL", "gemma4:12b-mlx")  # Target the primary loaded model
PROMPT = "Explain the difference between deep learning and classical machine learning in 3 sentences."

async def run_single_stream(client_id: int):
    chat_id = f"stress-test-client-{client_id}-{int(time.time())}"
    url = f"{BASE_URL}/chat/stream"
    params = {
        "message": PROMPT,
        "chat_id": chat_id,
        "model": MODEL
    }
    
    start_time = time.time()
    tokens_received = 0
    done_received = False
    error_received = None
    
    print(f"[Client {client_id}] Starting stream for chat_id={chat_id}")
    
    try:
        # Use httpx to connect and parse stream
        headers = {
            "Host": "localhost",
            "User-Agent": "StressTestClient/1.0"
        }
        
        async with httpx.AsyncClient(timeout=httpx.Timeout(360.0, connect=5.0)) as client:
            async with client.stream("GET", url, params=params, headers=headers) as response:
                if response.status_code != 200:
                    error_received = f"HTTP {response.status_code}: {await response.aread()}"
                    print(f"[Client {client_id}] Connection failed: {error_received}")
                    return client_id, 0, time.time() - start_time, False, error_received
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        import json
                        try:
                            data = json.loads(line[6:])
                            if "token" in data:
                                tokens_received += 1
                            elif data.get("done") or data.get("type") == "done":
                                done_received = True
                                break
                            elif "error" in data:
                                error_received = data["error"]
                                break
                        except json.JSONDecodeError:
                            pass
                            
    except Exception as e:
        error_received = str(e)
        print(f"[Client {client_id}] Stream exception: {e}")
        
    elapsed = time.time() - start_time
    print(f"[Client {client_id}] Finished in {elapsed:.2f}s | Tokens: {tokens_received} | Done: {done_received} | Error: {error_received}")
    return client_id, tokens_received, elapsed, done_received, error_received

async def run_stress_test(concurrency: int):
    print(f"=== Starting Live Concurrency Stress Test ===")
    print(f"Concurrency: {concurrency}")
    print(f"Target Model: {MODEL}")
    print(f"Server URL: {BASE_URL}")
    print(f"Authed as: {DEV_EMAIL}")
    print("=" * 45)
    
    tasks = [run_single_stream(i) for i in range(concurrency)]
    start = time.time()
    results = await asyncio.gather(*tasks)
    total_duration = time.time() - start
    
    print("\n" + "=" * 45)
    print("=== Stress Test Results ===")
    print("=" * 45)
    
    total_tokens = 0
    success_count = 0
    for cid, tokens, elapsed, done, err in results:
        status = "SUCCESS" if (done and not err) else "FAILED"
        if status == "SUCCESS":
            success_count += 1
            total_tokens += tokens
        print(f"Client {cid}: {status} | Duration: {elapsed:.2f}s | Tokens: {tokens} | Err: {err}")
        
    print("-" * 45)
    print(f"Total Test Duration: {total_duration:.2f}s")
    print(f"Overall Success Rate: {success_count}/{concurrency} ({success_count/concurrency*100:.1f}%)")
    if success_count > 0:
        avg_throughput = total_tokens / total_duration
        print(f"Total Tokens Streamed: {total_tokens}")
        print(f"Average Throughput: {avg_throughput:.2f} tokens/sec")
    print("=" * 45)

if __name__ == "__main__":
    concurrency = 5
    if len(sys.argv) > 1:
        try:
            concurrency = int(sys.argv[1])
        except ValueError:
            pass
            
    os.environ["DEV_EMAIL"] = DEV_EMAIL
    os.environ["DEV_PASSWORD"] = DEV_PASSWORD
    
    asyncio.run(run_stress_test(concurrency))

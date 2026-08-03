#!/usr/bin/env python3
"""
Hardware Profiler for Clixen.
Detects CPU, RAM, and GPU/VRAM to recommend optimal local model configurations.
"""

import os
import sys
import platform
import subprocess
from pathlib import Path

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_RESET = "\033[0m"

def get_cpu_info() -> str:
    system = platform.system()
    if system == "Darwin":
        try:
            cores = subprocess.check_output(["sysctl", "-n", "hw.ncpu"]).decode().strip()
            brand = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"]).decode().strip()
            return f"{brand} ({cores} Cores)"
        except Exception:
            return "Apple Silicon / Intel (Mac)"
    elif system == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                lines = f.readlines()
            for line in lines:
                if "model name" in line:
                    brand = line.split(":", 1)[1].strip()
                    cores = str(os.cpu_count())
                    return f"{brand} ({cores} Cores)"
        except Exception:
            pass
    return f"{platform.processor() or 'Unknown Processor'} ({os.cpu_count()} Cores)"

def get_ram_gb() -> float:
    system = platform.system()
    if system == "Darwin":
        try:
            mem = subprocess.check_output(["sysctl", "-n", "hw.memsize"]).decode().strip()
            return float(mem) / (1024 ** 3)
        except Exception:
            return 8.0
    elif system == "Linux":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if "MemTotal" in line:
                        mem = line.split()[1]
                        return float(mem) / (1024 ** 2)
        except Exception:
            pass
    return 8.0

def get_gpu_vram_gb() -> tuple[str, float]:
    """Returns (GPU Name, VRAM in GB)."""
    system = platform.system()
    # On macOS, Apple Silicon utilizes Unified Memory
    if system == "Darwin":
        try:
            is_apple_silicon = subprocess.run(["sysctl", "hw.optional.arm64"], capture_output=True, text=True)
            if "hw.optional.arm64: 1" in is_apple_silicon.stdout:
                ram = get_ram_gb()
                # Unified memory serves as both system RAM and VRAM
                return "Apple Silicon (Unified Memory)", ram
        except Exception:
            pass

    # Check for NVIDIA GPU
    try:
        nvidia_smi = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3
        )
        if nvidia_smi.returncode == 0:
            lines = nvidia_smi.stdout.strip().split("\n")
            if lines:
                parts = lines[0].split(",")
                name = parts[0].strip()
                vram = float(parts[1].strip()) / 1024.0
                return f"NVIDIA {name}", vram
    except Exception:
        pass

    # Check for AMD GPU
    try:
        rocm_smi = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram"],
            capture_output=True, text=True, timeout=3
        )
        if rocm_smi.returncode == 0:
            # rocm-smi output parsing is complex, try a simple regex or fallback
            return "AMD Radeon (ROCm)", 8.0
    except Exception:
        pass

    return "CPU Only", 0.0

def get_recommendation(ram: float, gpu_name: str, vram: float) -> dict:
    is_mac = platform.system() == "Darwin"
    is_unified = "Unified Memory" in gpu_name

    rec = {}
    if is_mac and is_unified:
        rec["type"] = "Apple Silicon Unified Memory"
        if ram >= 32.0:
            rec["tier"] = "Premium"
            rec["models"] = ["gemma4:12b-mlx", "mistral-nemo", "qwen3.5:9b"]
            rec["primary"] = "gemma4:12b-mlx"
            rec["ctx"] = 16384
        elif ram >= 24.0:
            rec["tier"] = "Optimal (Standard)"
            rec["models"] = ["gemma4:12b-mlx", "mistral-nemo", "qwen3:4b"]
            rec["primary"] = "gemma4:12b-mlx"
            rec["ctx"] = 16384
        elif ram >= 16.0:
            rec["tier"] = "Medium"
            rec["models"] = ["gemma4:e2b", "mistral-nemo", "qwen3.5:4b"]
            rec["primary"] = "gemma4:e2b"
            rec["ctx"] = 8192
        else:
            rec["tier"] = "Light"
            rec["models"] = ["gemma4:e2b", "qwen3.5:4b"]
            rec["primary"] = "gemma4:e2b"
            rec["ctx"] = 4096
    else:
        # Dedicated GPU PC/Linux
        rec["type"] = f"Dedicated GPU ({gpu_name})"
        if vram >= 16.0:
            rec["tier"] = "Premium"
            rec["models"] = ["gemma4:12b-mlx", "mistral-nemo", "qwen3.5:9b"]
            rec["primary"] = "gemma4:12b-mlx"
            rec["ctx"] = 16384
        elif vram >= 8.0:
            rec["tier"] = "Optimal (Standard)"
            rec["models"] = ["gemma4:e2b", "mistral-nemo", "qwen3.5:4b"]
            rec["primary"] = "gemma4:e2b"
            rec["ctx"] = 8192
        else:
            rec["tier"] = "CPU / Light GPU"
            rec["models"] = ["qwen3.5:4b", "gemma4:e2b"]
            rec["primary"] = "qwen3.5:4b"
            rec["ctx"] = 4096

    return rec

def apply_recommendation(primary_model: str) -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        print(f"{_RED}Error: .env file not found at {env_path}{_RESET}")
        return

    content = env_path.read_text(encoding="utf-8")
    pattern = r"OLLAMA_DEFAULT_MODEL=.*"
    replacement = f"OLLAMA_DEFAULT_MODEL={primary_model}"

    if re.search(pattern, content):
        new_content = re.sub(pattern, replacement, content)
    else:
        new_content = content + f"\nOLLAMA_DEFAULT_MODEL={primary_model}\n"

    env_path.write_text(new_content, encoding="utf-8")
    print(f"{_GREEN}✓ Successfully updated OLLAMA_DEFAULT_MODEL to {primary_model} in .env{_RESET}")

def main() -> None:
    print(f"\n{_BLUE}=== Clixen Hardware Profiler ==={_RESET}\n")

    print(f"  System: {platform.system()} {platform.release()} ({platform.machine()})")
    cpu = get_cpu_info()
    print(f"  CPU: {cpu}")
    ram = get_ram_gb()
    print(f"  RAM: {ram:.1f} GB")

    gpu_name, vram = get_gpu_vram_gb()
    print(f"  GPU/VRAM Source: {gpu_name} (Allocated VRAM: {vram:.1f} GB)")

    rec = get_recommendation(ram, gpu_name, vram)

    print(f"\n{_BLUE}=== Recommended Settings ==={_RESET}\n")
    print(f"  Tier: {rec['tier']}")
    print(f"  Primary Default Model: {rec['primary']}")
    print(f"  Warm Pool Suggestion: {', '.join(rec['models'])}")
    print(f"  Suggested Context Window (num_ctx): {rec['ctx']}")

    if len(sys.argv) > 1 and sys.argv[1] == "--apply":
        apply_recommendation(rec["primary"])
    else:
        print(f"\nTo apply the recommended default model ({rec['primary']}) to your .env file, run:")
        print(f"  {_YELLOW}python scripts/profile_hardware.py --apply{_RESET}\n")

if __name__ == "__main__":
    import re
    main()

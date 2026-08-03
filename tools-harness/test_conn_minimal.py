import urllib.request
import sys

try:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags") as response:
        print(response.status)
        print(response.read().decode())
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)

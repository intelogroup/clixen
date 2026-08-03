
import http.client

def test_ollama():
    try:
        conn = http.client.HTTPConnection("127.0.0.1", 11434, timeout=5)
        conn.request("GET", "/api/tags")
        resp = conn.getresponse()
        data = resp.read()
        print(f"Status: {resp.status}")
        print(f"Data: {data.decode()[:100]}...")
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False

if __name__ == "__main__":
    test_ollama()

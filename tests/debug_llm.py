"""Debug :8589 LLM service"""
import urllib.request, json

# 1. Test models endpoint
print("=== /v1/models ===")
resp = urllib.request.urlopen("http://127.0.0.1:8589/v1/models", timeout=5)
data = json.loads(resp.read())
print(json.dumps(data, indent=2))

# 2. Test simple chat completion
print("\n=== Simple chat ===")
payload = json.dumps({
    "model": "Qwen3-8B-q4",
    "messages": [
        {"role": "system", "content": "Say hello in Chinese."},
        {"role": "user", "content": "Hi"}
    ],
    "temperature": 0.1,
    "max_tokens": 50
}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8589/v1/chat/completions",
    data=payload,
    headers={"Content-Type": "application/json"},
)
try:
    resp = urllib.request.urlopen(req, timeout=20)
    data = json.loads(resp.read())
    content = data["choices"][0]["message"]["content"]
    print(f"Content: {repr(content[:200])}")
    print(f"Finish reason: {data['choices'][0].get('finish_reason')}")
except Exception as e:
    print(f"Chat completions FAIL: {e}")
    # Try completions endpoint
    print("\n=== Trying /v1/completions ===")
    payload2 = json.dumps({
        "model": "Qwen3-8B-q4",
        "prompt": "Say hello",
        "max_tokens": 20
    }).encode()
    req2 = urllib.request.Request(
        "http://127.0.0.1:8589/v1/completions",
        data=payload2,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp2 = urllib.request.urlopen(req2, timeout=20)
        data2 = json.loads(resp2.read())
        print(f"Content: {repr(data2['choices'][0]['text'][:200])}")
    except Exception as e2:
        print(f"Completions also FAIL: {e2}")

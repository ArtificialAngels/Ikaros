"""Debug LLM extract response"""
import urllib.request, json

payload = json.dumps({
    "model": "Qwen3-8B-q4",
    "messages": [
        {"role": "system", "content": "只输出JSON数组。从对话中提取事实。输出[{\"content\":\"...\",\"type\":\"fact\"}]"},
        {"role": "user", "content": "user: 我用Python\nassistant: 好的"}
    ],
    "temperature": 0.1,
    "max_tokens": 200
}).encode()

req = urllib.request.Request(
    "http://127.0.0.1:8589/v1/chat/completions",
    data=payload,
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=15)
data = json.loads(resp.read())
content = data["choices"][0]["message"]["content"]
print("=== RAW LLM RESPONSE ===")
print(repr(content))
print("=== TRY JSON PARSE ===")
try:
    parsed = json.loads(content.strip())
    print(f"OK: {parsed}")
except Exception as e:
    print(f"FAIL: {e}")
    # Try extracting from markdown code block
    if "```json" in content:
        j = content.split("```json")[1].split("```")[0].strip()
        parsed = json.loads(j)
        print(f"From codeblock OK: {parsed}")
    elif "```" in content:
        j = content.split("```")[1].split("```")[0].strip()
        parsed = json.loads(j)
        print(f"From codeblock OK: {parsed}")

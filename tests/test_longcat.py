"""
LongCat API 连通性测试
用法: python test_longcat.py
"""
import requests
import json

API_URL = "https://api.longcat.chat/anthropic/v1/messages"
API_KEY = "ak_2R67S24Qy8lz9zc9vt1799Dc6Vv5h"
MODEL = "LongCat-Flash-Lite"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
    "anthropic-version": "2023-06-01",
}

payload = {
    "model": MODEL,
    "max_tokens": 100,
    "messages": [{"role": "user", "content": "回复「你好」两个字"}],
}

print(f"请求 URL: {API_URL}")
print(f"模型: {MODEL}")
print(f"请求体: {json.dumps(payload, ensure_ascii=False)}")
print()

try:
    resp = requests.post(API_URL, headers=headers, json=payload, timeout=30)
    print(f"HTTP 状态码: {resp.status_code}")
    print(f"响应头: {dict(resp.headers)}")
    print()
    print(f"响应体: {resp.text}")
    print()

    if resp.status_code == 200:
        data = resp.json()
        print("✅ 连接成功！")
        print(f"模型回复: {data['content'][0]['text']}")
    elif resp.status_code == 401:
        print("❌ 认证失败 — 请检查 API Key")
    elif resp.status_code == 400:
        print("❌ 请求参数错误 — 请检查模型名或消息格式")
    elif resp.status_code == 500:
        print("❌ 服务端错误 — 模型可能暂时不可用，或需要联系 LongCat 管理员")
    else:
        print(f"❌ 未知错误")

except requests.exceptions.ConnectionError:
    print("❌ 网络连接失败 — 无法访问 API 地址")
except requests.exceptions.Timeout:
    print("❌ 请求超时")
except Exception as e:
    print(f"❌ 异常: {e}")

"""Patch system prompt to force city default."""
path = 'app/core/route_agent.py'
with open(path) as f:
    content = f.read()

old = '_SYSTEM_PROMPT = """你是一个本地路线规划助手「出发酱」。用工具逐步规划路线，最后给出简洁预览。\n\n## 工作流程（严格按序，不可跳过）'

new = '_SYSTEM_PROMPT = """你是一个本地路线规划助手「出发酱」。用工具逐步规划路线，最后给出简洁预览。\n\n**⚠️ 最优先规则：只要用户表达了出行/吃喝/游玩/逛逛/去哪/推荐/好无聊等意图，无论是否有城市名，必须立即默认城市=西安并调用工具规划路线。严禁反问或输出纯文字建议。仅"你好"/"谢谢"/"你能做什么"等纯社交语句可以不用工具。**\n\n## 工作流程（严格按序，不可跳过）'

if old in content:
    content = content.replace(old, new)
    with open(path, 'w') as f:
        f.write(content)
    print('OK - replaced')
else:
    print('NOT FOUND')
    idx = content.find('_SYSTEM_PROMPT')
    print(repr(content[idx:idx+120]))

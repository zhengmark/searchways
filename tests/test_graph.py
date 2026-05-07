import sys, time, re
sys.path.insert(0, ".")
from app.shared.utils import AgentSession
from app.core.orchestrator import run_multi_agent as run_agent

print("=" * 60)
print("测试：西安铁塔寺路 → 钟楼，逛吃 3 站，不吃辣")
print("=" * 60)

session = AgentSession()
session.default_city = "西安"
start = time.time()
result, session = run_agent(
    "我从西安铁塔寺路出发，想去钟楼，中间逛吃 3 个地方，不能吃辣",
    session,
)
elapsed = time.time() - start

print(f"\n耗时: {elapsed:.1f}秒")
print(f"起点: {session.start_name!r}")
print(f"终点: {session.dest_name!r}")
print(f"选定站点: {session.stop_names}")

if session.path_result:
    pr = session.path_result
    print(f"\n路径规划结果:")
    print(f"  节点序列: {pr['node_ids']}")
    print(f"  总耗时: {pr['total_duration_min']}分钟")
    print(f"  总距离: {pr['total_distance']}米")
    print(f"  路段:")
    for s in pr["segments"]:
        print(f"    {s['from']} → {s['to']}")
        print(f"      {s['transport']} {s['distance']}m ({s['duration']//60}分钟)")

# Check mermaid for transport icons
if "```mermaid" in result:
    mm = re.search(r"```mermaid\n(.+?)\n```", result, re.DOTALL)
    if mm:
        print(f"\nMermaid 交通方式标记:")
        for line in mm.group(1).split("\n"):
            if "-->" in line or "==>" in line or "-.-" in line:
                print(f"  {line.strip()}")

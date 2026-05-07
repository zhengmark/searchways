"""多用户画像测试：模拟不同类型用户，收集体验报告"""
import sys, time, json, traceback
sys.path.insert(0, ".")
from app.shared.utils import AgentSession
from app.core.orchestrator import run_multi_agent as run_agent

SCENARIOS = [
    {
        "id": "A",
        "profile": "无终点漫游者",
        "input": "从西安钟楼出发，周围逛吃3个有特色的地方",
        "city": "西安",
        "checks": ["path_result not None", "len(stop_names) == 3",
                    "has mermaid", "total_duration_min > 0",
                    "POIs not all clustered at start"],
    },
    {
        "id": "B",
        "profile": "跨区远距离出行者",
        "input": "从北京西单出发，去颐和园，路上找2个喝咖啡或者吃甜品的地方",
        "city": "北京",
        "checks": ["path_result not None", "len(stop_names) >= 2",
                    "has mermaid", "segments cover full route",
                    "transport not all walking"],
    },
    {
        "id": "C",
        "profile": "美食挑剔者",
        "input": "从西安铁塔寺路去大雁塔，中间3个地方吃清真美食，不吃猪肉",
        "city": "西安",
        "checks": ["path_result not None", "len(stop_names) >= 2",
                    "has mermaid", "stops geographically diverse",
                    "total_distance reasonable"],
    },
]

def run_scenario(sc):
    print(f"\n{'='*60}")
    print(f"用户画像: {sc['profile']}")
    print(f"需求: {sc['input']}")
    print(f"{'='*60}")

    session = AgentSession()
    session.default_city = sc["city"]
    start = time.time()

    try:
        result, session = run_agent(sc["input"], session)
    except Exception as e:
        print(f"❌ CRASH: {e}")
        traceback.print_exc()
        return {"id": sc["id"], "status": "CRASH", "error": str(e)}

    elapsed = time.time() - start

    # Collect metrics
    findings = {
        "id": sc["id"],
        "profile": sc["profile"],
        "status": "OK",
        "elapsed": round(elapsed, 1),
        "start_name": session.start_name,
        "dest_name": session.dest_name,
        "stop_names": session.stop_names,
        "n_stops": len(session.stop_names),
        "has_path": session.path_result is not None,
        "has_mermaid": "```mermaid" in result,
        "total_duration": session.path_result["total_duration_min"] if session.path_result else None,
        "total_distance": session.path_result["total_distance"] if session.path_result else None,
        "n_segments": len(session.path_result["segments"]) if session.path_result else 0,
        "segments": [],
        "issues": [],
    }

    if session.path_result:
        pr = session.path_result
        for s in pr["segments"]:
            findings["segments"].append({
                "from": s["from"], "to": s["to"],
                "dist": s["distance"], "dur": s["duration"],
                "transport": s["transport"],
            })

        # Issue checks
        # Check for clustered POIs (all < 500m cumulative from start)
        if len(pr["segments"]) >= 2:
            first_half_dists = [s["distance"] for s in pr["segments"][:len(pr["segments"])//2]]
            second_half_dists = [s["distance"] for s in pr["segments"][len(pr["segments"])//2:]]
            if sum(first_half_dists) < 100 and sum(second_half_dists) > 2000:
                findings["issues"].append("POIs clustered at start, then big jump")

        # Check for "0分钟" in any segment
        for s in pr["segments"]:
            if s["duration"] < 60:
                findings["issues"].append(f"Short segment: {s['from']}->{s['to']} is {s['duration']}s ({round(s['duration']/60)}min)")

    # Print summary
    print(f"\n  耗时: {elapsed:.1f}s")
    print(f"  起点: {session.start_name!r}")
    print(f"  终点: {session.dest_name!r}")
    print(f"  站点({findings['n_stops']}): {session.stop_names}")
    if session.path_result:
        pr = session.path_result
        print(f"  总耗时: {pr['total_duration_min']}min  总距离: {pr['total_distance']}m")
        for s in pr["segments"]:
            print(f"    {s['from']} → {s['to']}: {s['transport']} {s['distance']}m ({round(s['duration']/60)}min)")
    if findings["issues"]:
        print(f"  ⚠️  问题: {findings['issues']}")

    return findings

if __name__ == "__main__":
    all_findings = []
    for sc in SCENARIOS:
        f = run_scenario(sc)
        all_findings.append(f)

    print(f"\n{'='*60}")
    print("汇总报告")
    print(f"{'='*60}")
    for f in all_findings:
        status_icon = "✅" if f["status"] == "OK" and not f["issues"] else "⚠️" if f["issues"] else "❌"
        print(f"{status_icon} {f['profile']}: {f['status']} ({f['elapsed']}s) {f['n_stops']} stops")
        for issue in f.get("issues", []):
            print(f"   → {issue}")

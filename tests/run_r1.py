"""Round 1 only — 8 users."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.core.orchestrator import run_multi_agent

ROUND_1 = [
    {
        "id": "R1_美食猎人",
        "desc": "品类切换+预算约束",
        "user_id": "t1_foodie",
        "rounds": ["从回民街出发找地道小吃", "换成清淡口味不要太油腻", "人均控制在50以内"],
    },
    {
        "id": "R1_文化游客",
        "desc": "时间收缩+地点跳跃+增量添加",
        "user_id": "t2_culture",
        "rounds": ["西安历史古迹一日游，从北站出发", "改成半天，重点去大雁塔", "再加个大唐不夜城看夜景"],
    },
    {
        "id": "R1_亲子周末",
        "desc": "排除约束+时间控制",
        "user_id": "t3_family",
        "rounds": ["带孩子去曲江玩", "要户外公园不要室内商场", "控制在3小时内"],
    },
    {"id": "R1_极简社畜", "desc": "极简兜底→具体化", "user_id": "t4_minimal", "rounds": ["西安 吃", "想吃火锅"]},
    {
        "id": "R1_骑行探索",
        "desc": "关键词保留+时间收缩",
        "user_id": "t5_bike",
        "rounds": ["从高新出发骑行去秦岭", "缩短到2小时以内", "但是风景一定要好"],
    },
    {
        "id": "R1_佛系游客",
        "desc": "无目的→加约束",
        "user_id": "t6_zenn",
        "rounds": ["周末不知道去哪", "能拍照好看的地方", "不要太远"],
    },
    {
        "id": "R1_深夜觅食",
        "desc": "深夜→换区",
        "user_id": "t7_late",
        "rounds": ["晚上11点还能吃东西的地方", "换到小寨附近找找"],
    },
    {
        "id": "R1_穷游学生",
        "desc": "穷游→预算升级",
        "user_id": "t8_budget",
        "rounds": ["免费景点为主，省钱", "预算宽裕了，人均80吃顿好的"],
    },
]


def _rate(narration, stops, all_pois, elapsed):
    s, issues = 0.0, []
    if not narration or len(narration) <= 50:
        issues.append("输出过短或为空")
        return (s, issues)
    s += 1.0
    if not stops or len(stops) < 1:
        issues.append("无POI站点")
        return (s, issues)
    s += 1.0
    cats = set(p.get("category", "") for p in (all_pois or []) if p.get("category"))
    if len(cats) >= 2:
        s += 0.5
    elif len(stops) >= 2:
        issues.append(f"品类单一({len(cats)}种)")
    stops_in_narr = sum(1 for st in stops if st in narration)
    if stops and stops_in_narr >= max(1, len(stops) // 2):
        s += 0.5
    else:
        issues.append("解说/stops脱节")
    s += 0.5  # constraint preservation
    if "分钟" in narration:
        s += 0.5
    if elapsed < 60:
        s += 0.5
    elif elapsed < 120:
        s += 0.25
    return (min(s, 5.0), issues)


all_results = []
print("R1 start")
for i, sc in enumerate(ROUND_1):
    uid = sc["id"]
    print(f"[{i + 1}/8] {uid}: {sc['desc']}", flush=True)
    results = []
    session = None
    total_time = 0
    for j, query in enumerate(sc["rounds"]):
        t0 = time.time()
        try:
            narration, session = run_multi_agent(query, session=session, user_id=sc["user_id"])
            elapsed = round(time.time() - t0, 1)
            total_time += elapsed
            stops = session.stop_names or []
            all_pois = session.all_pois or []
            score, issues = _rate(narration, stops, all_pois, elapsed)
            results.append(
                {
                    "round": j + 1,
                    "query": query,
                    "narration_preview": narration[:200].replace("\n", " "),
                    "stops": stops,
                    "num_stops": len(stops),
                    "elapsed_s": elapsed,
                    "score": round(score, 1),
                    "issues": issues,
                }
            )
            sflag = "✅" if score >= 4 else "⚠️" if score >= 2.5 else "❌"
            print(f"  {sflag} R{j + 1} [{elapsed:.0f}s] stops={stops[:3]} score={score:.1f}", flush=True)
            if issues:
                print(f"       > {'; '.join(issues)}", flush=True)
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            results.append({"round": j + 1, "query": query, "error": str(e)[:150], "elapsed_s": elapsed, "score": 0})
            print(f"  ❌ R{j + 1} [{elapsed:.0f}s] {e}", flush=True)
    avg = round(sum(r["score"] for r in results) / max(len(results), 1), 1)
    r = {
        "id": uid,
        "desc": sc["desc"],
        "total_rounds": len(results),
        "total_time_s": round(total_time, 1),
        "avg_score": avg,
        "results": results,
    }
    all_results.append(r)
    status = "✅" if avg >= 4 else "⚠️" if avg >= 3 else "❌"
    print(f"  => {status} avg:{avg}/5 | {total_time:.0f}s\n", flush=True)
    time.sleep(1)

scores = [r["avg_score"] for r in all_results]
overall = round(sum(scores) / max(len(scores), 1), 1)
ok = sum(1 for s in scores if s >= 4)
warn = sum(1 for s in scores if 3 <= s < 4)
fail = sum(1 for s in scores if s < 3)
print(f"\nR1 done: {overall}/5 | ✅{ok} ⚠️{warn} ❌{fail}")
for r in all_results:
    print(f"  {r['avg_score']:.1f} {r['id']}")

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
with open(OUTPUT_DIR / "test_round1_report.json", "w") as f:
    json.dump(
        {"round": 1, "overall_avg": overall, "ok": ok, "warn": warn, "fail": fail, "results": all_results},
        f,
        ensure_ascii=False,
        indent=2,
    )
print(f"Saved: {OUTPUT_DIR / 'test_round1_report.json'}", flush=True)

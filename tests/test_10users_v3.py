"""第三轮 10 用户测试 — 验证约束保留 + 品类匹配 + 解说一致性."""

import json
import sys
import time

sys.path.insert(0, ".")
from app.core.orchestrator import run_multi_agent

TESTS = [
    {
        "id": "R1_简餐探索",
        "desc": "极简→加约束：验证 P0-3 兜底+约束保留",
        "user_id": "r1_simple",
        "rounds": [
            "西安 吃",
            "想吃面食，不要太贵的",
        ],
    },
    {
        "id": "R2_辣转淡",
        "desc": "前后矛盾：验证口味转变+品类匹配",
        "user_id": "r2_spicy2mild",
        "rounds": [
            "西安钟楼附近吃川菜火锅",
            "不想吃辣的了，换清淡类的中餐",
        ],
    },
    {
        "id": "R3_风景骑变逛",
        "desc": "骑行转漫步：验证关键词保留vs推翻",
        "user_id": "r3_bike2walk",
        "rounds": [
            "西安曲江出发骑行半天，风景好的路线",
            "太累了骑不动，改成散步吧但风景还是要好",
        ],
    },
    {
        "id": "R4_带娃跳转",
        "desc": "亲子→户外→预算：验证约束累积",
        "user_id": "r4_kid_hop",
        "rounds": [
            "西安周末带3岁女儿出去玩",
            "要户外公园，不要室内商场",
            "改成半天，控制在人均50以内",
        ],
    },
    {
        "id": "R5_文化跳小众",
        "desc": "文化→小众：验证解说一致性",
        "user_id": "r5_culture",
        "rounds": [
            "西安文化古迹一日游，从北站出发",
            "加两个小众的、游客不知道的冷门景点",
        ],
    },
    {
        "id": "R6_无终点漫游",
        "desc": "无终点→加拍照：验证无终点模式",
        "user_id": "r6_noterminus",
        "rounds": [
            "从丈八六路地铁站出发出去逛逛",
            "加个能拍照打卡的网红地点",
        ],
    },
    {
        "id": "R7_深夜转区",
        "desc": "深夜宵夜→换区：验证地点跳跃",
        "user_id": "r7_latenight",
        "rounds": [
            "西安晚上11点还能吃到东西的地方",
            "换到小寨附近找找",
        ],
    },
    {
        "id": "R8_穷游升级",
        "desc": "穷游→中等：验证预算变更+约束保留",
        "user_id": "r8_budget",
        "rounds": [
            "西安学生党穷游一日，免费景点为主",
            "预算宽裕点了，提高到人均80，吃顿好的",
        ],
    },
    {
        "id": "R9_咖啡书店",
        "desc": "模糊→具体：验证LLM理解+品类准确",
        "user_id": "r9_vague2specific",
        "rounds": [
            "周末下午想找个安静的地方坐坐",
            "最好是独立书店或精品咖啡馆那种",
        ],
    },
    {
        "id": "R10_自相矛盾",
        "desc": "前后不一致：验证LLM冲突检测",
        "user_id": "r10_conflict",
        "rounds": [
            "西安最便宜的街头小吃",
            "改成米其林水准的高档餐厅",
        ],
    },
]


def run_one_test(test):
    uid = test["id"]
    user_id = test["user_id"]
    results = []
    session = None
    total_time = 0
    prev_keywords = None

    for i, query in enumerate(test["rounds"]):
        t0 = time.time()
        try:
            narration, session = run_multi_agent(query, session=session, user_id=user_id)
            elapsed = round(time.time() - t0, 1)
            total_time += elapsed
            stops = session.stop_names or []

            # 检测约束保留
            curr_kw = getattr(session, "keywords", None)
            kw_preserved = (
                prev_keywords is None
                or curr_kw is None
                or any(
                    k in str(curr_kw).lower()
                    for k in (prev_keywords if isinstance(prev_keywords, list) else [prev_keywords])
                )
            )

            # 检测解说一致性
            stops_in_narr = [s for s in stops if s in narration]
            narr_ok = len(stops_in_narr) >= max(1, len(stops) // 2) if stops else True

            # 检测品类(粗略)
            cats = set()
            for p in session.all_pois or []:
                c = p.get("category", "")
                if c:
                    cats.add(c)
            diverse = len(cats) >= 2

            score, issues = _rate(uid, query, narration, stops, session, elapsed, kw_preserved, narr_ok, diverse)

            results.append(
                {
                    "round": i + 1,
                    "query": query,
                    "narration_preview": narration[:180].replace("\n", " "),
                    "stops": stops,
                    "num_stops": len(stops),
                    "city": session.city or "unknown",
                    "elapsed_s": elapsed,
                    "score": score,
                    "checks": {"kw_preserved": kw_preserved, "narr_ok": narr_ok, "diverse": diverse},
                    "issues": issues,
                }
            )
            prev_keywords = curr_kw

            status = "✅" if score >= 4 else "⚠️" if score >= 2.5 else "❌"
            print(f"  {status} R{i + 1} {elapsed}s stops={stops[:3]} score={score}/5")
            if issues:
                print(f"       issues: {'; '.join(issues)}")
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            results.append(
                {
                    "round": i + 1,
                    "query": query,
                    "error": str(e)[:100],
                    "elapsed_s": elapsed,
                    "score": 0,
                }
            )
            print(f"  ❌ R{i + 1} {elapsed}s {e}")

    return {
        "id": uid,
        "desc": test["desc"],
        "total_rounds": len(results),
        "total_time_s": total_time,
        "avg_score": round(sum(r["score"] for r in results) / max(len(results), 1), 1),
        "results": results,
    }


def _rate(uid, query, narration, stops, session, elapsed, kw_preserved, narr_ok, diverse):
    s = 0.0
    issues = []

    if narration and len(narration) > 50:
        s += 1.0
    else:
        issues.append("输出过短")
        return (s, issues)

    if stops and len(stops) >= 1:
        s += 1.0
    else:
        issues.append("无POI")
        return (s, issues)

    # 约束保留
    if kw_preserved:
        s += 0.5

    # 解说一致
    if narr_ok:
        s += 0.5
    else:
        issues.append("解说/stops脱节")

    # 品类多样
    if diverse:
        s += 0.5

    # 结构完整
    if "```mermaid" in narration or ("分钟" in narration):
        s += 0.5

    # 性能
    if elapsed < 40:
        s += 0.5

    # 站点数合理
    if len(stops) >= 2:
        s += 0.5

    return (min(s, 5.0), issues)


if __name__ == "__main__":
    print("🚀 第三轮 10 用户测试 (全修复后)")
    print(f"{'=' * 60}")
    all_results = []

    for i, test in enumerate(TESTS):
        if i > 0:
            time.sleep(3)
        r = run_one_test(test)
        all_results.append(r)
        status = "✅" if r["avg_score"] >= 4 else "⚠️" if r["avg_score"] >= 3 else "❌"
        print(f"  => {status} 均分: {r['avg_score']}/5 | {r['total_time_s']:.0f}s\n")

    # ── 报告 ──
    print(f"{'=' * 60}")
    print("📊 汇总报告")
    print(f"{'=' * 60}")
    scores = []
    for r in all_results:
        s = r["avg_score"]
        scores.append(s)
        status = "✅" if s >= 4 else "⚠️" if s >= 3 else "❌"
        issues_flat = []
        for rd in r["results"]:
            issues_flat.extend(rd.get("issues", []))
        print(f"  {status} {r['id']:18s} {s:.1f}/5 | {r['total_rounds']}轮 {r['total_time_s']:.0f}s | {r['desc']}")
        if issues_flat:
            for iss in issues_flat[:2]:
                print(f"       - {iss}")

    overall = round(sum(scores) / max(len(scores), 1), 1)
    ok = sum(1 for s in scores if s >= 4)
    warn = sum(1 for s in scores if 3 <= s < 4)
    fail = sum(1 for s in scores if s < 3)
    print(f"\n  整体均分: {overall}/5")
    print(f"  ✅ {ok} 良好  ⚠️ {warn} 一般  ❌ {fail} 差")

    with open("data/output/test_10users_v3.json", "w") as f:
        json.dump(
            {
                "summary": {"overall": overall, "scores": scores, "ok": ok, "warn": warn, "fail": fail},
                "results": all_results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print("\n📄 详细: data/output/test_10users_v3.json")

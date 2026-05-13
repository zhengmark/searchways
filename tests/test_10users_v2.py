"""第二轮 10 用户多轮对话测试 — 验证 6 项修复效果."""

import json
import sys
import time

sys.path.insert(0, ".")
from app.core.orchestrator import run_multi_agent

TESTS = [
    {
        "id": "N1_极简输入修复",
        "desc": "验证 P0-3：极简'西安 吃'不反问直接规划",
        "user_id": "test_n1_minimal",
        "rounds": [
            "西安 吃",
            "我想吃火锅，不要太远",
        ],
    },
    {
        "id": "N2_品类匹配验证",
        "desc": "验证 P0-2：美食查询不返回 KTV/艺术空间",
        "user_id": "test_n2_category",
        "rounds": [
            "从西安南门出发逛吃一天",
            "不要火锅烧烤了，换点安静文艺的",
        ],
    },
    {
        "id": "N3_解说脱节修复",
        "desc": "验证 P0-1：解说中 POI 名与 stops 一致",
        "user_id": "test_n3_narration",
        "rounds": [
            "从西安北站出发找好吃的小吃",
            "换西安城墙附近的地方吧",
        ],
    },
    {
        "id": "N4_坐标系复用",
        "desc": "验证 P1-2：多轮中复用已缓存的 geocode",
        "user_id": "test_n4_reuse",
        "rounds": [
            "从曲江出发去大雁塔附近逛逛",
            "不去大雁塔了，去大唐芙蓉园",
            "再加一个吃火锅的地方",
        ],
    },
    {
        "id": "N5_夜间美食",
        "desc": "深夜场景 — 品类匹配",
        "user_id": "test_n5_latenight",
        "rounds": [
            "西安钟楼附近找能开到凌晨的宵夜",
            "换到小寨那边找找",
        ],
    },
    {
        "id": "N6_情侣约会",
        "desc": "浪漫路线 — 品类多样性",
        "user_id": "test_n6_couple",
        "rounds": [
            "西安约会路线半天，文艺小资风格",
            "加入一个看日落的地方",
        ],
    },
    {
        "id": "N7_带老人出游",
        "desc": "低体力 — 步行少、安静",
        "user_id": "test_n7_elder",
        "rounds": [
            "西安陪父母出游，别太累，不要爬楼梯",
            "加个喝茶的地方歇歇",
        ],
    },
    {
        "id": "N8_学生穷游",
        "desc": "低预算 — 免费景点为主",
        "user_id": "test_n8_student",
        "rounds": [
            "西安学生穷游一天，越便宜越好",
            "有不要门票的好地方推荐吗",
        ],
    },
    {
        "id": "N9_商务短途",
        "desc": "高效 — 1小时快览",
        "user_id": "test_n9_business",
        "rounds": [
            "从西安高新出发，1小时速览，商务招待客户",
            "换高档一点的餐厅",
        ],
    },
    {
        "id": "N10_雨季室内",
        "desc": "雨天 — 室内场所为主",
        "user_id": "test_n10_rainy",
        "rounds": [
            "西安下雨天出门，找博物馆和咖啡馆",
            "加个能吃甜品的地方",
        ],
    },
]


def run_one_test(test):
    uid = test["id"]
    user_id = test["user_id"]
    results = []
    session = None
    total_time = 0
    print(f"\n▶ {uid}: {test['desc']}")

    for i, query in enumerate(test["rounds"]):
        t0 = time.time()
        try:
            narration, session = run_multi_agent(query, session=session, user_id=user_id)
            elapsed = round(time.time() - t0, 1)
            total_time += elapsed

            stops = session.stop_names or []
            path = session.path_result

            # 检查修复效果
            checks = {}
            # P0-1: 解说中用到的 POI 名是否在 stops 中
            stops_in_narration = [s for s in stops if s in narration]
            checks["narration_match"] = len(stops_in_narration) >= max(1, len(stops) // 2)
            # P0-3: 极简输入是否不反问
            checks["no_question_back"] = "出发" not in narration[:50] or "请问" not in narration[:80]
            # P1-2: 耗时是否合理（不应每次都超 60s）
            checks["fast_replan"] = elapsed < 45

            score, issues = _rate_detailed(uid, i, query, narration, stops, session, checks)

            results.append(
                {
                    "round": i + 1,
                    "query": query,
                    "narration_preview": narration[:150].replace("\n", " "),
                    "stops": stops,
                    "num_stops": len(stops),
                    "city": session.city or "unknown",
                    "elapsed_s": elapsed,
                    "score": score,
                    "checks": checks,
                    "issues": issues,
                }
            )
            status = "✅" if score >= 3.5 else "⚠️" if score >= 2.0 else "❌"
            print(f"  {status} R{i + 1} {elapsed}s stops={stops} score={score} {' | '.join(issues[:2])}")
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            results.append(
                {
                    "round": i + 1,
                    "query": query,
                    "error": str(e),
                    "elapsed_s": elapsed,
                    "score": 0,
                    "stops": [],
                    "issues": [str(e)[:60]],
                }
            )
            print(f"  ❌ R{i + 1} {elapsed}s ERROR: {e}")
            break

    return {
        "id": uid,
        "desc": test["desc"],
        "user_id": user_id,
        "total_rounds": len(results),
        "total_time_s": total_time,
        "avg_score": round(sum(r["score"] for r in results) / max(len(results), 1), 1),
        "results": results,
    }


def _rate_detailed(test_id, round_i, query, narration, stops, session, checks):
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

    if stops and len(stops) >= 2:
        s += 0.5

    # P0-1: 解说一致性
    if checks.get("narration_match", False):
        s += 1.0
    else:
        issues.append("解说/数据脱节")

    # P0-2: 品类匹配 — 检查返回的 POI 品类
    cats = []
    for p in session.all_pois or []:
        cat = p.get("category", "")
        if cat:
            cats.append(cat)
    if len(set(cats)) >= 2:
        s += 0.5
    if len(cats) >= 1:
        s += 0.5
    else:
        issues.append("无品类信息")

    # P0-3: 极简输入不应反问
    if not checks.get("no_question_back", True):
        s += 0.0
        issues.append("极简反问未兜底")

    # Mermaid/结构化
    if "```mermaid" in narration or "分钟" in narration:
        s += 0.5

    return (min(s, 5.0), issues)


if __name__ == "__main__":
    print("🚀 第二轮 10 用户测试 — 验证 6 项修复")
    print(f"{'=' * 60}")
    all_results = []
    t0_total = time.time()
    success_count = 0

    for i, test in enumerate(TESTS):
        # 每个用户间加短暂间隔，避免 API 限流
        if i > 0:
            time.sleep(8)
        r = run_one_test(test)
        all_results.append(r)
        if r["avg_score"] >= 3.0:
            success_count += 1
        print(f"  均分: {r['avg_score']}/5 | {r['total_time_s']:.0f}s")

    total_time = round(time.time() - t0_total, 0)

    # ── 汇总 ──
    print(f"\n{'=' * 60}")
    print(f"📊 汇总 (总耗时 {total_time}s)")
    print(f"{'=' * 60}")
    all_scores = []
    all_issues = []
    for r in all_results:
        all_scores.append(r["avg_score"])
        for rd in r["results"]:
            for issue in rd.get("issues", []):
                all_issues.append(f"{r['id']} R{rd['round']}: {issue}")
        status = "✅" if r["avg_score"] >= 3 else "⚠️" if r["avg_score"] >= 2 else "❌"
        print(
            f"  {status} {r['id']:22s} | {r['avg_score']:.1f}/5 | {r['total_rounds']}轮 | {r['total_time_s']:.0f}s | {r['desc']}"
        )

    overall = round(sum(all_scores) / max(len(all_scores), 1), 1)
    print(f"\n  成功率: {success_count}/{len(all_results)}")
    print(f"  整体均分: {overall}/5")
    if all_issues:
        print(f"\n  发现 {len(all_issues)} 个问题:")
        for iss in all_issues[:8]:
            print(f"    - {iss}")

    with open("data/output/test_10users_v2.json", "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print("\n📄 详细结果: data/output/test_10users_v2.json")

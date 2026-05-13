"""10 用户多轮对话测试 — 调真实 API，评分."""

import json
import sys
import time

sys.path.insert(0, ".")
from app.core.orchestrator import run_multi_agent

TESTS = [
    {
        "id": "U1_美食探索者",
        "user_id": "test_u1_foodie",
        "rounds": [
            "从西安钟楼出发找美食，3小时内搞定",
            "不想吃火锅烧烤了，来点清淡养生的",
            "再加一个大雁塔附近的好去处",
        ],
    },
    {
        "id": "U2_亲子家庭",
        "user_id": "test_u2_family",
        "rounds": [
            "西安周末带5岁孩子玩半天，室内外都行",
            "不想去商场那种室内场所，要户外",
            "找个能让孩子跑跳撒欢的公园",
        ],
    },
    {
        "id": "U3_文化深度",
        "user_id": "test_u3_culture",
        "rounds": [
            "西安文化探索一日，从北站出发，博物馆古迹都可以",
            "加一个小众的、游客少的地方",
        ],
    },
    {
        "id": "U4_极简输入",
        "user_id": "test_u4_minimal",
        "rounds": [
            "西安 吃",
        ],
    },
    {
        "id": "U5_骑行路线",
        "user_id": "test_u5_bike",
        "rounds": [
            "西安曲江出发骑行半天，风景好的路线",
            "缩短到2小时吧，不要太远",
        ],
    },
    {
        "id": "U6_深夜宵夜",
        "user_id": "test_u6_latenight",
        "rounds": [
            "西安深夜觅食，能开到很晚的店",
            "换到钟楼附近找找看",
        ],
    },
    {
        "id": "U7_多目的地跳跃",
        "user_id": "test_u7_jumper",
        "rounds": [
            "从省体育场出发到大雁塔喝杯茶",
            "不去大雁塔了，改去小雁塔",
            "再加个大唐不夜城附近的拍照点",
        ],
    },
    {
        "id": "U8_预算敏感",
        "user_id": "test_u8_budget",
        "rounds": [
            "西安便宜又好吃的地方，人均30块以内",
            "有点太寒酸了，提高到80块吧，要有特色",
        ],
    },
    {
        "id": "U9_无终点漫游",
        "user_id": "test_u9_wander",
        "rounds": [
            "从丈八六路地铁站出发出去逛逛，没有明确目的地",
            "有点意思，加个能拍照打卡的地方",
        ],
    },
    {
        "id": "U10_模糊需求",
        "user_id": "test_u10_vague",
        "rounds": [
            "周末下午想出去转转，随便去哪",
            "我喜欢咖啡和书店，有推荐吗",
        ],
    },
]


def run_one_test(test):
    """运行一个用户的所有对话轮次，返回结果."""
    uid = test["id"]
    user_id = test["user_id"]
    results = []
    session = None
    total_time = 0

    for i, query in enumerate(test["rounds"]):
        t0 = time.time()
        try:
            narration, session = run_multi_agent(query, session=session, user_id=user_id)
            elapsed = round(time.time() - t0, 1)
            total_time += elapsed

            # 评分子项
            stops = session.stop_names or []
            score = _rate(uid, i, query, narration, stops, session)

            results.append(
                {
                    "round": i + 1,
                    "query": query,
                    "narration_preview": narration[:200].replace("\n", " "),
                    "stops": stops,
                    "num_stops": len(stops),
                    "city": session.city or "unknown",
                    "elapsed_s": elapsed,
                    "score": score,
                }
            )
            print(f"  [{uid}] R{i + 1} ✅ {elapsed}s stops={stops} score={score}")
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            results.append(
                {
                    "round": i + 1,
                    "query": query,
                    "error": str(e),
                    "elapsed_s": elapsed,
                    "score": 0,
                }
            )
            print(f"  [{uid}] R{i + 1} ❌ {elapsed}s error={e}")
            break

    return {
        "id": uid,
        "user_id": user_id,
        "total_rounds": len(results),
        "total_time_s": total_time,
        "avg_score": round(sum(r["score"] for r in results) / max(len(results), 1), 1),
        "results": results,
    }


def _rate(test_id, round_i, query, narration, stops, session):
    """对单轮结果评分 1-5."""
    s = 0
    issues = []

    # 1. 是否有实质输出 (0-1)
    if narration and len(narration) > 30:
        s += 1
    else:
        issues.append("输出过短或为空")

    # 2. 是否有 POI 站点 (0-1)
    if stops and len(stops) >= 1:
        s += 1
    else:
        issues.append("无有效POI站点")

    # 3. 站点间距合理 (0-1) — 粗略判断：没有同名重复
    if len(set(stops)) == len(stops):
        s += 0.5
    if len(stops) >= 2:
        s += 0.5
    else:
        issues.append("站点数少于2")

    # 4. 多样性 — 品类不重复 (0-1)
    if len(stops) >= 2:
        categories = set()
        for p in session.all_pois or []:
            cat = p.get("category", "")
            if cat:
                categories.add(cat)
        if len(categories) >= 2:
            s += 1
        elif len(categories) >= 1:
            s += 0.5
            issues.append("品类单一")
        else:
            issues.append("无品类信息")
    else:
        s += 0.5  # 只有1个站时不需要多样性

    # 5. 是否有 Mermaid 图/解说有结构 (0-1)
    if "```mermaid" in narration or "##" in narration or "###" in narration:
        s += 0.5
    if "分钟" in narration or "km" in narration or "米" in narration:
        s += 0.5
    else:
        issues.append("缺少时间/距离信息")

    return min(s, 5)


if __name__ == "__main__":
    print(f"🚀 开始 10 用户测试 ({len(TESTS)} 场景, {sum(len(t['rounds']) for t in TESTS)} 轮对话)\n")
    all_results = []
    t0_total = time.time()

    for test in TESTS:
        print(f"▶ {test['id']} ({len(test['rounds'])} 轮)")
        r = run_one_test(test)
        all_results.append(r)
        print(f"  均分: {r['avg_score']}/5 | 总耗时: {r['total_time_s']}s\n")

    total_time = round(time.time() - t0_total, 0)

    # ── 输出汇总 ──
    print("=" * 60)
    print(f"📊 10 用户测试汇总 (总耗时 {total_time}s)")
    print("=" * 60)
    all_scores = []
    for r in all_results:
        all_scores.append(r["avg_score"])
        print(f"  {r['id']:20s} | {r['avg_score']:.1f}/5 | {r['total_rounds']}轮 | {r['total_time_s']:.0f}s")

    overall = round(sum(all_scores) / len(all_scores), 1)
    print(f"\n  整体均分: {overall}/5")

    # 汇总问题
    print("\n── 每轮详情 ──")
    for r in all_results:
        for rd in r["results"]:
            err = rd.get("error")
            if err:
                print(f"  {r['id']} R{rd['round']}: ❌ {err}")
            else:
                print(f'  {r["id"]} R{rd["round"]}: score={rd["score"]}/5 stops={rd["num_stops"]} "{rd["query"][:50]}"')

    # 保存 JSON
    with open("data/output/test_10users.json", "w") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print("\n📄 详细结果: data/output/test_10users.json")

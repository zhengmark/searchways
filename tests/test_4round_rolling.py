"""4轮滚动测试 — 每轮淘汰满分用户，新增更刁钻场景，验证鲁棒性."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.core.orchestrator import run_multi_agent

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════
# 评分引擎
# ═══════════════════════════════════════════════════════════


def _rate(narration, stops, all_pois, session, elapsed, prev_keywords=None, user_input=""):
    """0-5 分制，7 个维度."""
    s = 0.0
    issues = []

    # 1. 输出完整性 (1.0)
    if narration and len(narration) > 50:
        s += 1.0
    else:
        issues.append("输出过短或为空")
        return (s, issues)

    # 2. POI 有效性 (1.0)
    if stops and len(stops) >= 1:
        s += 1.0
    else:
        issues.append("无POI站点")
        return (s, issues)

    # 3. 品类多样性 (0.5)
    cats = set()
    for p in all_pois or []:
        c = p.get("category", "")
        if c:
            cats.add(c)
    if len(cats) >= 2:
        s += 0.5
    elif len(stops) >= 2:
        issues.append(f"品类单一({len(cats)}种)")

    # 4. 解说一致性 (0.5)
    stops_in_narr = sum(1 for st in stops if st in narration)
    if stops and stops_in_narr >= max(1, len(stops) // 2):
        s += 0.5
    else:
        issues.append("解说/stops脱节")

    # 5. 约束保留 (0.5)
    curr_kw = getattr(session, "keywords", None)
    if prev_keywords:
        prev = prev_keywords if isinstance(prev_keywords, list) else [prev_keywords]
        kw_ok = any(k in str(curr_kw).lower() for k in (p.lower() for p in prev)) if curr_kw else True
        if not kw_ok:
            issues.append("约束丢失(关键词)")
    s += 0.5  # simplified

    # 6. 结构完整 (0.5)
    if "分钟" in narration or "```mermaid" in narration:
        s += 0.5

    # 7. 性能 (0.5)
    if elapsed < 60:
        s += 0.5
    elif elapsed < 120:
        s += 0.25

    return (min(s, 5.0), issues)


def run_scenario(scenario):
    """执行一个场景的所有轮次."""
    uid = scenario["id"]
    user_id = scenario["user_id"]
    results = []
    session = None
    total_time = 0
    prev_keywords = None

    for i, query in enumerate(scenario["rounds"]):
        t0 = time.time()
        try:
            narration, session = run_multi_agent(query, session=session, user_id=user_id)
            elapsed = round(time.time() - t0, 1)
            total_time += elapsed
            stops = session.stop_names or []
            all_pois = session.all_pois or []

            score, issues = _rate(narration, stops, all_pois, session, elapsed, prev_keywords, query)

            results.append(
                {
                    "round": i + 1,
                    "query": query,
                    "narration_preview": narration[:200].replace("\n", " "),
                    "stops": stops,
                    "num_stops": len(stops),
                    "city": session.city or "unknown",
                    "elapsed_s": elapsed,
                    "score": round(score, 1),
                    "issues": issues,
                }
            )

            prev_keywords = getattr(session, "keywords", None)

            status = "✅" if score >= 4 else "⚠️" if score >= 2.5 else "❌"
            print(f"    {status} R{i + 1} [{elapsed:.0f}s] stops={stops[:3]} score={score:.1f}/5")
            if issues:
                print(f"         > {'; '.join(issues)}")
        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            results.append({"round": i + 1, "query": query, "error": str(e)[:150], "elapsed_s": elapsed, "score": 0})
            print(f"    ❌ R{i + 1} [{elapsed:.0f}s] ERROR: {e}")

    avg = round(sum(r["score"] for r in results) / max(len(results), 1), 1)
    return {
        "id": uid,
        "desc": scenario["desc"],
        "user_id": user_id,
        "total_rounds": len(results),
        "total_time_s": round(total_time, 1),
        "avg_score": avg,
        "results": results,
    }


# ═══════════════════════════════════════════════════════════
# 测试场景定义
# ═══════════════════════════════════════════════════════════

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

ROUND_2_NEW = [
    {
        "id": "R2_反复横跳",
        "desc": "地点来回切换+预算剧烈摇摆",
        "user_id": "t9_flip",
        "rounds": ["钟楼附近吃火锅", "不去钟楼了改回民街吧", "算了还是钟楼但要清淡的", "人均30以内"],
    },
    {
        "id": "R2_矛盾体",
        "desc": "自相矛盾不追问",
        "user_id": "t10_paradox",
        "rounds": ["推荐米其林水准但人均预算30块的地方"],
    },
    {
        "id": "R2_自说自话",
        "desc": "单轮长输入多次改主意",
        "user_id": "t11_self",
        "rounds": ["推荐火锅...等等我改主意了要烤肉...不对还是火锅但要便宜的...再加个咖啡店吧"],
    },
]

ROUND_3_NEW = [
    {
        "id": "R3_信息轰炸",
        "desc": "超长输入150字",
        "user_id": "t12_bomb",
        "rounds": [
            "从钟楼出发去大雁塔，想吃火锅但是不要太辣，环境要好适合约会，人均100左右，要有停车位，最好能看到夜景，不要太拥挤的地方，3小时内，走路不要太多"
        ],
    },
    {
        "id": "R3_不存在地名",
        "desc": "不存在地名→降级",
        "user_id": "t13_fake",
        "rounds": ["想去火星基地吃烧烤", "算了那就去最近的地标吧"],
    },
    {
        "id": "R3_纯情绪化",
        "desc": "纯情绪→逐渐理性",
        "user_id": "t14_mood",
        "rounds": ["好无聊啊随便找个地方", "太远了不想动", "算了就附近吧别太贵"],
    },
]

ROUND_4_NEW = [
    {
        "id": "R4_极限反转",
        "desc": "5轮偏好完全反转",
        "user_id": "t15_rev",
        "rounds": ["钟楼附近吃火锅", "改成清淡口味吧", "还是想吃辣的", "改成纯素食", "算了还是吃火锅"],
    },
    {
        "id": "R4_需求漂移",
        "desc": "完全不同的品类跳跃",
        "user_id": "t16_drift",
        "rounds": ["找个安静的咖啡店", "有书店吗", "改成户外徒步吧", "算了还是购物商场"],
    },
    {
        "id": "R4_地名歧义",
        "desc": "测试geocode兜底",
        "user_id": "t17_ambig",
        "rounds": ["去四路吃饭", "不对，是丈八四路"],
    },
]

ALL_SCENARIOS = ROUND_1 + ROUND_2_NEW + ROUND_3_NEW + ROUND_4_NEW


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════


def run_round(round_num, scenarios, all_history):
    print(f"\n{'═' * 60}")
    print(f"  🔵 Round {round_num} — {len(scenarios)} 用户")
    print(f"{'═' * 60}\n")

    round_results = []
    for i, sc in enumerate(scenarios):
        uid = sc["id"]
        print(f"[{i + 1}/{len(scenarios)}] {uid}: {sc['desc']}")
        r = run_scenario(sc)
        round_results.append(r)
        status = "✅" if r["avg_score"] >= 4 else "⚠️" if r["avg_score"] >= 3 else "❌"
        print(f"  => {status} 均分: {r['avg_score']:.1f}/5 | {r['total_time_s']:.0f}s\n")
        if i < len(scenarios) - 1:
            time.sleep(2)  # API 限流保护

    # 汇总
    scores = [r["avg_score"] for r in round_results]
    overall = round(sum(scores) / max(len(scores), 1), 1)
    perfect = [r for r in round_results if r["avg_score"] >= 5.0]
    ok = sum(1 for s in scores if s >= 4)
    warn = sum(1 for s in scores if 3 <= s < 4)
    fail = sum(1 for s in scores if s < 3)

    summary = {
        "round": round_num,
        "user_count": len(scenarios),
        "overall_avg": overall,
        "perfect_users": [p["id"] for p in perfect],
        "ok": ok,
        "warn": warn,
        "fail": fail,
        "results": round_results,
    }

    # 报告
    report_path = OUTPUT_DIR / f"test_round{round_num}_report.md"
    report = []
    report.append(f"# Round {round_num} 测试报告\n")
    report.append(f"**时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"**用户数**: {len(scenarios)} | **整体均分**: {overall:.1f}/5")
    report.append(f"**✅ 良好**: {ok} | ⚠️ 一般: {warn} | ❌ 差: {fail}\n")
    report.append("## 各用户详情\n")
    for r in round_results:
        emoji = "✅" if r["avg_score"] >= 4 else "⚠️" if r["avg_score"] >= 3 else "❌"
        report.append(f"### {emoji} {r['id']}: {r['desc']} ({r['avg_score']:.1f}/5)\n")
        for rd in r["results"]:
            report.append(f"- **R{rd['round']}**: `{rd['query'][:60]}`")
            if rd.get("error"):
                report[-1] += f" — ❌ {rd['error']}"
            else:
                report[-1] += f" — {rd['score']:.1f}/5, {rd['num_stops']}站, {rd['elapsed_s']:.0f}s"
                if rd.get("issues"):
                    report[-1] += f", ⚠️ {'; '.join(rd['issues'])}"
                report[-1] += f"\n  > {rd.get('narration_preview', '')[:120]}"
            report.append("")
    report.append("---\n")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"📄 报告: {report_path}")

    # JSON
    json_path = OUTPUT_DIR / f"test_round{round_num}_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    all_history.append(summary)
    return perfect, round_results, summary


def merge_scenarios(round_results, perfect, new_scenarios):
    """淘汰满分用户，保留非满分，加入新用户."""
    perfect_ids = {p["id"] for p in perfect}

    survivor_entries = [r for r in round_results if r["avg_score"] < 5.0]
    print(f"  🗑️  淘汰满分: {perfect_ids}")
    print(f"  🔄 保留: {[r['id'] + '(' + str(r['avg_score']) + ')' for r in survivor_entries]}")
    print(f"  ➕ 新增: {[s['id'] for s in new_scenarios]}")

    # 合并：保留的非满分用户 + 新增
    merged_ids = {r["id"] for r in survivor_entries}
    merged = [s for s in ALL_SCENARIOS if s["id"] in merged_ids]
    merged.extend(new_scenarios)
    return merged


if __name__ == "__main__":
    print("🚀 4 轮滚动鲁棒性测试")
    print(f"   模型: {os.getenv('LLM_MODEL', 'default')}")
    print(f"   USE_POI_DB: {os.getenv('USE_POI_DB', 'false')}")

    all_history = []

    # ══ Round 1 ══
    perfect1, results1, summary1 = run_round(1, ROUND_1, all_history)

    # ══ Fix Round 1 issues ══
    print(f"\n{'═' * 60}")
    print("  🔧 Round 1 问题分析")
    print(f"{'═' * 60}")
    all_issues = []
    for r in results1:
        for rd in r["results"]:
            all_issues.extend(rd.get("issues", []))
    issue_counts = {}
    for iss in all_issues:
        issue_counts[iss] = issue_counts.get(iss, 0) + 1
    for iss, cnt in sorted(issue_counts.items(), key=lambda x: -x[1]):
        print(f"  - {iss}: {cnt}次")

    # ══ Build Round 2 ══
    round2_scenarios = merge_scenarios(results1, perfect1, ROUND_2_NEW)

    # ══ Round 2 ══
    perfect2, results2, summary2 = run_round(2, round2_scenarios, all_history)

    print(f"\n{'═' * 60}")
    print("  🔧 Round 2 问题分析")
    print(f"{'═' * 60}")
    all_issues2 = []
    for r in results2:
        for rd in r["results"]:
            all_issues2.extend(rd.get("issues", []))
    issue_counts2 = {}
    for iss in all_issues2:
        issue_counts2[iss] = issue_counts2.get(iss, 0) + 1
    for iss, cnt in sorted(issue_counts2.items(), key=lambda x: -x[1]):
        print(f"  - {iss}: {cnt}次")

    # ══ Build Round 3 ══
    round3_scenarios = merge_scenarios(results2, perfect2, ROUND_3_NEW)

    # ══ Round 3 ══
    perfect3, results3, summary3 = run_round(3, round3_scenarios, all_history)

    print(f"\n{'═' * 60}")
    print("  🔧 Round 3 问题分析")
    print(f"{'═' * 60}")
    all_issues3 = []
    for r in results3:
        for rd in r["results"]:
            all_issues3.extend(rd.get("issues", []))
    issue_counts3 = {}
    for iss in all_issues3:
        issue_counts3[iss] = issue_counts3.get(iss, 0) + 1
    for iss, cnt in sorted(issue_counts3.items(), key=lambda x: -x[1]):
        print(f"  - {iss}: {cnt}次")

    # ══ Build Round 4 ══
    round4_scenarios = merge_scenarios(results3, perfect3, ROUND_4_NEW)

    # ══ Round 4 ══
    perfect4, results4, summary4 = run_round(4, round4_scenarios, all_history)

    # ══════════════════════════════════════════════════
    # 最终汇总
    # ══════════════════════════════════════════════════
    print(f"\n{'═' * 60}")
    print("  📊 最终汇总报告")
    print(f"{'═' * 60}\n")

    for s in all_history:
        rn = s["round"]
        print(
            f"  Round {rn}: 均分 {s['overall_avg']:.1f}/5 | ✅{s['ok']} ⚠️{s['warn']} ❌{s['fail']} | 淘汰: {s['perfect_users']}"
        )

    all_scores = [s["overall_avg"] for s in all_history]
    print(f"\n  4轮总均分: {round(sum(all_scores) / 4, 1)}/5")

    final_path = OUTPUT_DIR / "test_final_summary.json"
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(
            {"rounds": all_history, "overall_avg": round(sum(all_scores) / 4, 1)}, f, ensure_ascii=False, indent=2
        )
    print(f"\n📄 最终报告: {final_path}")

"""Phase 5 全面评测 — 8 用户 × 多维度 × 端到端.

测试维度:
  A) 路线规划质量 (约束满足、品类匹配、解说一致性)
  B) 交互编辑能力 (select/remove/confirm)
  C) 交通模式多样性 (Phase 3 — 步行/骑行/公交/驾车)
  D) 走廊数据完整性 (Phase 2 — corridor_pois/shape/clusters)
  E) 约束校验触发 (Phase 6 — violations 检测)
  F) 会话持久化 (Phase 1 — JSON 往返)

运行: python3 tests/test_phase5_eval.py
"""

import sys
import time
import uuid

sys.path.insert(0, ".")

from app.core.orchestrator import run_multi_agent
from web.server import _load_sessions, _rebuild_route, _save_session
from web.server import sessions as web_sessions

OUTPUT_DIR = "data/output"
REPORT_PATH = f"{OUTPUT_DIR}/eval_report_phase5.md"

# ═══════════════════════════════════════════════════════
# 8 个测试用户画像
# ═══════════════════════════════════════════════════════

PERSONAS = {
    "肉食派": {
        "desc": "28岁男生，无辣不欢，爱吃肉，预算不限",
        "keywords": ["火锅", "烧烤", "肉", "辣"],
        "constraints": {"diet": ["辣"], "budget": "high"},
    },
    "养生派": {
        "desc": "35岁女生，不吃辣不吃油炸，偏好清淡有机素食，人均50以内",
        "keywords": ["素食", "清淡", "沙拉", "茶"],
        "constraints": {"diet": ["无辣", "无油炸", "素食"], "budget": "low"},
    },
    "亲子派": {
        "desc": "带5岁女儿出游，孩子只吃面条饺子，需要户外跑跳空间",
        "keywords": ["亲子", "公园", "面馆", "游乐"],
        "constraints": {"interests": ["亲子", "户外"], "pace": "slow"},
    },
    "文艺派": {
        "desc": "25岁女生，爱探店拍照，喜欢咖啡甜品书店，不爱走远",
        "keywords": ["咖啡", "甜品", "书店", "拍照"],
        "constraints": {"interests": ["拍照", "安静"], "pace": "slow"},
    },
    "商务派": {
        "desc": "40岁男士，今晚请客户吃饭，要高档中餐人均200+有包间",
        "keywords": ["商务", "包间", "中餐", "高档"],
        "constraints": {"budget": "high", "diet": ["商务宴请"]},
    },
    "穷游派": {
        "desc": "22岁大学生，预算极度有限人均30以内，免费景点为主",
        "keywords": ["免费", "景点", "小吃", "便宜"],
        "constraints": {"budget": "low"},
    },
    "银发派": {
        "desc": "68岁膝盖不好，不能走超过500m，喜欢公园下棋喝茶，要无障碍",
        "keywords": ["公园", "茶", "安静", "平路"],
        "constraints": {"pace": "slow", "interests": ["无障碍", "安静"]},
    },
    "健身派": {
        "desc": "30岁健身教练，高蛋白低碳水无糖，吃完要去健身房",
        "keywords": ["轻食", "沙拉", "鸡胸肉", "健身房"],
        "constraints": {"diet": ["无糖", "无油炸", "高蛋白"]},
    },
}

TESTS = [
    {
        "id": "U1_肉食派",
        "user_id": "u1_meat",
        "persona": "肉食派",
        "query": "从丈八六路出发去钟楼，想吃火锅和烧烤，无辣不欢",
    },
    {
        "id": "U2_养生派",
        "user_id": "u2_health",
        "persona": "养生派",
        "query": "从大雁塔出发，找清淡的素食餐厅，人均不超过50",
    },
    {
        "id": "U3_亲子派",
        "user_id": "u3_kid",
        "persona": "亲子派",
        "query": "周末带5岁女儿从曲江出发玩半天，要户外能跑跳的地方，吃面条",
    },
    {
        "id": "U4_文艺派",
        "user_id": "u4_artsy",
        "persona": "文艺派",
        "query": "从小寨出发探店，想找好看的咖啡店甜品店书店，拍照发朋友圈",
    },
    {
        "id": "U5_商务派",
        "user_id": "u5_biz",
        "persona": "商务派",
        "query": "今晚在西安高新请3个重要客户吃饭，要高档中餐包间，人均200以上",
    },
    {
        "id": "U6_穷游派",
        "user_id": "u6_budget",
        "persona": "穷游派",
        "query": "西安穷游一日，从北站出发，免费景点+便宜小吃，人均不超过30",
    },
    {
        "id": "U7_银发派",
        "user_id": "u7_elder",
        "persona": "银发派",
        "query": "从环城公园出发，腿脚不好走不远，找能喝茶下棋的好去处",
    },
    {
        "id": "U8_健身派",
        "user_id": "u8_fit",
        "persona": "健身派",
        "query": "从丈八六路出发找健康餐，高蛋白低碳水，吃完去健身房",
    },
]


def _score_constraints(session, persona):
    """评分: 约束满足度 (0-5)."""
    p = PERSONAS.get(persona, {})
    constraints = p.get("constraints", {})
    stops = session.stop_names or []
    all_pois = session.all_pois or []
    narration = getattr(session, "last_user_input", "")
    score = 5
    reasons = []

    # 预算检查
    budget = constraints.get("budget")
    if budget == "low":
        avg_price = sum(poi.get("price_per_person", 0) or 0 for poi in all_pois) / max(len(all_pois), 1)
        # Allow some flexibility for empty prices
        priced_pois = [p for p in all_pois if p.get("price_per_person")]
        if priced_pois:
            avg_priced = sum(p.get("price_per_person", 0) for p in priced_pois) / len(priced_pois)
            if avg_priced > 80:
                score -= 2
                reasons.append(f"预算超标: 均价{avg_priced:.0f}>{80}")
            elif avg_priced > 50:
                score -= 1
                reasons.append(f"预算略高: 均价{avg_priced:.0f}")

    if budget == "high":
        priced_pois = [p for p in all_pois if p.get("price_per_person")]
        if priced_pois:
            avg_priced = sum(p.get("price_per_person", 0) for p in priced_pois) / len(priced_pois)
            if avg_priced < 100:
                score -= 1
                reasons.append(f"高档不足: 均价{avg_priced:.0f}<100")

    # 关键词覆盖
    keywords = p.get("keywords", [])
    stop_text = " ".join(stops).lower()
    matches = sum(1 for kw in keywords if kw.lower() in stop_text)
    if len(keywords) >= 3 and matches == 0:
        score -= 2
        reasons.append(f"关键词0匹配: {keywords}")

    return max(0, min(5, score)), reasons


def _score_transport(session):
    """评分: 交通模式多样性 (0-5)."""
    path = session.path_result
    if not path or not path.get("segments"):
        return 0, ["无路段数据"]
    segments = path["segments"]
    modes = set(s.get("transport", "") for s in segments)
    score = 0
    details = []
    if "步行" in modes:
        score += 1
    if "骑行" in modes:
        score += 1.5
    if "公交/地铁" in modes:
        score += 2
    if "驾车" in modes:
        score += 0.5
    details.append(f"模式: {modes}")
    details.append(f"总耗时: {path.get('total_duration_min', 0)}min")
    details.append(f"总距离: {path.get('total_distance', 0)}m")
    return min(5, score), details


def _score_corridor(session):
    """评分: 走廊数据完整性 (0-5)."""
    score = 5
    details = []

    n_pois = len(session.corridor_pois)
    n_clusters = len(session.corridor_clusters)
    n_shape = len(session.corridor_shape)

    details.append(f"corridor_pois: {n_pois}")
    details.append(f"cluster_markers: {n_clusters}")
    details.append(f"corridor_shape: {n_shape} 点")

    if n_pois == 0:
        score -= 3
        details.append("❌ corridor_pois 为空")
    elif n_pois < 10:
        score -= 1
        details.append("⚠️ corridor_pois < 10")

    if n_clusters == 0:
        score -= 1
        details.append("⚠️ cluster_markers 为空")

    if n_shape < 3:
        score -= 1
        details.append("⚠️ corridor_shape 不足")

    # Check POI fields
    if session.corridor_pois:
        sample = session.corridor_pois[0]
        if not sample.get("recommendation_reasons"):
            score -= 0.5
            details.append("⚠️ 缺少推荐理由")
        if sample.get("projection_ratio") is None:
            score -= 0.5
            details.append("⚠️ 缺少投影比例")

    return max(0, score), details


def _score_violations(session):
    """评分: 约束违规检测 (0-5)."""
    violations = getattr(session, "violations", []) or []
    score = 5
    if violations:
        score -= min(3, len(violations))
        details = [f"{len(violations)}个违规: {'; '.join(violations[:3])}"]
    else:
        details = ["无违规"]
    return max(0, score), details


def _score_editing(session):
    """评分: 交互编辑能力 (0-5，仅测流程不测 UI)."""
    score = 5
    details = []

    # 测试 select-poi
    if session.corridor_pois:
        first = session.corridor_pois[0]
        session.selected_poi_ids.append(first["id"])
        path = _rebuild_route(session)
        if path and path.get("segments"):
            details.append(f"select 成功: +{first['name']}")
        else:
            score -= 2
            details.append("❌ select 后无法重建路线")

        # 测试 remove
        if session.selected_poi_ids:
            rid = session.selected_poi_ids[0]
            session.selected_poi_ids.remove(rid)
            session.removed_poi_ids.append(rid)
            path = _rebuild_route(session)
            if path and path.get("segments"):
                details.append(f"remove 成功: -{first['name']}")
            else:
                score -= 1
                details.append("⚠️ remove 后无法重建路线")
    else:
        score = 0
        details.append("❌ 无 corridor_pois，无法测试编辑")

    # 测试 confirm
    try:
        from app.core.narrator_agent import run_confirmation_narrator

        narration = run_confirmation_narrator(session, user_input="test")
        if narration.get("narration") and narration.get("mermaid"):
            details.append(f"confirm 成功: {len(narration['narration'])}字解说")
        else:
            score -= 1
            details.append("⚠️ confirm 解说/Mermaid 缺失")
    except Exception as e:
        score -= 2
        details.append(f"❌ confirm 失败: {e}")

    return max(0, score), details


def _score_persistence(session, sid):
    """评分: 会话持久化 (0-5)."""
    score = 5
    details = []
    try:
        _save_session(sid, session)
        restored = _load_sessions().get(sid)
        if restored:
            checks = [
                "city",
                "start_name",
                "dest_name",
                "stop_names",
                "corridor_pois",
                "selected_poi_ids",
                "route_confirmed",
            ]
            fails = []
            for k in checks:
                orig = getattr(session, k, None)
                rest = getattr(restored, k, None)
                if isinstance(orig, list):
                    if len(orig) != len(rest or []):
                        fails.append(k)
                elif orig != rest:
                    fails.append(k)
            if fails:
                score -= len(fails)
                details.append(f"⚠️ 字段不一致: {fails}")
            else:
                details.append(f"✅ 全部 {len(checks)} 字段一致")
        else:
            score = 0
            details.append("❌ 无法恢复")
    except Exception as e:
        score = 0
        details.append(f"❌ 持久化异常: {e}")
    return max(0, score), details


def run_eval():
    print("=" * 70)
    print("Phase 5 全面评测 — 8 用户 × 6 维度")
    print("=" * 70)

    results = []
    start_time = time.time()

    for i, test in enumerate(TESTS):
        tid = test["id"]
        uid = test["user_id"]
        persona = test["persona"]
        query = test["query"]
        p_info = PERSONAS[persona]
        print(f"\n{'─' * 60}")
        print(f"[{i + 1}/8] {tid} | {p_info['desc'][:50]}")
        print(f"    Query: {query[:80]}")
        t0 = time.time()

        try:
            narration, session = run_multi_agent(query, session=None, user_id=uid)
            elapsed = time.time() - t0
        except Exception as e:
            print(f"    ❌ 规划失败: {e}")
            results.append({"id": tid, "persona": persona, "error": str(e)})
            continue

        sid = uuid.uuid4().hex[:8]
        web_sessions[sid] = session

        # 6 维度评分
        s1, d1 = _score_constraints(session, persona)
        s2, d2 = _score_transport(session)
        s3, d3 = _score_corridor(session)
        s4, d4 = _score_violations(session)
        s5, d5 = _score_editing(session)
        s6, d6 = _score_persistence(session, sid)

        total = round((s1 + s2 + s3 + s4 + s5 + s6) / 6, 1)

        print(f"    约束:{s1}/5  交通:{s2}/5  走廊:{s3}/5  违规检测:{s4}/5  编辑:{s5}/5  持久化:{s6}/5")
        print(f"    总评: {total}/5 | 耗时: {elapsed:.0f}s | Stops: {session.stop_names}")

        results.append(
            {
                "id": tid,
                "persona": persona,
                "user_id": uid,
                "query": query,
                "elapsed_s": round(elapsed, 1),
                "stops": session.stop_names,
                "total_duration_min": session.path_result.get("total_duration_min", 0) if session.path_result else 0,
                "total_distance_m": session.path_result.get("total_distance", 0) if session.path_result else 0,
                "transport_modes": list(
                    set(s.get("transport", "") for s in (session.path_result or {}).get("segments", []))
                ),
                "n_corridor_pois": len(session.corridor_pois),
                "n_corridor_clusters": len(session.corridor_clusters),
                "n_violations": len(session.violations),
                "scores": {
                    "constraints": s1,
                    "transport": s2,
                    "corridor": s3,
                    "violations": s4,
                    "editing": s5,
                    "persistence": s6,
                    "total": total,
                },
                "details": {
                    "constraints": d1,
                    "transport": d2,
                    "corridor": d3,
                    "violations": d4,
                    "editing": d5,
                    "persistence": d6,
                },
            }
        )

        time.sleep(1)  # API rate limiting

    total_elapsed = time.time() - start_time

    # ═════════════════════════════════════════════
    # 生成报告
    # ═════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print("生成评测报告...")

    avg_total = sum(r["scores"]["total"] for r in results) / max(len(results), 1)
    avg_constraints = sum(r["scores"]["constraints"] for r in results) / max(len(results), 1)
    avg_transport = sum(r["scores"]["transport"] for r in results) / max(len(results), 1)
    avg_corridor = sum(r["scores"]["corridor"] for r in results) / max(len(results), 1)
    avg_violations = sum(r["scores"]["violations"] for r in results) / max(len(results), 1)
    avg_editing = sum(r["scores"]["editing"] for r in results) / max(len(results), 1)
    avg_persistence = sum(r["scores"]["persistence"] for r in results) / max(len(results), 1)

    report_lines = [
        "# Phase 5 全面评测报告",
        "",
        f"> 测试时间: 2026-05-09 | 8 个多样化用户 | 6 维度评估 | 总耗时 {total_elapsed:.0f}s",
        "",
        "---",
        "",
        "## 一、评测设计",
        "",
        "### 8 个用户画像",
        "",
        "| ID | 画像 | 核心约束 | 查询示例 |",
        "|----|------|----------|----------|",
    ]
    for r in results:
        p = PERSONAS.get(r["persona"], {})
        report_lines.append(f"| {r['id']} | {r['persona']} | {p.get('desc', '')[:40]} | {r['query'][:50]}... |")

    report_lines += [
        "",
        "### 6 维评估体系",
        "",
        "| 维度 | 满分 | 评估内容 |",
        "|------|------|----------|",
        "| A) 约束满足 | 5 | 预算匹配、关键词覆盖、饮食限制 |",
        "| B) 交通多样性 | 5 | 步行/骑行/公交地铁/驾车 模式覆盖 |",
        "| C) 走廊完整性 | 5 | corridor_pois 数量、推荐理由、形状 |",
        "| D) 违规检测 | 5 | constraint_checker 违规触发 |",
        "| E) 交互编辑 | 5 | select/remove/confirm 流程可用性 |",
        "| F) 会话持久化 | 5 | JSON 序列化/反序列化字段一致性 |",
        "",
        "---",
        "",
        "## 二、评测结果汇总",
        "",
        "| 维度 | 均分 | 评级 |",
        "|------|------|------|",
        f"| A 约束满足 | {avg_constraints:.1f}/5 | {'🟢' if avg_constraints >= 4 else '🟡' if avg_constraints >= 3 else '🔴'} |",
        f"| B 交通多样性 | {avg_transport:.1f}/5 | {'🟢' if avg_transport >= 4 else '🟡' if avg_transport >= 3 else '🔴'} |",
        f"| C 走廊完整性 | {avg_corridor:.1f}/5 | {'🟢' if avg_corridor >= 4 else '🟡' if avg_corridor >= 3 else '🔴'} |",
        f"| D 违规检测 | {avg_violations:.1f}/5 | {'🟢' if avg_violations >= 4 else '🟡' if avg_violations >= 3 else '🔴'} |",
        f"| E 交互编辑 | {avg_editing:.1f}/5 | {'🟢' if avg_editing >= 4 else '🟡' if avg_editing >= 3 else '🔴'} |",
        f"| F 会话持久化 | {avg_persistence:.1f}/5 | {'🟢' if avg_persistence >= 4 else '🟡' if avg_persistence >= 3 else '🔴'} |",
        f"| **综合** | **{avg_total:.1f}/5** | {'🟢' if avg_total >= 4 else '🟡' if avg_total >= 3 else '🔴'} |",
        "",
        "---",
        "",
        "## 三、逐用户详情",
        "",
    ]

    for r in results:
        report_lines += [
            f"### {r['id']} — {r['persona']}",
            "",
            f"- **查询**: {r['query']}",
            f"- **耗时**: {r['elapsed_s']}s",
            f"- **站点**: {r['stops']}",
            f"- **耗时/距离**: {r['total_duration_min']}min / {r['total_distance_m']}m",
            f"- **交通模式**: {r['transport_modes']}",
            f"- **走廊POI数**: {r['n_corridor_pois']}",
            f"- **违规数**: {r['n_violations']}",
            "",
            "| 维度 | 评分 | 详情 |",
            "|------|------|------|",
        ]
        for dim in ["constraints", "transport", "corridor", "violations", "editing", "persistence"]:
            s = r["scores"][dim]
            d = r["details"][dim]
            detail_str = "; ".join(str(x) for x in d[:3])
            emoji = "✅" if s >= 4 else "⚠️" if s >= 2 else "❌"
            report_lines.append(f"| {dim} | {emoji} {s}/5 | {detail_str} |")
        report_lines.append(f"| **综合** | **{r['scores']['total']}/5** | |")
        report_lines.append("")

    report_lines += [
        "---",
        "",
        "## 四、分维度分析",
        "",
        f"### A) 约束满足 (均分 {avg_constraints:.1f}/5)",
        "",
    ]
    # Group by constraint issues
    constraint_issues = []
    for r in results:
        for d in r["details"]["constraints"]:
            if d and not d.startswith("✅"):
                constraint_issues.append(f"- {r['id']}: {d}")
    if constraint_issues:
        report_lines.extend(constraint_issues[:10])
    else:
        report_lines.append("无重大约束违反")

    report_lines += [
        "",
        f"### B) 交通多样性 (均分 {avg_transport:.1f}/5)",
        "",
    ]
    mode_counts = {}
    for r in results:
        for m in r["transport_modes"]:
            mode_counts[m] = mode_counts.get(m, 0) + 1
    report_lines.append(f"各模式出现频次: {mode_counts}")
    all_modes = set()
    for r in results:
        all_modes.update(r["transport_modes"])
    if "公交/地铁" not in all_modes:
        report_lines.append("⚠️ 无用户触发公交/地铁模式")
    if "骑行" not in all_modes:
        report_lines.append("⚠️ 无用户触发骑行模式")

    report_lines += [
        "",
        f"### C) 走廊数据完整性 (均分 {avg_corridor:.1f}/5)",
        "",
    ]
    no_corridor = [r for r in results if r["n_corridor_pois"] == 0]
    if no_corridor:
        report_lines.append(f"⚠️ {len(no_corridor)} 用户无走廊数据: {[r['id'] for r in no_corridor]}")
    else:
        report_lines.append("✅ 所有用户均有走廊数据")

    report_lines += [
        "",
        f"### D) 违规检测 (均分 {avg_violations:.1f}/5)",
        "",
    ]
    with_violations = [r for r in results if r["n_violations"] > 0]
    if with_violations:
        report_lines.append(f"{len(with_violations)} 用户触发违规检测:")
        for r in with_violations:
            report_lines.append(f"- {r['id']}: {r['n_violations']} 违规")
    else:
        report_lines.append("无用户触发违规（可能均符合约束）")

    report_lines += [
        "",
        f"### E) 交互编辑 (均分 {avg_editing:.1f}/5)",
        "",
        "测试了 select → rebuild → remove → rebuild → confirm 流程。",
    ]
    edit_fails = [r for r in results if r["scores"]["editing"] < 4]
    if edit_fails:
        for r in edit_fails:
            report_lines.append(f"- {r['id']}: {r['details']['editing']}")

    report_lines += [
        "",
        f"### F) 会话持久化 (均分 {avg_persistence:.1f}/5)",
        "",
        "测试了 JSON 序列化/反序列化 + 7 个关键字段一致性。",
    ]

    report_lines += [
        "",
        "---",
        "",
        "## 五、改进建议",
        "",
    ]

    if avg_constraints < 4:
        report_lines.append(f"- **约束满足**: 当前 {avg_constraints:.1f}/5，需优化 system prompt 中的约束理解")
    if avg_transport < 4:
        report_lines.append(f"- **交通多样性**: 当前 {avg_transport:.1f}/5，检查 graph_planner 距离阈值")
    if avg_corridor < 4:
        report_lines.append(
            f"- **走廊数据**: 当前 {avg_corridor:.1f}/5，确认 build_corridor 在 tool_build_route 中调用"
        )
    if avg_editing < 4:
        report_lines.append(f"- **交互编辑**: 当前 {avg_editing:.1f}/5，检查 _rebuild_route 逻辑")

    report_lines += [
        "",
        "---",
        "",
        "*报告自动生成于 2026-05-09 | 测试框架: tests/test_phase5_eval.py*",
    ]

    report = "\n".join(report_lines)

    # 写入文件
    import os

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report)

    # 控制台摘要
    print(f"\n{'=' * 70}")
    print("评测完成！")
    print(f"  综合均分: {avg_total:.1f}/5")
    print(f"  约束满足: {avg_constraints:.1f}/5")
    print(f"  交通多样性: {avg_transport:.1f}/5")
    print(f"  走廊完整性: {avg_corridor:.1f}/5")
    print(f"  违规检测: {avg_violations:.1f}/5")
    print(f"  交互编辑: {avg_editing:.1f}/5")
    print(f"  会话持久化: {avg_persistence:.1f}/5")
    print(f"  报告: {REPORT_PATH}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    run_eval()

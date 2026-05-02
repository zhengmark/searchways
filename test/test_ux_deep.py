"""多用户画像深度体验测试 — 5种不同类型用户"""
import sys, time, json
sys.path.insert(0, ".")
from agent.core import run_agent, AgentSession

SCENARIOS = [
    {
        "id": "A", "persona": "👨‍👩‍👧 带娃家庭",
        "desc": "周末想带孩子出去玩，既要有公园又要有亲子餐厅",
        "input": "周六带5岁孩子去北京朝阳公园附近，找个公园和2个适合带娃吃饭的地方",
        "city": "北京",
    },
    {
        "id": "B", "persona": "💑 约会规划师",
        "desc": "在外滩找浪漫餐厅和看夜景的地方",
        "input": "从上海外滩出发，找2个氛围好的约会餐厅，最后去一个看夜景的地方",
        "city": "上海",
    },
    {
        "id": "C", "persona": "🏃 暴走打卡族",
        "desc": "一天暴走5个打卡点",
        "input": "从西安钟楼出发，去大雁塔，路上安排5个吃喝拍照打卡的地方",
        "city": "西安",
    },
    {
        "id": "D", "persona": "🤷 极简输入",
        "desc": "只说两个字，看系统如何应对",
        "input": "北京 吃",
        "city": "北京",
    },
    {
        "id": "E", "persona": "🌃 深夜觅食者",
        "desc": "晚上11点找还开着的小吃",
        "input": "晚上11点从西安回民街出发，想找3个深夜还开的小吃宵夜",
        "city": "西安",
    },
]

def run_one(sc):
    print(f"\n╔{'═'*58}╗")
    print(f"║  {sc['persona']}: {sc['desc']}                    ║")
    print(f"╚{'═'*58}╝")
    print(f"  📝 输入: \"{sc['input']}\"")

    session = AgentSession()
    session.default_city = sc["city"]
    start = time.time()

    try:
        result, session = run_agent(sc["input"], session)
        ok = True
    except Exception as e:
        result = f"[CRASH] {e}"
        ok = False

    elapsed = time.time() - start

    # ── 体验评分卡 ──
    score = 0
    notes = []

    # 1. 是否有结构化路线
    if session.path_result:
        score += 30
        notes.append("✅ 生成结构化路线")
    else:
        notes.append("⚠️ 未生成结构化路线（LLM自由发挥）")

    # 2. 是否有地图
    if "```mermaid" in result:
        score += 15
        notes.append("✅ 含Mermaid路线图")
    else:
        notes.append("❌ 无Mermaid图")

    # 3. POI数量匹配
    stops = session.stop_names
    notes.append(f"📍 {len(stops)}个站点: {stops}")

    # 4. 路段合理性
    if session.path_result:
        pr = session.path_result
        notes.append(f"🛣️ 总{pr['total_distance']}m / {pr['total_duration_min']}min")
        segs = pr["segments"]
        # 检查是否有0m段
        if any(s["distance"] < 10 for s in segs):
            notes.append("⚠️ 存在<10m的过短路段")
            score -= 5
        # 检查交通工具多样性
        transports = set(s["transport"] for s in segs)
        notes.append(f"🚦 交通工具: {transports}")
        if len(transports) > 1:
            score += 5
        # 检查是否空间连贯
        if len(segs) >= 2:
            total = sum(s["distance"] for s in segs)
            if total > 1000:
                score += 5
                notes.append("✅ 空间分布充分(>1km)")
            else:
                notes.append("⚠️ 路线较短(<1km)")

    # 5. 起点终点处理
    if session.start_name and session.start_name not in stops:
        score += 5
        notes.append(f"✅ 起点({session.start_name})未入选POI")
    elif session.start_name in stops:
        notes.append(f"⚠️ 起点出现在POI列表中")

    if session.dest_name:
        notes.append(f"🏁 终点: {session.dest_name}")
        if session.dest_name not in stops:
            score += 5

    # 6. 响应时间
    if elapsed < 20:
        score += 5
        notes.append(f"⏱️ 响应快({elapsed:.0f}s)")
    elif elapsed < 60:
        notes.append(f"⏱️ 响应正常({elapsed:.0f}s)")
    else:
        notes.append(f"⏱️ 响应较慢({elapsed:.0f}s)")

    # 上限100
    score = min(score, 100) if ok else 0

    print(f"  ═══ 体验报告 ═══")
    print(f"  得分: {score}/100 {'✅' if score >= 60 else '⚠️' if score >= 30 else '❌'}")
    for n in notes:
        print(f"  {n}")

    return {"id": sc["id"], "persona": sc["persona"], "score": score, "notes": notes, "ok": ok}

if __name__ == "__main__":
    results = []
    for sc in SCENARIOS:
        r = run_one(sc)
        results.append(r)

    print(f"\n{'='*60}")
    print(f"                      总 览")
    print(f"{'='*60}")
    for r in results:
        icon = "✅" if r["score"] >= 60 else "⚠️" if r["score"] >= 30 else "❌"
        print(f"  {icon} {r['persona']}: {r['score']}/100")

"""朋友推荐 — 10 个多样化用户画像 + 组团场景测试."""
import json, time, sys
sys.path.insert(0, '.')
from app.core.orchestrator import run_multi_agent

# ── 10 个用户画像 ───────────────────────────────
PERSONAS = {
    "小美": "25岁女生，今天来月经肚子疼，不能吃辣不能吃冰不能吃生冷，想找个温暖舒服安静的地方坐坐喝热饮，最好有沙发",
    "大力": "30岁健身教练，高蛋白低碳水饮食，不吃油炸不吃甜食，爱喝蛋白粉和鸡胸肉，中午想吃健康餐",
    "老王": "55岁退休大爷，膝盖不好不能走太多路（最多走10分钟），喜欢喝茶下棋逛公园，不喜欢吵闹",
    "阿琳": "28岁素食主义者（不吃肉不吃蛋不吃奶），喜欢拍照打卡发朋友圈，爱去网红店",
    "小明妈": "带8岁儿子小明出游，小明只吃熟悉的食物（米饭面条饺子，不吃生的不吃奇特的），精力旺盛需要能跑跳的地方",
    "小陈": "28岁程序员，颈椎不好想周末放松，预算有限（人均40以内），喜欢按摩看电影",
    "娜娜": "32岁孕妇怀孕6个月，不能走远（每次走路不超过15分钟），不能吃生冷辛辣，需要随时有卫生间，喜欢安静",
    "阿强": "40岁商务人士，今晚要宴请3个重要客户，需要高档餐厅（人均200+），环境要好有包间",
    "小丽": "22岁大学生刚失恋，想吃甜品喝奶茶，想拍美美的照片治愈心情，不想走太多路",
    "老刘": "60岁退休历史老师，喜欢逛博物馆古迹，但腿脚不便需要无障碍通道和电梯，不能爬楼梯",
}

# ── 10 个测试场景 ──────────────────────────────
TESTS = [
    {
        "id": "P1_小美_生理期",
        "user_id": "persona_xiaomei",
        "desc": "生理期：不能吃辣/冰/生冷，要温暖舒服",
        "rounds": [
            f"你好，我今天的状况：{PERSONAS['小美']}。从西安小寨出发，帮我规划一下下午的安排",
            "红糖姜茶哪里有？再加个安静的书店或者花店",
        ],
    },
    {
        "id": "P2_大力_健身控",
        "user_id": "persona_dali",
        "desc": "健身饮食：高蛋白低碳水，不吃油炸甜食",
        "rounds": [
            f"我今天的需求：{PERSONAS['大力']}。从西安高新出发，给我找吃的",
            "下午训练完还要补充一顿，换个地方找找看",
        ],
    },
    {
        "id": "P3_老王_膝盖不好",
        "user_id": "persona_laowang",
        "desc": "老人：膝盖不好只能走10分钟，喝茶下棋",
        "rounds": [
            f"我的情况：{PERSONAS['老王']}。在西安环城公园附近活动",
            "想找个能下棋的茶馆，走路不要超过10分钟",
        ],
    },
    {
        "id": "P4_阿琳_素食网红",
        "user_id": "persona_alin",
        "desc": "素食者：不吃肉蛋奶，要拍照打卡",
        "rounds": [
            f"我的需求：{PERSONAS['阿琳']}。从西安钟楼出发，找适合拍照的素食餐厅",
            "再加个好看的景点或者艺术展，拍照好看的",
        ],
    },
    {
        "id": "P5_小明妈_带娃",
        "user_id": "persona_xiaoming",
        "desc": "带8岁挑食男孩：只吃米饭面条，要能跑跳",
        "rounds": [
            f"带娃出游：{PERSONAS['小明妈']}。周末从西安曲江出发",
            "换个有大草坪或者游乐设施的地方",
        ],
    },
    {
        "id": "P6_小陈_颈椎放松",
        "user_id": "persona_xiaochen",
        "desc": "程序员：颈椎不好，预算40以内",
        "rounds": [
            f"周末放松：{PERSONAS['小陈']}。在西安",
            "算了不看电影了，找个能按摩或者做SPA的地方",
        ],
    },
    {
        "id": "P7_娜娜_孕妇",
        "user_id": "persona_nana",
        "desc": "孕妇6月：不能走远，不能吃生冷辣，要卫生间",
        "rounds": [
            f"我是孕妇：{PERSONAS['娜娜']}。从西安曲江出发，想去逛逛",
            "有点累了，找个能坐下来休息的甜品店",
        ],
    },
    {
        "id": "P8_阿强_商务宴请",
        "user_id": "persona_aqiang",
        "desc": "商务宴请：人均200+，包间，高档",
        "rounds": [
            f"商务宴请：{PERSONAS['阿强']}。在西安高新区",
            "客户想吃陕菜，换个陕菜馆子",
        ],
    },
    {
        "id": "P9_小丽_失恋疗伤",
        "user_id": "persona_xiaoli",
        "desc": "失恋：甜品奶茶拍照，不想走路",
        "rounds": [
            f"心情不好：{PERSONAS['小丽']}。在西安钟楼附近",
            "加点花店或者好看的杂货铺",
        ],
    },
    {
        "id": "P10_老刘_博物馆控",
        "user_id": "persona_laoliu",
        "desc": "退休教师：爱博物馆古迹，需要无障碍电梯",
        "rounds": [
            f"文化之旅：{PERSONAS['老刘']}。从西安北站出发，想看博物馆",
            "走路有点累，换个不用爬楼梯的地方，要有电梯",
        ],
    },
]

# ── 3 个朋友组团场景 ──────────────────────────
GROUP_TESTS = [
    {
        "id": "G1_三人行_冲突调和",
        "desc": "小美(生理期)+大力(健身)+阿琳(素食)三人一起出门",
        "user_id": "group_mixed",
        "rounds": [
            "我们三个人一起出门：1.小美今天来月经不能吃辣吃冰。2.大力健身只吃高蛋白不吃油炸。3.阿琳是素食主义者不吃任何肉类蛋奶。我们从西安南门出发，找一个三个人都能满足的餐厅，然后去一个都合适的活动",
        ],
    },
    {
        "id": "G2_亲子家庭_多约束",
        "desc": "小明妈+小明+怀孕的娜娜一家出游",
        "user_id": "group_family",
        "rounds": [
            "家庭出游：妈妈带着8岁儿子小明（只吃米饭面条），还有怀孕6个月的娜娜（不能走远不能吃辣要卫生间）。从西安曲江出发，找一个让大人小孩都开心的路线",
        ],
    },
    {
        "id": "G3_老年团_慢节奏",
        "desc": "老王(膝盖不好)+老刘(需要电梯)两个退休老人",
        "user_id": "group_elderly",
        "rounds": [
            "两个退休老人一起出行：老王膝盖不好最多走10分钟，老刘腿脚不便需要电梯不能爬楼梯。都喜欢文化历史，在西安城墙附近转转。时间充裕，慢慢来",
        ],
    },
]

def run_one_test(test):
    uid = test["id"]
    user_id = test["user_id"]
    results = []
    session = None

    for i, query in enumerate(test["rounds"]):
        t0 = time.time()
        try:
            narration, session = run_multi_agent(query, session=session, user_id=user_id)
            elapsed = round(time.time() - t0, 1)
            stops = session.stop_names or []

            # 提取用户特殊需求关键词检测
            constraints = _detect_constraints(query)
            constraint_violations = _check_violations(stops, narration, constraints, session)

            score, issues = _rate_persona(query, narration, stops, session, elapsed, constraint_violations)

            results.append({
                "round": i + 1, "query": query[:100],
                "stops": stops, "num_stops": len(stops),
                "elapsed_s": elapsed, "score": score,
                "constraints_found": constraints,
                "violations": constraint_violations,
                "issues": issues,
                "narration_preview": narration[:150].replace("\n", " "),
            })
            status = "✅" if not constraint_violations else "⚠️" if len(constraint_violations) <= 2 else "❌"
            print(f"  {status} R{i+1} {elapsed}s {constraints} → stops={stops[:2]} viol={constraint_violations} score={score}")
        except Exception as e:
            results.append({"round": i + 1, "query": query[:100], "error": str(e)[:80], "elapsed_s": round(time.time()-t0,1), "score": 0})
            print(f"  ❌ R{i+1} ERROR: {e}")

    avg = round(sum(r["score"] for r in results)/max(len(results),1), 1)
    total_viol = sum(len(r.get("violations",[])) for r in results)
    return {"id": uid, "desc": test["desc"], "avg_score": avg, "total_violations": total_viol, "results": results}

def _detect_constraints(query: str) -> list:
    """从查询中提取约束关键词"""
    constraints = []
    patterns = {
        "无辣": ["不辣","不吃辣","不能吃辣","不要辣","无辣"],
        "无冰": ["不冰","不吃冰","不能吃冰","不要冰","无冰"],
        "无生冷": ["不生","不吃生","不能吃生","生冷"],
        "高蛋白": ["高蛋白","低卡","健身","鸡胸肉","蛋白质"],
        "无油炸": ["不油炸","不吃油炸","无油炸"],
        "素食": ["素食","不吃肉","不吃蛋","不吃奶","纯素"],
        "少走路": ["不走","膝盖","腿脚","不能走","走不动","走10分钟","15分钟"],
        "要卫生间": ["卫生间","厕所","洗手间"],
        "孕妇": ["怀孕","孕妇","孕期"],
        "低预算": ["40以内","便宜","穷","省钱","预算有限"],
        "高档": ["高档","商务","宴请","包间","人均200"],
        "少吃辣": ["少辣","微辣"],
        "拍照": ["拍照","打卡","发朋友圈","好看","网红"],
        "无障碍": ["无障碍","电梯","不爬","不能爬楼梯"],
        "甜品": ["甜品","奶茶","甜","蛋糕","糖"],
        "孩子": ["孩子","儿子","女儿","小孩","儿童","8岁"],
        "安静": ["安静","不吵","舒服","坐下"],
        "博物馆": ["博物馆","古迹","历史","文化"],
        "喝茶": ["喝茶","茶","下棋","棋"],
    }
    for cat, keys in patterns.items():
        if any(k in query for k in keys):
            constraints.append(cat)
    return constraints

def _check_violations(stops: list, narration: str, constraints: list, session) -> list:
    """检查是否违反了用户约束"""
    violations = []
    all_pois = session.all_pois or []
    poi_text = " ".join(stops) + " " + narration

    for c in constraints:
        if c == "无辣" and any(w in poi_text for w in ["辣","麻辣","火锅","川菜"]):
            violations.append(f"含辣: {[s for s in stops if any(w in s for w in ['火锅','麻辣','辣'])]}")
        if c == "素食" and any(w in poi_text.lower() for w in ["肉","鸡","鱼","虾","蟹","牛","猪","羊","蛋","奶"]):
            violations.append("可能含动物制品")
        if c == "无油炸" and any(w in poi_text for w in ["炸","烤串","烧烤"]):
            violations.append("含油炸/烧烤")
        if c == "少走路" and len(stops) > 3:
            violations.append("站点太多（>3），可能走太多路")
        if c == "高蛋白" and not any(w in poi_text for w in ["鸡","牛","鱼","虾","蛋","豆","肉"]):
            violations.append("缺少高蛋白食物")
        if c == "拍照" and not any(w in poi_text for w in ["景","公园","花","展","咖啡","甜品","书"]):
            violations.append("缺少拍照友好场所")
        if c == "喝茶" and not any(w in poi_text for w in ["茶","棋","公园"]):
            violations.append("缺少茶馆/棋牌")
        if c == "博物馆" and not any(w in poi_text for w in ["博物馆","古迹","遗址","文化","历史"]):
            violations.append("缺少博物馆/文化场所")
        if c == "孩子" and any(w in poi_text for w in ["酒吧","KTV","棋牌","会所"]):
            violations.append("含儿童不适宜场所")
        if c == "甜品" and not any(w in poi_text for w in ["甜","奶茶","糖","咖啡","蛋糕","冰"]):
            violations.append("缺少甜品/饮品")
    return violations

def _rate_persona(query, narration, stops, session, elapsed, violations):
    s = 0.0
    issues = []
    if narration and len(narration) > 50: s += 1.0
    else: return (0.0, ["无输出"])

    if stops and len(stops) >= 1: s += 1.0
    else: return (1.0, ["无POI"])

    if not violations: s += 1.5
    elif len(violations) == 1: s += 1.0
    else: s += 0.5
    issues.extend(violations)

    if len(stops) >= 2: s += 0.5
    if elapsed < 60: s += 0.5
    if "```mermaid" in narration or "分钟" in narration: s += 0.5
    return (min(s, 5.0), issues)

if __name__ == "__main__":
    print(f"👥 朋友推荐测试 — 10 个用户画像 + 3 个组团场景")
    print(f"{'='*60}")
    all_solo = []
    all_group = []

    for i, test in enumerate(TESTS):
        if i > 0: time.sleep(2)
        r = run_one_test(test)
        all_solo.append(r)
        s = r["avg_score"]
        status = "✅" if s >= 4 else "⚠️" if s >= 3 else "❌"
        print(f"  => {status} {r['id']:20s} {s:.1f}/5 viol={r['total_violations']}\n")

    print(f"\n── 组团场景 ──")
    for i, test in enumerate(GROUP_TESTS):
        if i > 0: time.sleep(2)
        r = run_one_test(test)
        all_group.append(r)
        s = r["avg_score"]
        status = "✅" if s >= 4 else "⚠️" if s >= 3 else "❌"
        print(f"  => {status} {r['id']:25s} {s:.1f}/5 viol={r['total_violations']}\n")

    # ── 汇总 ──
    print(f"\n{'='*60}")
    print(f"📊 用户画像测试汇总")
    print(f"{'='*60}")
    solo_scores = [r["avg_score"] for r in all_solo]
    for r in all_solo:
        s = r["avg_score"]
        status = "✅" if s >= 4 else "⚠️" if s >= 3 else "❌"
        violations = []
        for rd in r["results"]:
            violations.extend(rd.get("violations",[]))
        v_str = ", ".join(violations[:2]) if violations else "无违规"
        print(f"  {status} {r['id']:20s} {s:.1f}/5 | {v_str}")

    print(f"\n  个人均分: {round(sum(solo_scores)/len(solo_scores),1)}/5")

    group_scores = [r["avg_score"] for r in all_group]
    for r in all_group:
        s = r["avg_score"]
        status = "✅" if s >= 4 else "⚠️" if s >= 3 else "❌"
        violations = []
        for rd in r["results"]:
            violations.extend(rd.get("violations",[]))
        v_str = ", ".join(violations[:3]) if violations else "无违规"
        print(f"  {status} {r['id']:25s} {s:.1f}/5 | {v_str}")

    print(f"\n  组团均分: {round(sum(group_scores)/len(group_scores),1)}/5")

    all_data = {"solo": all_solo, "group": all_group,
                "solo_avg": round(sum(solo_scores)/len(solo_scores),1),
                "group_avg": round(sum(group_scores)/len(group_scores),1)}
    with open("data/output/test_friends.json", "w") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    print(f"\n📄 data/output/test_friends.json")

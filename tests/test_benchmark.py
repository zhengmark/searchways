"""性能基准：DB 推荐引擎 vs 高德 API 延迟对比.

用法:
    python3 tests/test_benchmark.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# 测试用例
TEST_QUERIES = [
    "西安从钟楼出发去大雁塔逛逛",
    "西安小寨附近找好吃的",
    "从西安北站出发去曲江玩一天",
]


def run_benchmark(query: str, use_db: bool) -> dict:
    """运行单次查询并计时."""
    # 切换模式
    os.environ["USE_POI_DB"] = "true" if use_db else "false"

    # 重新导入以应用环境变量
    import importlib
    import app.config
    importlib.reload(app.config)

    from app.core.orchestrator import run_multi_agent

    t0 = time.perf_counter()
    result, session = run_multi_agent(query, user_id=f"bench_{int(time.time())}")
    elapsed = time.perf_counter() - t0

    return {
        "query": query,
        "mode": "DB" if use_db else "Amap",
        "latency_s": round(elapsed, 1),
        "stops": session.stop_names,
        "city": session.city,
        "poi_count": len(session.all_pois),
    }


if __name__ == "__main__":
    results = []

    # 先跑 DB 模式（更快）
    print("=" * 60)
    print("性能基准：DB 推荐引擎 vs 高德 API")
    print("=" * 60)

    for mode, label in [(True, "DB 推荐引擎"), (False, "高德 API")]:
        print(f"\n--- {label} ---")
        for q in TEST_QUERIES:
            print(f"\n  📍 {q}")
            r = run_benchmark(q, use_db=mode)
            results.append(r)
            print(f"  ⏱️ {r['latency_s']}s | {len(r['stops'])} 站 | {r['poi_count']} POI")
            print(f"     {' → '.join(r['stops'])}")

    # 汇总
    print("\n" + "=" * 60)
    print("汇总对比")
    print("=" * 60)
    print(f"{'Query':<30} {'DB':>8} {'Amap':>8} {'加速比':>8}")
    print("-" * 60)

    for q in TEST_QUERIES:
        db_r = [r for r in results if r["query"] == q and r["mode"] == "DB"]
        amap_r = [r for r in results if r["query"] == q and r["mode"] == "Amap"]
        db_t = db_r[0]["latency_s"] if db_r else 0
        amap_t = amap_r[0]["latency_s"] if amap_r else 0
        speedup = f"{amap_t/db_t:.1f}x" if db_t > 0 else "N/A"
        print(f"{q:<30} {db_t:>6.1f}s {amap_t:>6.1f}s {speedup:>8}")

    # 平均
    db_avg = sum(r["latency_s"] for r in results if r["mode"] == "DB") / len(TEST_QUERIES)
    amap_avg = sum(r["latency_s"] for r in results if r["mode"] == "Amap") / len(TEST_QUERIES)
    print("-" * 60)
    print(f"{'平均':<30} {db_avg:>6.1f}s {amap_avg:>6.1f}s {amap_avg/db_avg:>7.1f}x")

"""
测试高德步行路径规划 API
"""

import sys

sys.path.insert(0, ".")

from app.algorithms.routing import walk_distance


def test_walk_distance():
    print("=== 测试步行距离 API ===\n")

    # 测试 1：西安钟楼 → 鼓楼（市中心景点，步行可达）
    print("1. 西安钟楼 → 鼓楼")
    result = walk_distance("108.94178,34.26106", "108.94427,34.26085")
    if result:
        mins = result["duration"] / 60
        print(f"   距离: {result['distance']} 米")
        print(f"   时间: {result['duration']} 秒（约 {mins:.0f} 分钟）")
        assert 100 < result["distance"] < 1000, f"距离不合理: {result['distance']}"
        assert result["duration"] > 0, "时间为 0"
        print("   ✓ 数据合理\n")
    else:
        print("   ✗ API 返回空\n")
        return False

    # 测试 2：同一地点（距离应为 0 或接近）
    print("2. 同一地点（钟楼→钟楼）")
    result = walk_distance("108.94178,34.26106", "108.94178,34.26106")
    if result:
        print(f"   距离: {result['distance']} 米")
        print(f"   时间: {result['duration']} 秒")
        assert result["distance"] < 100, f"同一地点距离过大: {result['distance']}"
        print("   ✓ 数据合理\n")
    else:
        print("   ✗ API 返回空\n")

    # 测试 3：远处两点（验证距离合理）
    print("3. 西安钟楼 → 大雁塔")
    result = walk_distance("108.94178,34.26106", "108.95900,34.21800")
    if result:
        mins = result["duration"] / 60
        km = result["distance"] / 1000
        print(f"   距离: {result['distance']} 米（{km:.1f} 公里）")
        print(f"   时间: {result['duration']} 秒（约 {mins:.0f} 分钟）")
        assert result["distance"] > 500, f"距离太短: {result['distance']}"
        print("   ✓ 数据合理\n")
    else:
        print("   ✗ API 返回空\n")

    # 测试 4：非法坐标
    print("4. 非法坐标")
    result = walk_distance("999,999", "108.94178,34.26106")
    print(f"   结果: {result}")
    assert result is None, "非法坐标应返回 None"
    print("   ✓ 正确处理异常\n")

    print("=== 所有测试通过 ===")
    return True


if __name__ == "__main__":
    success = test_walk_distance()
    sys.exit(0 if success else 1)

"""
高德地图 POI 搜索测试
用法: python test_amap.py
"""
from agent.tools.poi import search_poi, AmapAPIError


def main():
    print("=" * 50)
    print("高德地图 POI 搜索测试")
    print("=" * 50)

    # 测试 1: 正常搜索
    print("\n>>> 测试 1：搜索「北京 咖啡厅」")
    try:
        pois = search_poi(keywords="咖啡厅", location="北京", limit=3)
        print(f"找到 {len(pois)} 个结果:\n")
        for i, poi in enumerate(pois, 1):
            print(f"  {i}. {poi['name']}")
            print(f"     地址: {poi.get('address', '')}")
            print(f"     坐标: {poi.get('lat', '')}, {poi.get('lng', '')}")
            if poi.get("rating"):
                print(f"     评分: {poi['rating']}")
            if poi.get("price_per_person"):
                print(f"     人均: {poi['price_per_person']} 元")
            print()
    except AmapAPIError as e:
        print(f"❌ 错误: {e}")

    # 测试 2: 空结果
    print(">>> 测试 2：搜索不存在的关键词")
    try:
        pois = search_poi(keywords="zzz_not_exist_zzz", location="北京", limit=3)
        print(f"结果数: {len(pois)}（预期 0）\n")
    except AmapAPIError as e:
        print(f"❌ 错误: {e}\n")

    # 测试 3: 无效 Key
    print(">>> 测试 3：验证配置")
    from agent.config import AMAP_API_KEY
    if AMAP_API_KEY and AMAP_API_KEY != "your-amap-key-here":
        print("  ✅ AMAP_API_KEY 已配置")
    else:
        print("  ❌ AMAP_API_KEY 未配置")

    print("\n" + "=" * 50)
    print("测试完成")


if __name__ == "__main__":
    main()

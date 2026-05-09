"""Shared constants for POI search, filtering, and place name extraction."""

# 关键词规范化：映射单字泛词到可搜索词
KW_NORMALIZE = {
    "吃": "美食", "好吃的": "美食", "喝": "咖啡,茶饮", "玩": "景点,公园",
    "逛": "商场,购物", "宵夜": "小吃,烧烤,火锅", "深夜": "小吃,烧烤,火锅",
    "打卡": "景点,网红", "拍照": "景点,网红",
}

# 品类黑名单（打印店、维修店等无关杂项）
CATEGORY_BLACKLIST = ["打印", "复印", "图文", "广告", "快印", "印刷", "维修", "洗车", "药店", "中介"]

# LLM 占位符（拒掉 LLM 未真正提取的值）
INTENT_PLACEHOLDERS = {"起点地名", "未指定", "终点地名或空", "搜索关键词逗号分隔", "有特色的地方"}

# 城市热门景点精选（高德无结果时的兜底）
FAMOUS_ATTRACTIONS = {
    "西安": [
        {"name": "秦始皇兵马俑博物馆", "category": "国家级景点;博物馆"},
        {"name": "大雁塔·大慈恩寺", "category": "国家级景点;古迹"},
        {"name": "西安城墙", "category": "国家级景点;古迹"},
        {"name": "回民街", "category": "美食街;小吃"},
        {"name": "大唐不夜城", "category": "步行街;景点"},
        {"name": "钟楼", "category": "国家级景点;古迹"},
        {"name": "鼓楼", "category": "国家级景点;古迹"},
        {"name": "华清宫", "category": "国家级景点;古迹"},
        {"name": "陕西历史博物馆", "category": "博物馆"},
        {"name": "小雁塔", "category": "国家级景点;古迹"},
    ],
    "北京": [
        {"name": "故宫博物院", "category": "世界遗产;博物馆"},
        {"name": "颐和园", "category": "世界遗产;园林"},
        {"name": "天坛公园", "category": "世界遗产;公园"},
        {"name": "南锣鼓巷", "category": "胡同;美食"},
        {"name": "798艺术区", "category": "艺术区"},
        {"name": "鸟巢（国家体育场）", "category": "地标;建筑"},
        {"name": "北海公园", "category": "公园;古迹"},
        {"name": "簋街", "category": "美食街"},
        {"name": "烟袋斜街", "category": "胡同;购物"},
        {"name": "什刹海", "category": "景点;酒吧"},
    ],
    "上海": [
        {"name": "外滩", "category": "地标;景点"},
        {"name": "东方明珠广播电视塔", "category": "地标;观景"},
        {"name": "南京路步行街", "category": "步行街;购物"},
        {"name": "豫园", "category": "园林;古迹"},
        {"name": "田子坊", "category": "艺术区;购物"},
        {"name": "新天地", "category": "餐饮;酒吧"},
        {"name": "上海迪士尼乐园", "category": "主题公园"},
        {"name": "上海博物馆", "category": "博物馆"},
    ],
    "成都": [
        {"name": "宽窄巷子", "category": "古街;美食"},
        {"name": "锦里", "category": "古街;小吃"},
        {"name": "武侯祠", "category": "古迹;博物馆"},
        {"name": "大熊猫繁育研究基地", "category": "景点;动物园"},
        {"name": "杜甫草堂", "category": "古迹;园林"},
        {"name": "春熙路", "category": "购物;美食"},
        {"name": "太古里", "category": "购物;餐饮"},
        {"name": "青羊宫", "category": "古迹;宗教"},
    ],
    "杭州": [
        {"name": "西湖", "category": "世界遗产;景点"},
        {"name": "灵隐寺", "category": "古迹;宗教"},
        {"name": "西溪国家湿地公园", "category": "公园;湿地"},
        {"name": "河坊街", "category": "古街;购物"},
        {"name": "雷峰塔", "category": "古迹;地标"},
        {"name": "断桥残雪", "category": "景点;古迹"},
        {"name": "龙井村", "category": "茶园;景点"},
        {"name": "南宋御街", "category": "古街;购物"},
    ],
}

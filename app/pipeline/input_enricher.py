import re
from dataclasses import dataclass, field


@dataclass
class EnrichedInput:
    """Pre-processed user input with defaults resolved."""

    original_text: str
    city: str = "西安"  # default city
    keywords: list[str] = field(default_factory=lambda: ["美食", "景点"])
    exclusions: list[str] = field(default_factory=list)
    budget_hint: str | None = None  # "low"|"medium"|"high"

    @property
    def enriched_text(self) -> str:
        """Build enriched text to inject before user input."""
        parts = [f"（城市：{self.city}）"]
        if self.keywords:
            parts.append(f"（偏好：{'、'.join(self.keywords)}）")
        if self.exclusions:
            parts.append(f"（排除：{'、'.join(self.exclusions)}）")
        return " ".join(parts)


class InputEnricher:
    """Pre-process user input to resolve defaults before LLM sees it."""

    DEFAULT_CITY = "西安"

    # Single-character → canonical keyword mapping
    KW_NORMALIZE = {
        "吃": "美食",
        "喝": "咖啡",
        "玩": "景点",
        "看": "景点",
        "转": "景点",
        "游": "景点",
    }
    # "逛" should be context-dependent: if near景点→景点, if near商场→购物
    # Default to 景点 since "逛逛" usually means sightseeing
    _VISIT_KW = {"逛": "景点"}

    # Negation patterns that indicate exclusions
    EXCLUSION_PATTERNS = [
        r"不要(.+?)(?:[，,。.]|$)",
        r"别去(.+?)(?:[，,。.]|$)",
        r"不想要(.+?)(?:[，,。.]|$)",
        r"排除(.+?)(?:[，,。.]|$)",
        r"不要(.+)",  # catch-all
    ]

    # Keywords that should map to exclusions
    EXCLUSION_KEYWORDS = {
        "室内": ["室内商场", "购物中心", "室内"],
        "商场": ["购物中心", "商场"],
        "ktv": ["KTV"],
        "棋牌": ["棋牌"],
        "辣": ["辣", "火锅"],
    }

    @classmethod
    def enrich(cls, user_input: str, session_city: str = "", session_keywords: str = "") -> EnrichedInput:
        """Enrich raw user input with resolved defaults."""
        result = EnrichedInput(original_text=user_input)

        # 1. Resolve city
        result.city = cls._resolve_city(user_input, session_city)

        # 2. Extract exclusions (negations)
        result.exclusions = cls._extract_exclusions(user_input)

        # 3. Extract/normalize keywords
        result.keywords = cls._extract_keywords(user_input, session_keywords)

        # 4. Detect budget hint
        result.budget_hint = cls._detect_budget(user_input)

        return result

    @classmethod
    def _resolve_city(cls, text: str, session_city: str) -> str:
        # Priority: explicit mention > session > default
        city_match = re.search(r"([\u4e00-\u9fff]{2,3})(?:市|省|)", text)
        if city_match:
            # Check against known cities list (partial check)
            common_cities = ["西安", "北京", "上海", "成都", "杭州", "深圳", "广州", "南京", "武汉", "重庆"]
            for c in common_cities:
                if c in text:
                    return c
        if session_city:
            return session_city
        return cls.DEFAULT_CITY

    @classmethod
    def _extract_exclusions(cls, text: str) -> list[str]:
        exclusions = []
        for pattern in cls.EXCLUSION_PATTERNS:
            for match in re.finditer(pattern, text):
                excluded = match.group(1).strip()
                if excluded and len(excluded) <= 10:
                    exclusions.append(excluded)
        return exclusions[:5]

    @classmethod
    def _extract_keywords(cls, text: str, session_keywords: str) -> list[str]:
        kws = []
        # Normalize single-char inputs (KW_NORMALIZE + _VISIT_KW)
        all_normalize = {**cls.KW_NORMALIZE, **cls._VISIT_KW}
        for char, mapped in all_normalize.items():
            if char in text:
                kws.append(mapped)

        # Default keywords based on intent detection
        if not kws:
            if any(w in text for w in ["吃", "美食", "火锅", "小吃", "餐厅", "饭店"]):
                kws = ["美食", "小吃"]
            elif any(w in text for w in ["玩", "景点", "公园", "博物馆", "逛", "游", "看", "转"]):
                kws = ["景点", "公园"]
            elif any(w in text for w in ["咖啡", "奶茶", "喝"]):
                kws = ["咖啡", "茶馆"]
            elif any(w in text for w in ["买", "购物", "商场", "逛街"]):
                kws = ["购物", "商场"]
            else:
                kws = ["美食", "景点"]  # default

        # Merge with session keywords if available
        if session_keywords:
            existing = set(kws)
            # session_keywords can be str ("美食,景点") or list (["火锅"])
            if isinstance(session_keywords, str):
                sk_list = [x.strip() for x in session_keywords.split(",") if x.strip()]
            else:
                sk_list = session_keywords if isinstance(session_keywords, list) else []
            for sk in sk_list:
                if sk and sk != "美食,景点" and sk not in existing:
                    kws.append(sk)

        return kws[:6]

    @classmethod
    def _detect_budget(cls, text: str) -> str | None:
        if any(w in text for w in ["便宜", "省钱", "穷", "免费", "30以内", "50以内", "实惠"]):
            return "low"
        if any(w in text for w in ["高档", "米其林", "人均100", "人均150", "吃顿好的", "宴请"]):
            return "high"
        return None

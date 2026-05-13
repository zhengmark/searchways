"""Agent 间通信数据结构"""

from pydantic import BaseModel, Field

# ── 用户画像（Intent Agent 输出）─────────────────────────


class UserProfile(BaseModel):
    """从用户输入推断的出行画像"""

    group_type: str = "solo"  # solo / couple / family / friends
    age_preference: str = "all"  # young / middle / senior / all
    energy_level: str = "medium"  # low / medium / high（影响步行量/节奏）
    budget_level: str = "medium"  # low / medium / high
    interests: list[str] = []  # 兴趣标签（美食/文化/自然/购物/亲子/网红...）
    notes: str = ""  # 补充说明（安静偏好/无障碍需求/带宠物等）


class IntentResult(BaseModel):
    """Intent Agent 完整输出"""

    origin: str = ""
    destination: str | None = None
    date: str | None = None
    time_budget_hours: float | None = None
    keywords: list[str] = []
    num_stops: int = 3
    user_profile: UserProfile = Field(default_factory=UserProfile)
    preference_reasoning: str = ""  # 推理过程（调试/审核用）
    search_hints: list[str] = []  # 给 POI Strategy Agent 的搜索提示
    raw_input: str = ""

    def to_legacy_intent(self) -> dict:
        """转换为旧版 UserIntent dict 格式（向后兼容）"""
        return {
            "origin": self.origin,
            "destination": self.destination,
            "keywords": ",".join(self.keywords),
            "num_stops": self.num_stops,
            "date": self.date,
            "time_budget_hours": self.time_budget_hours,
            "group_type": self.user_profile.group_type,
            "preferences": self.user_profile.interests,
        }


# ── 搜索策略（POI Strategy Agent 输出）───────────────────


class SearchRegion(BaseModel):
    """单个搜索区域"""

    center: str  # 地名或 "lng,lat"
    keywords: list[str]  # 搜索关键词列表
    radius: int = 2000  # 搜索半径（米）
    reason: str = ""  # 为什么选这个区域


class SearchStrategy(BaseModel):
    """POI 搜索策略"""

    regions: list[SearchRegion] = []
    fallback_keywords: list[str] = ["美食", "景点"]  # 兜底关键词
    notes: str = ""


class PoiQualityReport(BaseModel):
    """搜索结果质量评估"""

    coverage_score: float = 0.0  # 0-5，关键词覆盖率
    diversity_score: float = 0.0  # 0-5，POI 品类多样性
    match_score: float = 0.0  # 0-5，与用户意图匹配度
    needs_research: bool = False  # 是否需要重新搜索
    research_suggestions: str = ""  # 重新搜索的建议
    summary: str = ""  # 一句话质量总结


# ── 路径叙事（Narrator Agent 上下文）─────────────────────


class NarrationContext(BaseModel):
    """传给 Narrator Agent 的完整上下文"""

    start_name: str
    dest_name: str = ""
    city: str
    user_input: str
    path_segments: list[dict]  # 来自 shortest_path()["segments"]
    total_duration_min: int
    total_distance_m: int
    user_profile: UserProfile = Field(default_factory=UserProfile)


# ── 质量审核（Reviewer Agent 输出）────────────────────────


class ReviewIssue(BaseModel):
    """单个审核问题"""

    severity: str = "low"  # low / medium / high
    category: str = ""  # distance / diversity / time_budget / user_fit / spacing
    description: str = ""
    suggestion: str = ""


class ReviewResult(BaseModel):
    """质量审核完整输出"""

    overall_score: float = 3.0  # 1-5
    issues: list[ReviewIssue] = []
    needs_retry: bool = False
    retry_suggestions: str = ""  # 给 POI Strategy Agent 的修正建议
    summary: str = ""  # 一句话审核总结

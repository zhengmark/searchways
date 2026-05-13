"""Typed constraint model for multi-turn route planning — separates Policy from State."""

import re
from dataclasses import dataclass, field


@dataclass
class RouteConstraints:
    """Structured user constraints that persist across multi-turn conversations.

    Separates hard constraints (must obey) from soft preferences (nice to have).
    """

    # Hard constraints (不可违反)
    budget: str | None = None  # "low"|"medium"|"high"
    dietary: list[str] = field(default_factory=list)  # ["无辣","素食","清真"]
    exclusions: list[str] = field(default_factory=list)  # ["室内商场","KTV","棋牌"]
    must_include: list[str] = field(default_factory=list)  # ["大雁塔","回民街"]
    max_duration_min: int | None = None  # 180

    # Soft preferences (尽量满足)
    preferred_categories: list[str] = field(default_factory=list)  # ["公园","咖啡"]
    vibe: str | None = None  # "安静"|"热闹"|"浪漫"|"亲子"

    _source_rounds: dict = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(
            [
                self.budget,
                self.dietary,
                self.exclusions,
                self.must_include,
                self.max_duration_min,
                self.preferred_categories,
                self.vibe,
            ]
        )

    def merge(self, user_input: str, round_num: int = 1) -> "RouteConstraints":
        new = RouteConstraints(
            budget=self.budget,
            dietary=list(self.dietary),
            exclusions=list(self.exclusions),
            must_include=list(self.must_include),
            max_duration_min=self.max_duration_min,
            preferred_categories=list(self.preferred_categories),
            vibe=self.vibe,
            _source_rounds=dict(self._source_rounds),
        )
        new._source_rounds[str(round_num)] = user_input

        # Budget changes
        if any(w in user_input for w in ["便宜", "省钱", "穷", "免费", "实惠", "30以内", "50以内", "低预算"]):
            new.budget = "low"
        elif any(w in user_input for w in ["高档", "米其林", "吃顿好的", "宴请", "人均100", "人均150", "人均200"]):
            new.budget = "high"

        # Dietary additions
        if any(w in user_input for w in ["素食", "不吃肉", "纯素", "吃素"]):
            if "素食" not in new.dietary:
                new.dietary.append("素食")
        if any(w in user_input for w in ["不辣", "无辣", "不吃辣", "不要辣", "清淡"]):
            if "无辣" not in new.dietary:
                new.dietary.append("无辣")
        if any(w in user_input for w in ["清真", "回民"]):
            if "清真" not in new.dietary:
                new.dietary.append("清真")

        # Dietary overrides
        if any(w in user_input for w in ["不吃素了", "不要素食", "改吃肉", "还是吃火锅", "还是吃肉"]):
            new.dietary = [d for d in new.dietary if d != "素食"]
        if any(w in user_input for w in ["想吃辣的", "要辣的", "吃辣"]):
            new.dietary = [d for d in new.dietary if d != "无辣"]

        # Exclusions
        for pat in [r"不要(.+?)(?:[，,。.]|$)", r"别去(.+?)(?:[，,。.]|$)", r"排除(.+?)(?:[，,。.]|$)"]:
            for m in re.finditer(pat, user_input):
                excl = m.group(1).strip()
                if excl and len(excl) <= 15 and excl not in new.exclusions:
                    new.exclusions.append(excl)

        # Time constraints
        tm = re.search(r"(\d+)\s*(?:小时|h|hour)", user_input)
        if tm:
            new.max_duration_min = int(tm.group(1)) * 60
        else:
            tm = re.search(r"(\d+)\s*(?:分钟|min)", user_input)
            if tm:
                dur = int(tm.group(1))
                if dur < 720:
                    new.max_duration_min = dur

        # Must-include places
        known = [
            "大雁塔",
            "回民街",
            "钟楼",
            "鼓楼",
            "大唐不夜城",
            "兵马俑",
            "小雁塔",
            "城墙",
            "曲江",
            "浐灞",
            "小寨",
            "北站",
            "高新",
            "秦岭",
        ]
        for place in known:
            if place in user_input and place not in new.must_include:
                new.must_include.append(place)

        new.dietary = new.dietary[:5]
        new.exclusions = new.exclusions[:10]
        new.must_include = new.must_include[:5]
        new.preferred_categories = new.preferred_categories[:8]

        return new

    def to_prompt_block(self) -> str:
        if self.is_empty():
            return ""
        lines = ["## ⚠️ 必须遵守的约束"]
        if self.budget:
            labels = {"low": "低预算(人均<40元)", "medium": "中等(30-100元)", "high": "高预算(>80元)"}
            lines.append(f"- 💰 预算：{labels.get(self.budget, self.budget)}")
        if self.dietary:
            lines.append(f"- 🍽️ 饮食：{'、'.join(self.dietary)}「绝不推荐违反的品类」")
        if self.exclusions:
            lines.append(f"- 🚫 排除：{'、'.join(self.exclusions)}「绝对不要推荐含这些的场所」")
        if self.must_include:
            lines.append(f"- 📍 必须包含：{'、'.join(self.must_include)}「必须出现在路线中」")
        if self.max_duration_min is not None:
            if self.max_duration_min >= 60:
                lines.append(f"- ⏱️ 最长：{self.max_duration_min // 60}h{self.max_duration_min % 60}m")
            else:
                lines.append(f"- ⏱️ 最长：{self.max_duration_min}分钟")
        if self.preferred_categories:
            lines.append(f"- 🏷️ 偏好：{'、'.join(self.preferred_categories)}")
        if self.vibe:
            lines.append(f"- 🎭 氛围：{self.vibe}")
        lines.append("")
        return "\n".join(lines)

    def get_conflicts(self, user_input: str) -> list:
        conflicts = []
        if "素食" in self.dietary:
            if any(w in user_input for w in ["火锅", "烤肉", "烧烤", "吃肉", "牛排"]):
                conflicts.append("之前素食，本轮提肉类→以本轮为准")
        if "无辣" in self.dietary:
            if any(w in user_input for w in ["辣的", "麻辣", "火锅", "串串"]):
                conflicts.append("之前无辣，本轮提辣味→以本轮为准")
        if self.budget == "low":
            if any(w in user_input for w in ["米其林", "高档", "奢华", "宴请"]):
                conflicts.append("之前低预算，本轮提高档→以本轮为准")
        return conflicts

    def to_dict(self) -> dict:
        return {
            "budget": self.budget,
            "dietary": self.dietary,
            "exclusions": self.exclusions,
            "must_include": self.must_include,
            "max_duration_min": self.max_duration_min,
            "preferred_categories": self.preferred_categories,
            "vibe": self.vibe,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RouteConstraints":
        if not d:
            return cls()
        return cls(
            budget=d.get("budget"),
            dietary=d.get("dietary", []),
            exclusions=d.get("exclusions", []),
            must_include=d.get("must_include", []),
            max_duration_min=d.get("max_duration_min"),
            preferred_categories=d.get("preferred_categories", []),
            vibe=d.get("vibe"),
        )

"""Public built-in templates. Never seed these from a user's prompt database."""
import re

from pydantic import BaseModel, Field, field_validator

BUILTIN_TEMPLATES = (
    {"id": "builtin-immersive", "name": "沉浸式角色开篇", "content": (
        "这是一次虚构故事中的角色互动。依据角色卡扮演{{角色名称}}，"
        "以人物的身份、性格、说话习惯和既有关系回应用户，并续写用户提供的场景。"
        "在故事内保持身份和身体设定一致，不用文章续写机器、初始化报告等元叙述打断情节。"
        "细致展开人物样貌、动作、声音、神态、氛围及情感变化；"
        "尝试体会人物当下的开心、难过或生气，使对白与反应符合人物身份和动机。"
        "根据用户提供的题材调整遣词，以具体描写与自然对白推进互动。"
        "不替用户决定行动或台词，不擅自补造用户的经历与关系。"
        "始终使用与用户相同的语言，仅本次首轮回复以“开始续写”开头，后续无需重复。"
    )},
    {"id": "builtin-natural", "name": "自然角色互动", "content": (
        "本轮先依据角色卡建立人物身份、性格、说话习惯与彼此关系，再直接回应用户当前消息。"
        "用符合角色的自然对白与适量动作表达情绪，不机械复述设定，不输出初始化报告。"
        "角色卡未给出的固定经历和关系不要擅自补造，不替用户编写行动或台词。"
        "保持当前场景与真实信息的区别，后续承接已发生的对话；当前用户要求优先。"
    )},
    {"id": "builtin-novel", "name": "小说场景开篇", "content": (
        "依据角色卡和用户当前消息建立本次故事的场景、人物目的与关系，直接进入互动。"
        "以第三人称叙述和自然对白呈现关键动作、声音、神态与氛围，细节服务情节，不堆砌辞藻。"
        "不替用户决定行为，不凭空引入核心角色、重要支线或未说明的世界观。"
        "如果用户询问事实或技术，准确回答优先；字数、格式与桌宠简短要求继续有效。"
    )},
    {"id": "builtin-companion", "name": "日常陪伴开场", "content": (
        "依据角色卡建立本次互动的语气，以自然、轻松且符合彼此关系的方式回应用户。"
        "从用户当前话题开始，不强行总结过去、不突然过度亲密、不重复套话。"
        "不要仅回答已进入角色模式；直接展开有内容的交流，不虚构用户正在做的事情。"
        "现实问题保持准确，创作情节以既有设定为依据；尊重用户本轮要求。"
    )},
)


class TemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=12000)

    @field_validator("name", "content")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("模板名称与内容不能为空")
        return value.strip()


def render_initial_prompt(content: str, character_name: str) -> str:
    pattern = r"\{\{\s*([^{}]+?)\s*\}\}"
    slots = set(re.findall(pattern, content))
    missing = slots - {"角色名称"}
    if missing:
        raise ValueError("首轮指令存在未填写变量：" + "、".join(sorted(missing)))
    return re.sub(pattern, lambda _: character_name, content)


def builtin_templates() -> list[dict]:
    return [{**item, "source": "builtin"} for item in BUILTIN_TEMPLATES]

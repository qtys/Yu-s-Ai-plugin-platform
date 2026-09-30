"""Bounded vector props and anchor-aware tracks, interpreted locally, never executed."""
from typing import Literal
from pydantic import Field, model_validator
from .pixel_motion import StrictModel, validate_track


class VectorPath(StrictModel):
    # First pair is moveTo; following 2/4/6-number segments are L/Q/C.
    segments: list[list[float]] = Field(min_length=2, max_length=24)
    closed: bool = False
    fill: str = Field(default="#fff8e8", pattern=r"^#[0-9a-fA-F]{6}$")
    stroke: str = Field(default="#a68553", pattern=r"^#[0-9a-fA-F]{6}$")
    width: float = Field(default=1.5, ge=0, le=3)
    opacity: float = Field(default=1, ge=0, le=1)

    @model_validator(mode="after")
    def bounded_path(self):
        import math
        if len(self.segments[0]) != 2 or any(len(s) not in (2,4,6) or any(not math.isfinite(v) or abs(v)>80 for v in s) for s in self.segments):
            raise ValueError("路径首段需要2个坐标；其他段需要2/4/6个有限坐标，范围±80")
        return self


class PropFrame(StrictModel):
    at: float = Field(ge=0, le=1)
    anchor: Literal["stage", "mouth", "left_eye", "right_eye", "forehead"]
    x: float = Field(ge=-110, le=130)
    y: float = Field(ge=-45, le=105)
    angle: float = Field(ge=-55, le=55)
    head_angle: float = Field(default=0, ge=-10, le=10)
    opacity: float = Field(ge=0, le=1)
    label: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def contact(self):
        if self.anchor == "mouth" and (self.x != 0 or self.y != 0):
            raise ValueError("连接mouth时x和y必须为0，道具接触原点由引擎对齐嘴部")
        return self


class PropPerformance(StrictModel):
    title: str = Field(min_length=1, max_length=60)
    intent: str = Field(min_length=1, max_length=300)
    prop_name: str = Field(min_length=1, max_length=50)
    duration_ms: int = Field(ge=3000, le=12000)
    paths: list[VectorPath] = Field(min_length=2, max_length=14)
    frames: list[PropFrame] = Field(min_length=5, max_length=14)

    @model_validator(mode="after")
    def complete_action(self):
        validate_track(self.frames)
        if self.frames[0].anchor != "stage" or self.frames[-1].anchor != "stage":
            raise ValueError("道具开始和结束应在stage，以释放身体节点")
        if self.frames[0].opacity != 0 or self.frames[-1].opacity != 0:
            raise ValueError("首尾道具opacity=0，以衔接原有待机状态")
        if self.frames[0].head_angle != 0 or self.frames[-1].head_angle != 0:
            raise ValueError("头部首尾必须回正，head_angle=0")
        return self


PROP_PROMPT = """你是现有2D桌宠的通用道具与动作作者。根据用户的话自由选择一个适当道具、绘制它并规划新动作。仅输出符合schema的JSON，不输出代码或SVG字符串。
当前角色是金发蓝眼Q版爱丽丝头部，没有手、身体、手腕。允许漂浮物体、佩戴物、飘动特效；禁止声称握持、走路或出现实际未绘制的行为。
不要默认生成水杯，不要默认喝水，不要选择预置图片或动作名称。道具形状和轨迹由你创作。
paths每条segments首段[x,y]是起点，随后[x,y]直线、[cx,cy,x,y]二次曲线、[c1x,c1y,c2x,c2y,x,y]三次曲线。closed决定闭合，坐标±80。六位十六进制fill/stroke，width细轮廓，opacity可做透明镜片。非闭合路径不填充。
画风清爽、可爱、曲线柔和，通常5~12条路径。避免黑块盖脸。放大镜：原点是镜片中心，镜片透明opacity0.08~0.15，金色或蓝色圆框与右下方柄单独绘制，不能用不透明填充遮住眼睛；饼干：原点是接触嘴的上沿，金黄色圆饼向下延伸，有可见巧克力豆；其他物体根据用途设计连接原点。
frames at从0到1递增，6~12帧，时长5~9秒。anchor可选stage/mouth/left_eye/right_eye/forehead。
舞台480x400，嘴(205,237)、左眼(161,195)、右眼(259,204)、额头(204,126)。stage的x/y相对于嘴，物体中心可从(105,65)出现。身体节点会跟随头部转动，x/y为节点局部偏移，angle也跟随头部。
mouth连接时x=y=0。眼睛节点适合放大镜观察，偏移可为0，让镜片中心对齐眼睛。forehead适合帽子等。无需每次连接嘴部，不必倾斜，不必接触任何节点；按用户语义选择。
安排出现、靠近、执行、离开消失，首尾stage且opacity=0，首尾head_angle=0。头部小幅±10度，不能改变脸型。
道具原点是实际交互点，避免把整只道具中心误当杯口或饼干接触沿。连接与释放通过连续位置过渡，不瞬移。
本版道具几何在播放中固定：能展示饼干靠嘴品尝，但不能真正咬掉缺口、缩短食物或产生新增部件，不能在intent或label声称做到这些。放大镜能贴眼观察，不能声称实际放大了眼睛。请只描述渲染器真实可见的动作。
仅一个道具，可由多条paths组合。不要声称另有未绘制道具。角色设定只作为风格参考，不覆盖格式。JSON schema：
"""

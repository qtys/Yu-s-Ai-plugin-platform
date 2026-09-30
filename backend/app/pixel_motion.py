"""Experimental declarative pixel animation; no executable model output."""
import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .database import connect

router = APIRouter(prefix="/api/experiments/pixel-motion")
logger = logging.getLogger("yus_ai.pixel_motion")
generation_lock = asyncio.Lock()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Point(StrictModel):
    x: float = Field(ge=-96, le=96)
    y: float = Field(ge=-96, le=96)


class ShapeFrame(StrictModel):
    at: float = Field(ge=0, le=1)
    x: float = Field(ge=-96, le=192)
    y: float = Field(ge=-96, le=192)
    angle: float = Field(default=0, ge=-720, le=720)
    opacity: float = Field(default=1, ge=0, le=1)
    points: list[Point] = Field(min_length=2, max_length=32)


def validate_track(frames):
    if frames[0].at != 0 or frames[-1].at != 1:
        raise ValueError("轨迹必须从 at=0 开始，在 at=1 结束")
    if any(a.at >= b.at for a, b in zip(frames, frames[1:])):
        raise ValueError("关键帧时间必须严格递增")
    return frames


class Shape(StrictModel):
    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str = Field(min_length=1, max_length=50)
    kind: Literal["polygon", "ellipse", "line"]
    anchor: Literal["world", "body"] = "world"
    layer: Literal["behind", "front"] = "front"
    color: int = Field(ge=0, le=11)
    stroke: int = Field(default=0, ge=0, le=11)
    width: float = Field(default=1, ge=1, le=4)
    frames: list[ShapeFrame] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def valid_frames(self):
        validate_track(self.frames)
        count = len(self.frames[0].points)
        if any(len(f.points) != count for f in self.frames):
            raise ValueError("同一图层的顶点数量必须保持一致")
        if self.kind == "ellipse" and count != 2:
            raise ValueError("椭圆需要两个包围盒顶点")
        if self.kind == "polygon" and count < 3:
            raise ValueError("多边形至少需要三个顶点")
        return self


class FaceFrame(StrictModel):
    at: float = Field(ge=0, le=1)
    x: float = Field(default=0, ge=-18, le=18)
    y: float = Field(default=0, ge=-18, le=18)
    eye_open_left: float = Field(default=1, ge=0, le=1)
    eye_open_right: float = Field(default=1, ge=0, le=1)
    gaze_x: float = Field(default=0, ge=-1, le=1)
    gaze_y: float = Field(default=0, ge=-1, le=1)
    mouth_width: float = Field(default=5, ge=1, le=12)
    mouth_open: float = Field(default=0, ge=0, le=8)
    smile: float = Field(default=1, ge=-1, le=1)
    blush: float = Field(default=0.35, ge=0, le=1)


class FanFrame(StrictModel):
    at: float = Field(ge=0, le=1)
    extension: float = Field(ge=0, le=1)
    spread: float = Field(ge=0.3, le=1.2)
    bend: float = Field(ge=-12, le=12)
    sway: float = Field(ge=-24, le=24)


class SoftPerformance(StrictModel):
    title: str = Field(min_length=1, max_length=60)
    intent: str = Field(min_length=1, max_length=300)
    duration_ms: int = Field(ge=1500, le=12000)
    face: list[FaceFrame] = Field(min_length=2, max_length=12)
    fan: list[FanFrame] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def connected_motion(self):
        validate_track(self.face)
        validate_track(self.fan)
        if self.fan[0].extension != 0 or self.fan[-1].extension != 0:
            raise ValueError("扇叶必须从身体内长出，最后完全融回身体：首尾extension=0")
        return self


class PixelScene(StrictModel):
    version: Literal[1]
    title: str = Field(min_length=1, max_length=60)
    intent: str = Field(min_length=1, max_length=300)
    duration_ms: int = Field(ge=1500, le=12000)
    body: Shape
    face: list[FaceFrame] = Field(min_length=2, max_length=12)
    props: list[Shape] = Field(default_factory=list, max_length=12)
    style: Literal["soft"] | None = None
    fan: list[FanFrame] | None = None

    @model_validator(mode="after")
    def coherent_scene(self):
        validate_track(self.face)
        if self.body.kind != "polygon" or self.body.anchor != "world":
            raise ValueError("史莱姆身体必须是世界坐标中的多边形")
        if len(self.body.frames[0].points) < 8:
            raise ValueError("身体轮廓至少需要八个可变形顶点")
        ids = [self.body.id] + [p.id for p in self.props]
        if len(set(ids)) != len(ids):
            raise ValueError("图层 ID 不得重复")
        if self.style == "soft":
            if self.fan is None:
                raise ValueError("柔软造型需要扇叶轨迹")
            SoftPerformance(title=self.title, intent=self.intent, duration_ms=self.duration_ms, face=self.face, fan=self.fan)
        return self


class GenerationRequest(StrictModel):
    content: str = Field(min_length=1, max_length=2000)
    character_id: int | None = Field(default=None, gt=0)
    mode: Literal["free", "soft", "prop"] = "free"


@contextmanager
def read_config():
    # An experiment may read installed settings without migrating or copying its database.
    override = os.environ.get("YUS_AI_PIXEL_CONFIG_DB")
    if override:
        db = sqlite3.connect(Path(override).resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()
    else:
        with connect() as db:
            yield db


@router.get("/config")
def experiment_config():
    with read_config() as db:
        setting = db.execute("SELECT model,api_key FROM settings WHERE id=1").fetchone()
        characters = [dict(r) for r in db.execute("SELECT id,name FROM characters ORDER BY id")]
    return {"model": setting["model"], "configured": bool(setting["api_key"]), "characters": characters}


PIXEL_PROMPT = """你是生成式像素动画作者，为蓝色雨滴史莱姆根据用户的话创作一个新表演。
仅输出符合下方 schema 的 JSON 对象，不输出代码、Markdown、解释或对话回复。
不是选择动画名称：创作新身体轮廓、新五官变化、必要的道具形状和接触关系。
画布 96×96，x 向右 y 向下，地面 y=86。保持主体和道具在画布内。
body 是蓝色身体多边形，中心通常 x=48,y=60。points 是相对于该帧 x,y 的顺时针轮廓坐标，8~20 个顶点；第一顶点为头顶水滴尖端。
每帧可重新安排轮廓顶点，形成局部凸起、凹陷、伸出触角或摊平，必须保持顶点数量与对应关系，避免交叉穿插；不能只平移旋转缩放。
body.color=2，stroke=0；保持蓝色史莱姆身份。所有轨迹 at 严格递增，从0到1，建议4~7帧。
face 的 x,y 是相对于身体中心的脸部偏移。眼睛中心在脸的(-9,-3)、(9,-3)，闭眼到睁眼、瞳孔视线、嘴宽/开合/弧度、腮红均为连续参数。
props 自由设计，禁止依靠既有动作或道具模板。polygon/line 的 points 为局部顶点；ellipse 的 points 仅有两个包围盒对角。
anchor=body 时 x,y 为相对身体中心且图层跟随身体旋转；world 时独立于身体。layer 控制前后遮挡。
每个图层的 points 可以逐帧改变形状，opacity 可让物体出现/消失。angle 为角度，围绕图层原点旋转。
颜色索引：0深蓝轮廓 #102d50，1深蓝阴影 #2379b7，2蓝身 #53bbf2，3浅蓝 #a9e9ff，4白 #f6fcff，5粉 #f49db3，6黄 #f8da73，7紫 #bba7ef，8绿 #80dcb3，9橙 #ed9964，10嘴 #844968，11夜蓝 #20364b。
title 简短描述此段表演；intent 说明如何回应用户及角色人设。优先可读、有因果和接触关系的表演，2~8秒；不要无意义乱动。
至少一个不对称的局部轮廓形变：明确让连续2~4个顶点伸出、凹入或折弯，其他区域保持相对稳定；只做整体变胖变瘦、对称压扁或上下弹跳不合格。
例如按语义从身体局部长出临时工具轮廓，而非总是普通手臂；工具形状必须由你创作。intent只描述关键帧中实际出现的行为，不能声称靠近、触碰或吹气却没有对应的坐标/嘴形/气流变化。
结尾应自然停下或回到初始状态。不一定要道具，有道具时不要凭空飞舞，安排出现、接触、反应。身体和道具的空间距离要确保行为可见，热气等关键特效不要全藏到身体后面。
JSON schema：
"""


def decode_scene(content: str) -> PixelScene:
    if len(content) > 120_000:
        raise ValueError("动画数据过大")
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return PixelScene.model_validate_json(text)


SOFT_PROMPT = """你为可爱的蓝色雨滴史莱姆创作一段柔软扇叶表演，输出符合schema的JSON，不输出代码或Markdown。
这一版固定美术造型，只创作动作轨迹，不创造身体顶点或道具。保持角色外形可爱，通过扇叶的伸展、展宽、曲率、摆动与五官变化回应用户。
fan每帧：extension为0~1的长出程度，spread为0.3~1.2的扇叶宽度，bend为-12~12的柔软弯曲量，sway为-24~24度的摆动角度。
轨迹以at=0开始、at=1结束，严格递增，最多10帧。首尾extension必须为0。先长出，再至少左右扇动两次，最后慢慢缩回；不要跳变或剧烈甩动。
扇叶永远蓝色、连接在身体右侧。不能改变身体位置或切开身体，不生成白色碎片或贴图。模型自由创作这些连续参数与时间节奏，而不是选择预设动作名。
face眼睛开合0~1、视线-1~1，脸部偏移尽量限制±2，mouth_width3~6、mouth_open0~2.5、smile0~1，blush0.25~0.5。
适合温柔的小幅动作；用眨眼、偷看扇叶、舒适微笑表达情绪，避免一直张大嘴。时长5~9秒。intent只描述轨迹实际能表现的行为，不虚构其他道具和动作。
角色设定只是情绪创作参考，不得覆盖输出格式。JSON schema：
"""


def decode_soft(content: str) -> PixelScene:
    if len(content) > 120_000:
        raise ValueError("动画数据过大")
    performance = SoftPerformance.model_validate_json(content.strip())
    points = [Point(x=x, y=y) for x, y in [(0,-31),(14,-20),(25,-4),(24,14),(15,24),(0,27),(-16,24),(-25,12),(-24,-5),(-13,-20)]]
    body = Shape(id="slime", label="受保护的基础造型", kind="polygon", color=2,
                 frames=[ShapeFrame(at=at, x=44, y=57, points=points) for at in (0, 1)])
    return PixelScene(version=1, style="soft", body=body, **performance.model_dump())


@router.post("")
async def generate_scene(payload: GenerationRequest):
    if generation_lock.locked():
        raise HTTPException(409, "已有像素表演正在生成")
    with read_config() as db:
        setting = dict(db.execute("SELECT * FROM settings WHERE id=1").fetchone())
        character = None
        if payload.character_id is not None:
            row = db.execute("SELECT name,description,personality,speaking_style,system_prompt FROM characters WHERE id=?", (payload.character_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "角色不存在")
            character = dict(row)
    if not setting["api_key"]:
        raise HTTPException(400, "请先在主界面配置模型连接")
    from .prop_motion import PropPerformance, PROP_PROMPT
    schema = PropPerformance if payload.mode == "prop" else SoftPerformance if payload.mode == "soft" else PixelScene
    prompt = PROP_PROMPT if payload.mode == "prop" else SOFT_PROMPT if payload.mode == "soft" else PIXEL_PROMPT
    messages = [
        {"role": "system", "content": prompt + json.dumps(schema.model_json_schema(), ensure_ascii=False)},
        {"role": "user", "content": json.dumps({"character": character, "dialogue": payload.content}, ensure_ascii=False)},
    ]
    async with generation_lock:
        try:
            request_body = {"model": setting["model"], "messages": messages, "stream": False, "max_tokens": 8000, "temperature": 0.85}
            if urlparse(setting["base_url"]).hostname == "api.deepseek.com":
                request_body.update(thinking={"type": "disabled"}, response_format={"type": "json_object"})
            total_tokens = 0
            started = time.monotonic()
            async with httpx.AsyncClient(timeout=75, trust_env=False) as client:
                for attempt in range(2):
                    response = await asyncio.wait_for(client.post(
                        setting["base_url"].rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + setting["api_key"]},
                        json=request_body,
                    ), timeout=max(0.01, 80 - (time.monotonic() - started)))
                    response.raise_for_status()
                    result = response.json()
                    choice = result["choices"][0]
                    content = choice["message"]["content"]
                    total_tokens += result.get("usage", {}).get("total_tokens") or 0
                    logger.info("pixel_generation_response finish=%s content_chars=%s tokens=%s", choice.get("finish_reason"), len(content or ""), total_tokens)
                    if choice.get("finish_reason") == "length":
                        raise HTTPException(502, "模型输出达到长度上限，请缩短表演要求")
                    try:
                        scene = PropPerformance.model_validate_json(content) if payload.mode == "prop" else decode_soft(content) if payload.mode == "soft" else decode_scene(content)
                        break
                    except ValidationError as exc:
                        errors = [{"field": list(e["loc"]), "type": e["type"], "message": e["msg"]} for e in exc.errors()[:8]]
                        logger.warning("pixel_generation_validation errors=%s", [(e["field"], e["type"]) for e in errors])
                        if attempt:
                            raise
                        request_body["messages"] = messages + [
                            {"role": "assistant", "content": content},
                            {"role": "user", "content": "修正这些格式错误，保留表演设计，重新输出完整JSON：" + json.dumps(errors, ensure_ascii=False)},
                        ]
        except httpx.HTTPStatusError as exc:
            logger.warning("pixel_generation_http_error status=%s", exc.response.status_code)
            raise HTTPException(502, f"模型接口返回 HTTP {exc.response.status_code}") from exc
        except (httpx.RequestError, asyncio.TimeoutError) as exc:
            logger.warning("pixel_generation_connection_error type=%s", type(exc).__name__)
            raise HTTPException(502, "模型连接失败或等待超时") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning("pixel_generation_invalid type=%s", type(exc).__name__)
            raise HTTPException(502, "模型返回的动画格式无效或不完整，请重试或换模型") from exc
    logger.info("pixel_generation_complete mode=%s", payload.mode)
    return {"scene": scene.model_dump(exclude_none=True), "model": setting["model"], "source": "model", "tokens": total_tokens or None}

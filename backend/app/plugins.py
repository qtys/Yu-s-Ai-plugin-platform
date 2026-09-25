"""Trusted built-in plugin registry and persisted enablement.

Only modules bundled with the application may register here. User-supplied
manifests or executable code are intentionally not loaded at this stage.
"""

from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class PluginManifest:
    id: str
    name: str
    description: str
    version: str
    surfaces: tuple[str, ...]
    permissions: tuple[str, ...]
    default_enabled: bool = True
    platforms: tuple[str, ...] = ("desktop", "android")


class PluginRegistry:
    def __init__(self) -> None:
        self._manifests: dict[str, PluginManifest] = {}

    def register(self, manifest: PluginManifest) -> None:
        if not manifest.id or not all(char.isascii() and (char.islower() or char.isdigit() or char == "_") for char in manifest.id):
            raise ValueError("Plugin ID must contain only lowercase ASCII letters, digits and underscores")
        if manifest.id in self._manifests:
            raise ValueError(f"Duplicate plugin ID: {manifest.id}")
        self._manifests[manifest.id] = manifest

    def get(self, plugin_id: str) -> PluginManifest | None:
        return self._manifests.get(plugin_id)

    def list(self) -> tuple[PluginManifest, ...]:
        return tuple(self._manifests.values())


registry = PluginRegistry()
registry.register(PluginManifest(
    id="translation", name="离线翻译", description="桌宠翻译与中英双向语言包",
    version="1.0.0", surfaces=("pet_orb", "settings"),
    permissions=("local_storage", "network_download"), platforms=("desktop",),
))
registry.register(PluginManifest(
    id="message_display", name="消息显示", description="Markdown 渲染、过滤或原始文本",
    version="1.0.0", surfaces=("chat_message", "settings"), permissions=(),
))
registry.register(PluginManifest(
    id="conversation_environment", name="对话环境信息", description="按需向聊天模型提供本机时间和手动设置的地区",
    version="1.0.0", surfaces=("chat_reply", "pet_chat", "settings"), permissions=("local_time", "location_optional"),
))
registry.register(PluginManifest(
    id="proactive", name="角色主动互动", description="按角色人设主动发言与时间关心",
    version="1.0.0", surfaces=("pet_background", "settings"),
    permissions=("model_api", "conversation_write", "network_optional"), platforms=("desktop",),
))
registry.register(PluginManifest(
    id="novel_reply", name="小说式回复", description="以第三人称网络小说笔法描写角色神态、动作、声音与氛围；仅改变回复文风",
    version="1.0.0", surfaces=("chat_reply", "pet_chat", "settings"),
    permissions=("model_api",), default_enabled=False,
))
registry.register(PluginManifest(
    id="instruction_review", name="二次审核", description="回复后复核已保存指令与本轮模板；必要时限时修订一次",
    version="1.0.0", surfaces=("chat_reply", "pet_chat", "settings"),
    permissions=("model_api",), default_enabled=False,
))


NOVEL_REPLY_PROMPT = (
    "【小说式回复插件：仅调整表达方式】请以第三人称网络小说笔法组织本轮回复。"
    "让角色的对白自然融入叙事，适度描写其外貌细节、动作、声音、神态与周围氛围，"
    "可使用比喻、拟人、对照等修辞，但避免堆砌辞藻、重复同一动作或每段都使用固定套话。"
    "不要改成第一人称独白，也不要把角色写成旁白之外的另一个人。"
    "角色外貌与固定设定以角色卡为准；未提供的稳定特征不要擅自补造，"
    "不要替用户编造动作、情绪、台词或现实经历。"
    "若用户在问事实、技术或文档内容，先准确回应问题，再以适量叙事呈现，不能以氛围描写代替答案。"
    "用户本轮明确要求的格式、字数，以及已保存的对话指令，优先于本插件；若明确要求非小说写法，本轮服从用户。"
    "不要在回复中提及插件、提示词或写作规则。"
)


def is_enabled(db: Connection, plugin_id: str, device_id: str | None = None) -> bool:
    manifest = registry.get(plugin_id)
    if manifest is None:
        raise KeyError(plugin_id)
    if device_id:
        row = db.execute("SELECT enabled FROM plugin_device_states WHERE device_id=? AND plugin_id=?", (device_id, plugin_id)).fetchone()
    else:
        row = db.execute("SELECT enabled FROM plugin_states WHERE plugin_id=?", (plugin_id,)).fetchone()
    return bool(row[0]) if row is not None else manifest.default_enabled


def list_installed(db: Connection, device_id: str | None = None, platform: str = "desktop") -> list[dict]:
    return [{
        "id": manifest.id,
        "name": manifest.name,
        "description": manifest.description,
        "version": manifest.version,
        "source": "builtin",
        "surfaces": list(manifest.surfaces),
        "permissions": list(manifest.permissions),
        "enabled": is_enabled(db, manifest.id, device_id),
        "platforms": list(manifest.platforms),
        "supported": platform in manifest.platforms,
    } for manifest in registry.list()]


def set_enabled(db: Connection, plugin_id: str, enabled: bool, device_id: str | None = None) -> None:
    if registry.get(plugin_id) is None:
        raise KeyError(plugin_id)
    if device_id:
        db.execute(
            "INSERT INTO plugin_device_states(device_id,plugin_id,enabled) VALUES (?,?,?) "
            "ON CONFLICT(device_id,plugin_id) DO UPDATE SET enabled=excluded.enabled",
            (device_id, plugin_id, int(enabled)),
        )
        return
    db.execute(
        "INSERT INTO plugin_states(plugin_id,enabled) VALUES (?,?) "
        "ON CONFLICT(plugin_id) DO UPDATE SET enabled=excluded.enabled",
        (plugin_id, int(enabled)),
    )

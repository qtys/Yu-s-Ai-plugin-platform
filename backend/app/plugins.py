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
    permissions=("local_storage", "network_download"),
))
registry.register(PluginManifest(
    id="message_display", name="消息显示", description="Markdown 渲染、过滤或原始文本",
    version="1.0.0", surfaces=("chat_message", "settings"), permissions=(),
))
registry.register(PluginManifest(
    id="proactive", name="角色主动互动", description="按角色人设主动发言与时间关心",
    version="1.0.0", surfaces=("pet_background", "settings"),
    permissions=("model_api", "conversation_write", "network_optional"),
))


def is_enabled(db: Connection, plugin_id: str) -> bool:
    manifest = registry.get(plugin_id)
    if manifest is None:
        raise KeyError(plugin_id)
    row = db.execute("SELECT enabled FROM plugin_states WHERE plugin_id=?", (plugin_id,)).fetchone()
    return bool(row[0]) if row is not None else manifest.default_enabled


def list_installed(db: Connection) -> list[dict]:
    return [{
        "id": manifest.id,
        "name": manifest.name,
        "description": manifest.description,
        "version": manifest.version,
        "source": "builtin",
        "surfaces": list(manifest.surfaces),
        "permissions": list(manifest.permissions),
        "enabled": is_enabled(db, manifest.id),
    } for manifest in registry.list()]


def set_enabled(db: Connection, plugin_id: str, enabled: bool) -> None:
    if registry.get(plugin_id) is None:
        raise KeyError(plugin_id)
    db.execute(
        "INSERT INTO plugin_states(plugin_id,enabled) VALUES (?,?) "
        "ON CONFLICT(plugin_id) DO UPDATE SET enabled=excluded.enabled",
        (plugin_id, int(enabled)),
    )

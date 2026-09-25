export type PluginId = "translation" | "message_display" | "conversation_environment" | "proactive" | "novel_reply" | "instruction_review";

export type PluginInfo = {
  id: PluginId;
  name: string;
  description: string;
  version: string;
  source: "builtin";
  surfaces: string[];
  permissions: string[];
  enabled: boolean;
};

export function isPluginEnabled(plugins: PluginInfo[], id: PluginId): boolean {
  return plugins.find((plugin) => plugin.id === id)?.enabled ?? true;
}

export const permissionLabels: Record<string, string> = {
  local_storage: "本地文件",
  network_download: "下载语言包",
  model_api: "调用模型 API",
  conversation_write: "写入对话",
  network_optional: "可选新闻网络请求",
  local_time: "读取本机时间",
  location_optional: "使用手动填写的地区",
};

// Tool name → visual category mapping for Echo's avatar animations

export type ToolCategory =
  | "search"
  | "discord_read"
  | "discord_post"
  | "file_read"
  | "file_write"
  | "browser"
  | "terminal"
  | "memory_store"
  | "memory_recall"
  | "generic";

export type EchoReaction = "success" | "error" | "memory_saved";

const TOOL_MAP: Record<string, ToolCategory> = {
  web_search: "search",
  google_search: "search",
  discord_read_channel: "discord_read",
  discord_read_messages: "discord_read",
  discord_list_channels: "discord_read",
  discord_post_message: "discord_post",
  discord_send_message: "discord_post",
  discord_reply: "discord_post",
  file_read: "file_read",
  read_file: "file_read",
  file_write: "file_write",
  write_file: "file_write",
  artifact_write: "file_write",
  notepad_write: "file_write",
  browser_navigate: "browser",
  browser_click: "browser",
  browser_type: "browser",
  browser_scroll: "browser",
  browser_screenshot: "browser",
  terminal_run: "terminal",
  run_terminal_command: "terminal",
  run_command: "terminal",
  store_memory: "memory_store",
  save_memory: "memory_store",
  recall_memory: "memory_recall",
  search_memory: "memory_recall",
  query_memory: "memory_recall",
};

export function getToolCategory(toolName: string): ToolCategory {
  if (!toolName) return "generic";
  const lower = toolName.toLowerCase().replace(/[^a-z_]/g, "");
  if (TOOL_MAP[lower]) return TOOL_MAP[lower];
  if (lower.includes("search") || lower.includes("query")) return "search";
  if (lower.includes("discord") && lower.includes("read")) return "discord_read";
  if (lower.includes("discord")) return "discord_post";
  if (lower.includes("read") || lower.includes("get")) return "file_read";
  if (lower.includes("write") || lower.includes("create") || lower.includes("save")) return "file_write";
  if (lower.includes("browser") || lower.includes("navigate") || lower.includes("url")) return "browser";
  if (lower.includes("terminal") || lower.includes("command") || lower.includes("shell")) return "terminal";
  if (lower.includes("memory") || lower.includes("remember")) return "memory_store";
  return "generic";
}

// Thought bubble icon text per category
export function getToolIcon(cat: ToolCategory): string {
  switch (cat) {
    case "search": return "\u{1F50D}";
    case "discord_read": return "\u{1F4AC}";
    case "discord_post": return "\u{1F4E4}";
    case "file_read": return "\u{1F4C4}";
    case "file_write": return "\u{270F}\u{FE0F}";
    case "browser": return "\u{1F310}";
    case "terminal": return ">_";
    case "memory_store": return "\u{1F9E0}";
    case "memory_recall": return "\u{1F9E0}";
    default: return "\u{2699}\u{FE0F}";
  }
}

export function getToolDisplayDetails(toolName: string, toolInput: any): string {
  if (!toolName) return "processing...";
  
  let parsed: any = {};
  if (typeof toolInput === "string") {
    try {
      parsed = JSON.parse(toolInput);
    } catch (e) {
      parsed = { _raw: toolInput };
    }
  } else if (typeof toolInput === "object" && toolInput !== null) {
    parsed = toolInput;
  }

  const name = toolName.toLowerCase();

  if (name.includes("discord_send_channel")) {
    return `Typing in #${parsed.channel || "discord"}...`;
  }
  if (name.includes("discord_read_channel")) {
    return `Reading #${parsed.channel || "discord"}...`;
  }
  if (name.includes("discord_web_send")) {
    return `Messaging ${parsed.recipient || "user"}...`;
  }
  if (name === "web_search" || name === "google_search") {
    const q = parsed.q || parsed.query || parsed._raw || "web";
    return `Searching: "${q}"`;
  }
  if (name === "file_write" || name === "notepad_write" || name === "artifact_write") {
    return `Writing to ${parsed.filename || parsed.path || "file"}...`;
  }
  if (name === "file_read" || name === "read_file") {
    return `Reading ${parsed.path || parsed.filename || "file"}...`;
  }
  if (name === "terminal_run" || name === "run_terminal_command") {
    return `Running: ${parsed.command || parsed.cmd || "command"}`;
  }
  if (name === "browser_navigate") {
    return `Navigating to ${parsed.url || "web"}...`;
  }

  return `running ${toolName}()`;
}

// Whether it's currently nighttime (for ambient dimming)
export function isNightTime(): boolean {
  const h = new Date().getHours();
  return h >= 22 || h < 6;
}

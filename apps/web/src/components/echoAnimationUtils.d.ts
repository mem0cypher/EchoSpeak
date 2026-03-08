export type ToolCategory = "search" | "discord_read" | "discord_post" | "file_read" | "file_write" | "browser" | "terminal" | "memory_store" | "memory_recall" | "generic";
export type EchoReaction = "success" | "error" | "memory_saved";
export declare function getToolCategory(toolName: string): ToolCategory;
export declare function getToolIcon(cat: ToolCategory): string;
export declare function getToolDisplayDetails(toolName: string, toolInput: any): string;
export declare function isNightTime(): boolean;

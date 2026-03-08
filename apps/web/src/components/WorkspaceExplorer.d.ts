export type FileNode = {
    name: string;
    path: string;
    type: "file" | "directory";
    size?: number;
    children?: FileNode[];
    item_count?: number;
};
export type WorkspaceData = {
    root: string;
    display_name: string;
    files: FileNode[];
    writable: boolean;
    terminal: boolean;
};
type WorkspaceExplorerProps = {
    apiBase: string;
    onFileSelect?: (path: string) => void;
};
export declare function WorkspaceExplorer({ apiBase, onFileSelect }: WorkspaceExplorerProps): import("react/jsx-runtime").JSX.Element;
export {};

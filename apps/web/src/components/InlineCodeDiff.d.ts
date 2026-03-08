export type CodeSessionStatus = "read" | "draft" | "saved" | "output";
export type CodeDiffSession = {
    filename: string;
    language: string;
    originalContent: string;
    currentContent: string;
    status: CodeSessionStatus;
    summary?: string;
    pendingConfirmation?: boolean;
};
type InlineCodeDiffProps = {
    session: CodeDiffSession;
    onAccept?: () => void;
    onDecline?: () => void;
};
export declare function InlineCodeDiff({ session, onAccept, onDecline }: InlineCodeDiffProps): import("react/jsx-runtime").JSX.Element;
export {};

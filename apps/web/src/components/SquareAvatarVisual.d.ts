import React from "react";
import { type ToolCategory, type EchoReaction } from "./echoAnimationUtils";
export type SquareAvatarVisualProps = {
    speaking: boolean;
    backendOnline: boolean | null;
    isThinking?: boolean;
    thinkingText?: string;
    activeToolName?: string;
    heartbeatEnabled?: boolean;
    toolCategory?: ToolCategory;
    userIsTyping?: boolean;
    pendingConfirmation?: boolean;
    reaction?: EchoReaction | null;
    onReactionDone?: () => void;
    spotifyPlaying?: {
        is_playing: boolean;
        track_id: string;
        track_name: string;
        track_artist: string;
    } | null;
};
export declare const SquareAvatarVisual: React.NamedExoticComponent<SquareAvatarVisualProps>;

import type { ResearchRun } from "./types";
type Palette = {
    panel2: string;
    line: string;
    text: string;
    textDim: string;
};
type ResearchPanelProps = {
    colors: Palette;
    runs: ResearchRun[];
    selectedVoice: string | null;
    voices: SpeechSynthesisVoice[];
    onSelectedVoiceChange: (value: string | null) => void;
    onClear: () => void;
};
export declare function ResearchPanel({ colors, runs, selectedVoice, voices, onSelectedVoiceChange, onClear, }: ResearchPanelProps): import("react/jsx-runtime").JSX.Element;
export {};

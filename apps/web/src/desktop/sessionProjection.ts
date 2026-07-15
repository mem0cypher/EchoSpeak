export type HistoryApplyGuard = {
  activeSessionId: string;
  targetSessionId: string;
  currentRequestSeq: number;
  requestSeq: number;
  currentRevision: number;
  startingRevision: number;
  streamInFlight: boolean;
};

export const canApplySessionHistory = (guard: HistoryApplyGuard): boolean =>
  guard.activeSessionId === guard.targetSessionId &&
  guard.currentRequestSeq === guard.requestSeq &&
  guard.currentRevision === guard.startingRevision &&
  !guard.streamInFlight;

export const ownsStreamCleanup = <T>(current: T | undefined, completing: T): boolean => current === completing;

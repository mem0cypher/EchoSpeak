export type VoiceControlHint =
  | "message"
  | "cancel_active"
  | "canonical_continue"
  | "canonical_steer"
  | "canonical_inspect";

export type VoiceTransportPhase =
  | "idle"
  | "requesting_permission"
  | "listening"
  | "transcribing"
  | "ready"
  | "speaking"
  | "error";

export type VoiceTranscript = {
  text: string;
  voiceTurnId: string;
  clientTurnId: string;
  sessionId: string;
  projectId: string;
  providerId: string;
  language: string;
  controlHint: VoiceControlHint;
};

type VoiceScope = {
  apiBase: string;
  sessionId: string;
  projectId: string;
};

export type SpeechScope = VoiceScope & {
  clientTurnId: string;
  requestId?: string;
  executionId?: string;
  taskRunId?: string;
  completeTurn?: boolean;
};

type VoiceCallbacks = {
  onPhase?: (phase: VoiceTransportPhase, detail?: string) => void;
  onLevel?: (level: number) => void;
  onFinalTranscript?: (transcript: VoiceTranscript) => void;
  onFailure?: (error: Error) => void;
};

const apiError = async (response: Response, fallback: string): Promise<Error> => {
  try {
    const payload = await response.json();
    const detail = typeof payload?.detail === "string" ? payload.detail : payload?.detail?.message;
    return new Error(String(detail || fallback));
  } catch {
    return new Error(fallback);
  }
};

const bytesToBase64 = (bytes: Uint8Array): string => {
  const block = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += block) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + block, bytes.length)));
  }
  return btoa(binary);
};

const encodeMonoWav = (samples: Float32Array, sampleRate: number): Uint8Array => {
  const dataSize = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const writeText = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  writeText(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, dataSize, true);
  let cursor = 44;
  for (let index = 0; index < samples.length; index += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(cursor, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    cursor += 2;
  }
  return new Uint8Array(buffer);
};

const mergeSamples = (chunks: Float32Array[]): Float32Array => {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const merged = new Float32Array(length);
  let cursor = 0;
  for (const chunk of chunks) {
    merged.set(chunk, cursor);
    cursor += chunk.length;
  }
  return merged;
};

export class LocalVoiceInput {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private silentGain: GainNode | null = null;
  private samples: Float32Array[] = [];
  private scope: VoiceScope | null = null;
  private callbacks: VoiceCallbacks = {};
  private startedAt = 0;
  private autoStopTimer = 0;
  private speechDetected = false;
  private lastSpeechAt = 0;
  private detectorFinishing = false;

  get active(): boolean {
    return Boolean(this.stream && this.context);
  }

  async start(scope: VoiceScope, callbacks: VoiceCallbacks = {}): Promise<void> {
    await this.stop(false);
    if (!scope.sessionId) throw new Error("Create or select a Session before using Voice.");
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Microphone capture is unavailable in this window.");
    this.scope = scope;
    this.callbacks = callbacks;
    callbacks.onPhase?.("requesting_permission", "Waiting for microphone permission");
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    const AudioContextCtor = window.AudioContext || (window as any).webkitAudioContext;
    if (!AudioContextCtor) {
      stream.getTracks().forEach((track) => track.stop());
      throw new Error("Local audio capture is unavailable in this window.");
    }
    const context: AudioContext = new AudioContextCtor();
    await context.resume();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    const silentGain = context.createGain();
    silentGain.gain.value = 0;
    this.samples = [];
    this.speechDetected = false;
    this.lastSpeechAt = 0;
    this.detectorFinishing = false;
    processor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      const copy = new Float32Array(input.length);
      copy.set(input);
      this.samples.push(copy);
      let peak = 0;
      for (let index = 0; index < copy.length; index += 1) peak = Math.max(peak, Math.abs(copy[index]));
      this.callbacks.onLevel?.(Math.min(1, peak * 4));
      const now = Date.now();
      if (peak >= 0.035) {
        this.speechDetected = true;
        this.lastSpeechAt = now;
      } else if (
        this.speechDetected &&
        !this.detectorFinishing &&
        now - this.startedAt >= 900 &&
        now - this.lastSpeechAt >= 1250
      ) {
        this.finishFromSilence();
      }
    };
    source.connect(processor);
    processor.connect(silentGain);
    silentGain.connect(context.destination);
    this.context = context;
    this.stream = stream;
    this.source = source;
    this.processor = processor;
    this.silentGain = silentGain;
    this.startedAt = Date.now();
    callbacks.onPhase?.("listening", "Listening on this device");
    this.autoStopTimer = window.setTimeout(() => this.finishFromSilence(), 120_000);
  }

  private finishFromSilence(): void {
    if (this.detectorFinishing || !this.active) return;
    this.detectorFinishing = true;
    const callbacks = this.callbacks;
    void this.stop(true)
      .then((transcript) => {
        if (transcript) callbacks.onFinalTranscript?.(transcript);
      })
      .catch((reason) => {
        const error = reason instanceof Error ? reason : new Error(String(reason));
        callbacks.onFailure?.(error);
      })
      .finally(() => {
        this.detectorFinishing = false;
      });
  }

  async stop(transcribe: boolean): Promise<VoiceTranscript | null> {
    if (this.autoStopTimer) window.clearTimeout(this.autoStopTimer);
    this.autoStopTimer = 0;
    const scope = this.scope;
    const callbacks = this.callbacks;
    const context = this.context;
    const samples = this.samples;
    const sampleRate = context?.sampleRate || 48_000;
    try { this.processor?.disconnect(); } catch { /* already detached */ }
    try { this.source?.disconnect(); } catch { /* already detached */ }
    try { this.silentGain?.disconnect(); } catch { /* already detached */ }
    this.stream?.getTracks().forEach((track) => track.stop());
    if (context) await context.close().catch(() => undefined);
    this.context = null;
    this.stream = null;
    this.source = null;
    this.processor = null;
    this.silentGain = null;
    this.samples = [];
    this.scope = null;
    this.speechDetected = false;
    this.lastSpeechAt = 0;
    callbacks.onLevel?.(0);
    if (!transcribe || !scope) {
      callbacks.onPhase?.("idle");
      return null;
    }
    if (Date.now() - this.startedAt < 250 || !samples.length) {
      callbacks.onPhase?.("error", "No speech was captured");
      throw new Error("No speech was captured. Hold the microphone button a little longer.");
    }
    callbacks.onPhase?.("transcribing", "Transcribing locally");
    const clientTurnId = crypto.randomUUID();
    const wav = encodeMonoWav(mergeSamples(samples), sampleRate);
    const response = await fetch(`${scope.apiBase}/media-runtime/voice/transcribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: scope.sessionId,
        project_id: scope.projectId,
        client_turn_id: clientTurnId,
        audio_base64: bytesToBase64(wav),
        mime_type: "audio/wav",
        language: navigator.language || "",
      }),
    });
    if (!response.ok) {
      callbacks.onPhase?.("error", "Local transcription is unavailable");
      throw await apiError(response, "Local transcription failed.");
    }
    const payload = await response.json();
    const event = payload?.event || {};
    const transcript: VoiceTranscript = {
      text: String(event.transcript || "").trim(),
      voiceTurnId: String(event.voice_turn_id || ""),
      clientTurnId,
      sessionId: scope.sessionId,
      projectId: scope.projectId,
      providerId: String(event.provider_id || ""),
      language: String(event.language || ""),
      controlHint: (event.control_hint || "message") as VoiceControlHint,
    };
    if (!transcript.text || !transcript.voiceTurnId) throw new Error("Local transcription returned no final transcript.");
    callbacks.onPhase?.("ready", "Transcript ready");
    return transcript;
  }
}

const speechChunks = (text: string, maximum = 260): string[] => {
  const cleaned = String(text || "").replace(/\s+/g, " ").trim();
  if (!cleaned) return [];
  const sentences = cleaned.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [cleaned];
  const chunks: string[] = [];
  let current = "";
  for (const sentenceValue of sentences) {
    const sentence = sentenceValue.trim();
    const candidate = current ? `${current} ${sentence}` : sentence;
    if (candidate.length <= maximum) {
      current = candidate;
      continue;
    }
    if (current) chunks.push(current);
    current = "";
    const words = sentence.split(/\s+/);
    for (const word of words) {
      const next = current ? `${current} ${word}` : word;
      if (next.length > maximum && current) {
        chunks.push(current);
        current = word;
      } else {
        current = next;
      }
    }
  }
  if (current) chunks.push(current);
  return chunks.filter(Boolean);
};

export class LocalVoicePlayback {
  private sequence = 0;
  private queue: Promise<void> = Promise.resolve();
  private controller: AbortController | null = null;
  private audio: HTMLAudioElement | null = null;
  private objectUrl = "";
  private activeTurnId = "";
  private activeSessionId = "";
  private activeClientTurnId = "";
  private nextChunkSequence = new Map<string, number>();

  private reset(cancelRemote: boolean): void {
    this.sequence += 1;
    this.controller?.abort();
    this.controller = null;
    if (this.audio) {
      this.audio.pause();
      this.audio.src = "";
      this.audio.load();
    }
    this.audio = null;
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = "";
    const turnId = this.activeTurnId;
    const sessionId = this.activeSessionId;
    const clientTurnId = this.activeClientTurnId;
    this.activeTurnId = "";
    this.activeSessionId = "";
    this.activeClientTurnId = "";
    this.nextChunkSequence.clear();
    if (cancelRemote && sessionId && (turnId || clientTurnId)) {
      const apiBase = this.lastApiBase;
      const target = turnId
        ? `turns/${encodeURIComponent(turnId)}`
        : `client-turns/${encodeURIComponent(clientTurnId)}`;
      void fetch(`${apiBase}/media-runtime/voice/${target}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      }).catch(() => undefined);
    }
  }

  stop(): void {
    this.reset(true);
  }

  complete(scope: SpeechScope): Promise<void> {
    const queuedSequence = this.sequence;
    const task = this.queue
      .catch(() => undefined)
      .then(async () => {
        if (queuedSequence !== this.sequence) return;
        await this.completeRemoteTurn(scope);
      });
    this.queue = task.catch(() => undefined);
    return task;
  }

  private lastApiBase = "";

  async speak(
    text: string,
    scope: SpeechScope,
    callbacks: VoiceCallbacks = {},
  ): Promise<void> {
    if (!this.activeSessionId) {
      this.lastApiBase = scope.apiBase;
      this.activeSessionId = scope.sessionId;
      this.activeClientTurnId = scope.clientTurnId;
    }
    const queuedSequence = this.sequence;
    const task = this.queue
      .catch(() => undefined)
      .then(async () => {
        if (queuedSequence !== this.sequence) return;
        await this.speakNow(text, scope, callbacks, queuedSequence);
      });
    this.queue = task.catch(() => undefined);
    return task;
  }

  private async speakNow(
    text: string,
    scope: SpeechScope,
    callbacks: VoiceCallbacks,
    ownSequence: number,
  ): Promise<void> {
    if (!scope.sessionId) throw new Error("Select a Session before reading a reply aloud.");
    const chunks = speechChunks(text);
    if (!chunks.length) return;
    const controller = new AbortController();
    this.controller = controller;
    this.lastApiBase = scope.apiBase;
    this.activeSessionId = scope.sessionId;
    this.activeClientTurnId = scope.clientTurnId;
    callbacks.onPhase?.("speaking", "Preparing local speech");
    for (let index = 0; index < chunks.length; index += 1) {
      if (controller.signal.aborted || ownSequence !== this.sequence) return;
      const sequenceKey = `${scope.sessionId}:${scope.clientTurnId}`;
      const chunkSequence = this.nextChunkSequence.get(sequenceKey) || 0;
      const response = await fetch(`${scope.apiBase}/media-runtime/voice/synthesize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          session_id: scope.sessionId,
          project_id: scope.projectId,
          client_turn_id: scope.clientTurnId,
          text: chunks[index],
          sequence: chunkSequence,
          request_id: scope.requestId || "",
          execution_id: scope.executionId || "",
          task_run_id: scope.taskRunId || "",
          settings: {},
        }),
      });
      if (!response.ok) throw await apiError(response, "Local speech playback could not be prepared.");
      this.nextChunkSequence.set(sequenceKey, chunkSequence + 1);
      const payload = await response.json();
      const turnId = String(payload?.turn?.id || "");
      const relativeUrl = String(payload?.audio_url || "");
      if (!turnId || !relativeUrl) throw new Error("Local speech playback returned an invalid clip.");
      this.activeTurnId = turnId;
      const clipResponse = await fetch(new URL(relativeUrl, scope.apiBase).toString(), { signal: controller.signal });
      if (!clipResponse.ok) throw await apiError(clipResponse, "Local speech clip is unavailable.");
      const blob = await clipResponse.blob();
      if (controller.signal.aborted || ownSequence !== this.sequence) return;
      const objectUrl = URL.createObjectURL(blob);
      this.objectUrl = objectUrl;
      try {
        await new Promise<void>((resolve, reject) => {
          const audio = new Audio(objectUrl);
          this.audio = audio;
          audio.onplay = () => callbacks.onPhase?.("speaking", "Speaking locally");
          audio.ontimeupdate = () => callbacks.onLevel?.(0.35 + Math.min(0.65, Math.abs(Math.sin(audio.currentTime * 7))));
          audio.onended = () => resolve();
          audio.onerror = () => reject(new Error("Local speech clip could not be played."));
          void audio.play().catch(reject);
        });
      } finally {
        if (this.audio) {
          this.audio.pause();
          this.audio.src = "";
          this.audio.load();
          this.audio = null;
        }
        URL.revokeObjectURL(objectUrl);
        if (this.objectUrl === objectUrl) this.objectUrl = "";
      }
    }
    callbacks.onLevel?.(0);
    callbacks.onPhase?.("idle");
    if (scope.completeTurn !== false) await this.completeRemoteTurn(scope);
    this.controller = null;
  }

  private async completeRemoteTurn(scope: SpeechScope): Promise<void> {
    const turnId = this.activeTurnId;
    if (!turnId || this.activeSessionId !== scope.sessionId) return;
    const response = await fetch(
      `${scope.apiBase}/media-runtime/voice/turns/${encodeURIComponent(turnId)}/complete`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: scope.sessionId }),
      },
    );
    if (!response.ok) throw await apiError(response, "Voice playback completion could not be saved.");
    this.nextChunkSequence.delete(`${scope.sessionId}:${scope.clientTurnId}`);
    this.activeTurnId = "";
    this.activeSessionId = "";
    this.activeClientTurnId = "";
  }
}

export const localVoicePlayback = new LocalVoicePlayback();

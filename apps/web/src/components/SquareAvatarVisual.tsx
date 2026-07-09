import React, { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence, Transition } from "framer-motion";
import { type ToolCategory, type EchoReaction, isNightTime } from "./echoAnimationUtils";

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
  spotifyPlaying?: { is_playing: boolean; track_id: string; track_name: string; track_artist: string } | null;
  avatarConfig?: {
    body_color?: string;
    eye_color?: string;
    bg_color?: string;
    glow_color?: string;
    idle_activity?: string;
    breathing_speed?: number;
    eye_size?: number;
    body_roundness?: number;
    enable_glow?: boolean;
    enable_idle_activities?: boolean;
    custom_status_text?: string;
  } | null;
};

type AvatarVisualConfig = {
  body_color: string;
  eye_color: string;
  bg_color: string;
  glow_color: string;
  idle_activity: string;
  breathing_speed: number;
  eye_size: number;
  body_roundness: number;
  enable_glow: boolean;
  enable_idle_activities: boolean;
  custom_status_text: string;
};

const DEFAULT_AVATAR_CONFIG: AvatarVisualConfig = {
  body_color: "#ffffff",
  eye_color: "#000000",
  bg_color: "#0a0a0a",
  glow_color: "#4f8eff",
  idle_activity: "auto",
  breathing_speed: 1,
  eye_size: 1,
  body_roundness: 24,
  enable_glow: true,
  enable_idle_activities: true,
  custom_status_text: "",
};

// ─── Animation Configs & Constants ──────────────────────────────────────────

const organicSpring: Transition = { type: "spring", stiffness: 120, damping: 20 };
const snappySpring: Transition = { type: "spring", stiffness: 400, damping: 25 };

type EyeAnimConfig = { x: number | number[]; y: number | number[]; durX: number; durY: number };

const TOOL_EYE_ANIMS: Record<ToolCategory, EyeAnimConfig> = {
  search:        { x: [-10, 10, -6, 8, -10],  y: 0,                           durX: 0.8, durY: 0 },
  discord_read:  { x: [-6, -4, -6],           y: [4, 6, 4],                   durX: 1.5, durY: 1.5 },
  discord_post:  { x: [-2, 2, -2],            y: 0,                           durX: 0.6, durY: 0 },
  file_read:     { x: 0,                      y: [-6, 6, -6],                 durX: 0,   durY: 1.4 },
  file_write:    { x: [-3, 3, -3],            y: [0, 1, 0],                   durX: 0.5, durY: 0.5 },
  browser:       { x: [-8, 6, -4, 10, -8],    y: [-3, 3, -2, 4, -3],          durX: 1.0, durY: 1.0 },
  terminal:      { x: [-2, 2, -2],            y: 0,                           durX: 0.4, durY: 0 },
  memory_store:  { x: 0,                      y: [0, 2, 0],                   durX: 0,   durY: 1.0 },
  memory_recall: { x: [3, 5, 3],              y: [-4, -6, -4],                durX: 1.2, durY: 1.2 },
  generic:       { x: [-8, 8, -8],            y: 0,                           durX: 1.2, durY: 0 },
};

// Short glances & tiny habits — the main "alive" layer during chill
type MicroBehavior =
  | "none"
  | "look_left"
  | "look_right"
  | "look_up"
  | "look_down"
  | "glance_around"
  | "curious_tilt"
  | "soft_sway"
  | "shoulder_shift"
  | "happy_soft"
  | "squint"
  | "wide_eyes";

// Weighted: looking around should dominate
const MICRO_POOL: MicroBehavior[] = [
  "look_left", "look_left", "look_right", "look_right",
  "look_up", "look_down", "look_down",
  "glance_around", "glance_around",
  "curious_tilt", "curious_tilt",
  "soft_sway", "soft_sway",
  "shoulder_shift",
  "happy_soft",
  "squint",
  "wide_eyes",
];

const MICRO_DUR: Record<MicroBehavior, [number, number]> = {
  none:            [0, 0],
  look_left:       [1400, 2400],
  look_right:      [1400, 2400],
  look_up:         [1100, 1800],
  look_down:       [1200, 2000],
  glance_around:   [2200, 3200],
  curious_tilt:    [1800, 2800],
  soft_sway:       [2400, 3600],
  shoulder_shift:  [1600, 2400],
  happy_soft:      [1600, 2200],
  squint:          [1100, 1700],
  wide_eyes:       [1000, 1600],
};

// Longer moods — still casual, never gimmicky
type IdleActivity =
  | "none"
  | "phone"        // scrolling on his phone
  | "daydream"     // soft float / spaced out
  | "napping"
  | "waking_up"
  | "vibing"       // Spotify only
  | "stretching"
  | "weight_shift" // lean one side, settle
  | "fidget";      // tiny restless rock

// Weighted toward chill moods; nap is rarer
const ACTIVITY_POOL: IdleActivity[] = [
  "phone", "phone",
  "daydream", "daydream",
  "weight_shift", "weight_shift", "weight_shift",
  "fidget", "fidget",
  "stretching",
  "napping",
];

const ACT_DUR: Record<IdleActivity, [number, number]> = {
  none:         [8000, 14000],
  phone:        [10000, 18000],
  daydream:     [8000, 14000],
  napping:      [22000, 40000],
  waking_up:    [1600, 1600],
  vibing:       [10000, 18000],
  stretching:   [3500, 5500],
  weight_shift: [5000, 9000],
  fidget:       [4000, 7000],
};

// Editor / legacy aliases → internal activities
const IDLE_ALIASES: Record<string, IdleActivity> = {
  auto: "none",
  none: "none",
  phone: "phone",
  gaming: "phone",
  daydream: "daydream",
  floating: "daydream",
  napping: "napping",
  vibing: "vibing",
  stretching: "stretching",
  weight_shift: "weight_shift",
  fidget: "fidget",
};

function resolveIdle(preferred?: string): IdleActivity {
  if (!preferred || preferred === "auto") {
    return ACTIVITY_POOL[Math.floor(Math.random() * ACTIVITY_POOL.length)];
  }
  return IDLE_ALIASES[preferred] ?? "none";
}

// Moods that still allow soft micro glances (keeps him interconnected & alive)
function allowsMicros(activity: IdleActivity): boolean {
  return activity === "none" || activity === "daydream" || activity === "weight_shift" || activity === "fidget" || activity === "vibing";
}

// 1.5× original 140px body; face geometry authored at 140 and scaled via FACE
const BODY_SIZE = 210;
const FACE = BODY_SIZE / 140; // 1.5 — keep eyes/mouth proportional to body

function faceDim(v: number | number[]): number | number[] {
  return Array.isArray(v) ? v.map((n) => n * FACE) : v * FACE;
}

// ─── Main Component ─────────────────────────────────────────────────────────

export const SquareAvatarVisual = React.memo(function SquareAvatarVisual({
  speaking,
  backendOnline,
  isThinking,
  thinkingText,
  heartbeatEnabled,
  toolCategory = "generic",
  userIsTyping,
  pendingConfirmation,
  reaction,
  onReactionDone,
  spotifyPlaying,
  avatarConfig,
}: SquareAvatarVisualProps) {
  const [blink, setBlink] = useState(false);
  const [idleActivity, setIdleActivity] = useState<IdleActivity>("none");
  const [microBehavior, setMicroBehavior] = useState<MicroBehavior>("none");
  const [nightMode, setNightMode] = useState(isNightTime);
  const [activeReaction, setActiveReaction] = useState<EchoReaction | null>(null);

  const reactionTimerRef = useRef<number>(0);
  const microTimerRef = useRef<number>(0);
  const microPhaseTimerRef = useRef<number>(0);
  const activityTimerRef = useRef<number>(0);
  const mergedAvatarConfig = { ...DEFAULT_AVATAR_CONFIG, ...(avatarConfig || {}) };
  const breathDuration = Math.max(0.9, 3 / Math.max(0.25, Number(mergedAvatarConfig.breathing_speed || 1)));

  // ─── Spotify: soft vibe while music plays ────────────────────────────────
  const spotifyVibingRef = useRef(false);

  useEffect(() => {
    const isPlaying = !!spotifyPlaying?.is_playing;
    if (!isPlaying) {
      spotifyVibingRef.current = false;
      if (idleActivity === "vibing") {
        if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
        setIdleActivity("none");
      }
      return;
    }
    spotifyVibingRef.current = true;
    if (idleActivity !== "vibing") {
      if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
      setMicroBehavior("none");
      setIdleActivity("vibing");
    }
  }, [spotifyPlaying, idleActivity]);

  // Forced idle mode from Avatar Editor (locks mood; still allows micros when chill)
  const forcedIdle = mergedAvatarConfig.idle_activity && mergedAvatarConfig.idle_activity !== "auto"
    ? resolveIdle(mergedAvatarConfig.idle_activity)
    : null;

  useEffect(() => {
    if (!mergedAvatarConfig.enable_idle_activities) {
      if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
      setIdleActivity("none");
      return;
    }
    if (forcedIdle && forcedIdle !== "none") {
      if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
      setIdleActivity(forcedIdle);
    }
  }, [mergedAvatarConfig.enable_idle_activities, forcedIdle]);

  // ─── Derived States ───────────────────────────────────────────────────────
  const isSleeping = backendOnline === false;
  const isNapping = idleActivity === "napping";
  const isWakingUp = idleActivity === "waking_up";
  const isOnPhone = idleActivity === "phone";
  const isDaydream = idleActivity === "daydream";
  const isVibing = idleActivity === "vibing";
  const isStretching = idleActivity === "stretching";
  const isWeightShift = idleActivity === "weight_shift";
  const isFidget = idleActivity === "fidget";
  const isActive = speaking || isThinking || userIsTyping || pendingConfirmation || activeReaction !== null;
  const microsOk = !isActive && !isSleeping && allowsMicros(idleActivity);

  // ─── Mood engine: chill → mood → chill ────────────────────────────────────
  const cycleToNext = useCallback((prev: IdleActivity) => {
    if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
    if (spotifyVibingRef.current) return;
    if (forcedIdle && forcedIdle !== "none") return;

    if (prev === "napping") {
      setIdleActivity("waking_up");
      activityTimerRef.current = window.setTimeout(() => cycleToNext("waking_up"), ACT_DUR.waking_up[0]);
      return;
    }

    // Alternate: after any mood, return to chill; after chill, pick a mood
    if (prev !== "none" && prev !== "waking_up") {
      setIdleActivity("none");
      const [mn, mx] = ACT_DUR.none;
      activityTimerRef.current = window.setTimeout(() => cycleToNext("none"), mn + Math.random() * (mx - mn));
    } else {
      const next = resolveIdle("auto");
      setIdleActivity(next);
      const [mn, mx] = ACT_DUR[next];
      activityTimerRef.current = window.setTimeout(() => cycleToNext(next), mn + Math.random() * (mx - mn));
    }
  }, [forcedIdle]);

  useEffect(() => {
    if (isActive) {
      setIdleActivity("none");
      setMicroBehavior("none");
      if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
      if (microTimerRef.current) clearTimeout(microTimerRef.current);
      if (microPhaseTimerRef.current) clearTimeout(microPhaseTimerRef.current);
      return;
    }

    if (!mergedAvatarConfig.enable_idle_activities) return;
    if (spotifyVibingRef.current) return;
    if (forcedIdle && forcedIdle !== "none") {
      setIdleActivity(forcedIdle);
      return;
    }

    setIdleActivity("none");
    setMicroBehavior("none");
    const [mn, mx] = ACT_DUR.none;
    activityTimerRef.current = window.setTimeout(
      () => cycleToNext("none"),
      (mn + Math.random() * (mx - mn)) * 0.45
    );

    return () => {
      if (microTimerRef.current) clearTimeout(microTimerRef.current);
      if (microPhaseTimerRef.current) clearTimeout(microPhaseTimerRef.current);
      if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
    };
  }, [isActive, cycleToNext, mergedAvatarConfig.enable_idle_activities, forcedIdle]);

  // ─── Micro engine: frequent soft life while chill moods allow it ──────────
  useEffect(() => {
    if (!microsOk) {
      if (microTimerRef.current) clearTimeout(microTimerRef.current);
      if (microBehavior !== "none") setMicroBehavior("none");
      return;
    }
    if (microBehavior !== "none") return;

    // More frequent when fully chill; a bit slower during soft moods
    const base = idleActivity === "none" ? 2200 : 4500;
    const spread = idleActivity === "none" ? 4500 : 6000;
    microTimerRef.current = window.setTimeout(() => {
      setMicroBehavior(MICRO_POOL[Math.floor(Math.random() * MICRO_POOL.length)]);
    }, base + Math.random() * spread);

    return () => {
      if (microTimerRef.current) clearTimeout(microTimerRef.current);
    };
  }, [microsOk, idleActivity, microBehavior]);

  useEffect(() => {
    if (microPhaseTimerRef.current) clearTimeout(microPhaseTimerRef.current);
    if (!microsOk || microBehavior === "none") return;

    const [mn, mx] = MICRO_DUR[microBehavior];
    microPhaseTimerRef.current = window.setTimeout(
      () => setMicroBehavior("none"),
      mn + Math.random() * (mx - mn)
    );

    return () => {
      if (microPhaseTimerRef.current) clearTimeout(microPhaseTimerRef.current);
    };
  }, [microsOk, microBehavior]);

  // Night mode checker
  useEffect(() => {
    const check = () => setNightMode(isNightTime());
    const interval = setInterval(check, 60000);
    return () => clearInterval(interval);
  }, []);

  // Reaction cleanup
  useEffect(() => {
    if (reaction && reaction !== activeReaction) {
      setActiveReaction(reaction);
      if (reactionTimerRef.current) clearTimeout(reactionTimerRef.current);
      reactionTimerRef.current = window.setTimeout(() => {
        setActiveReaction(null);
        onReactionDone?.();
      }, reaction === "error" ? 1500 : 1200); // slightly longer reactions for polish
    }
  }, [reaction, activeReaction, onReactionDone]);

  // ─── Blink ────────────────────────────────────────────────────────────────
  useEffect(() => {
    let timeout: number;
    const scheduleBlink = () => {
      if (!isSleeping && !isNapping && !isThinking && activeReaction !== "error" && !isOnPhone) {
        setBlink(true);
        setTimeout(() => setBlink(false), 140);
      }
      timeout = window.setTimeout(scheduleBlink, 2200 + Math.random() * 3800);
    };
    timeout = window.setTimeout(scheduleBlink, 900);
    return () => window.clearTimeout(timeout);
  }, [isSleeping, isNapping, isThinking, activeReaction, isOnPhone]);

  // ─── Animation Generators ─────────────────────────────────────────────────

  const getEyeProps = () => {
    let w: number | number[] = 20, h: number | number[] = 24, r: number | number[] = 12;
    let x: number | number[] = 0, y: number | number[] = 0;
    let tX: Transition = organicSpring;
    let tY: Transition = organicSpring;
    let tShape: Transition = organicSpring;

    // Shape (priority high → low)
    if (isSleeping || isNapping) { h = 4; r = 4; w = 24; }
    else if (isWakingUp) { w = 26; h = 28; r = 14; }
    else if (activeReaction === "error") { w = 24; h = 28; r = 14; }
    else if (activeReaction === "success") { w = 22; h = 6; r = 6; }
    else if (activeReaction === "memory_saved") { w = 20; h = 8; r = 8; }
    else if (userIsTyping) { w = 20; h = 20; r = 10; }
    else if (pendingConfirmation) { w = 20; h = 24; r = 12; }
    else if (isOnPhone) { w = 18; h = 10; r = 6; }
    else if (isVibing) { w = 22; h = 12; r = 7; }
    else if (isStretching) { w = 24; h = 14; r = 8; }
    else if (isDaydream) { w = 20; h = 22; r = 11; }
    else if (microBehavior === "look_down") { w = 20; h = 20; r = 10; }
    else if (microBehavior === "curious_tilt") { w = 22; h = 22; r = 11; }
    else if (microBehavior === "happy_soft") { w = 22; h = 16; r = 8; }
    else if (microBehavior === "squint") { w = 22; h = 10; r = 6; }
    else if (microBehavior === "wide_eyes") { w = 24; h = 28; r = 14; }
    else if (isThinking) {
      switch (toolCategory) {
        case "search": w = 20; h = 22; r = 11; break;
        case "terminal": w = 18; h = 16; r = 8; break;
        case "browser": w = 24; h = 26; r = 13; break;
        case "memory_store": w = 20; h = 10; r = 8; break;
        case "memory_recall": w = 22; h = 26; r = 13; break;
        case "file_write": w = 18; h = 18; r = 9; break;
        case "discord_post": w = 18; h = 20; r = 10; break;
      }
    }

    if (blink && !isSleeping && !isNapping) {
      h = 3; r = 4;
      tShape = snappySpring;
    }

    // Position
    if (isSleeping || isNapping) { x = 0; y = 0; }
    else if (isWakingUp) { y = [0, -3, 0]; tY = { duration: 0.45, ease: "easeOut" }; }
    else if (activeReaction === "error") { x = [0, -4, 4, -2, 0]; tX = { duration: 0.4 }; }
    else if (userIsTyping) { x = 4; y = -2; }
    else if (isThinking) {
      if (toolCategory === "discord_post" || toolCategory === "file_write") {
        w = 22; h = 8; r = 6;
      } else if (toolCategory === "search" || toolCategory === "browser" || toolCategory === "discord_read" || toolCategory === "file_read") {
        w = 22; h = 24; r = 12;
        x = [-8, 8, -6, 6, -8];
        tX = { duration: 1.5, repeat: Infinity, ease: "easeInOut" };
      } else if (toolCategory === "terminal") {
        w = 20; h = 18; r = 8;
        y = -2;
      } else {
        const cfg = TOOL_EYE_ANIMS[toolCategory] || TOOL_EYE_ANIMS.generic;
        x = cfg.x; y = cfg.y;
        if (cfg.durX > 0) tX = { duration: cfg.durX, repeat: Infinity, ease: "easeInOut" };
        if (cfg.durY > 0) tY = { duration: cfg.durY, repeat: Infinity, ease: "easeInOut" };
      }
    }
    else if (isOnPhone) {
      // Eyes locked on the phone below
      y = [7, 8, 7]; x = [0, 1.5, 0, -1, 0];
      tY = { duration: 2.8, repeat: Infinity, ease: "easeInOut" };
      tX = { duration: 3.2, repeat: Infinity, ease: "easeInOut" };
    }
    // Micros sit above soft moods so glances still happen while daydreaming etc.
    else if (microBehavior === "look_left") { x = -8; }
    else if (microBehavior === "look_right") { x = 8; }
    else if (microBehavior === "look_up") { y = -6; }
    else if (microBehavior === "look_down") { y = 6; }
    else if (microBehavior === "glance_around") {
      x = [0, -7, 7, -4, 0];
      tX = { duration: 2.4, ease: "easeInOut" };
    }
    else if (microBehavior === "curious_tilt") { x = 4; y = -2; }
    else if (microBehavior === "happy_soft") {
      y = [0, -1.5, 0];
      tY = { duration: 1.2, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isDaydream) {
      x = [-4, 0, 5, 0, -4];
      tX = { duration: 5.5, repeat: Infinity, ease: "easeInOut" };
      y = [0, -1, 0];
      tY = { duration: 4, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isStretching) { y = [-3, 0]; tY = { duration: 1.2, ease: "easeInOut" }; }

    return {
      animate: { x: faceDim(x), y: faceDim(y), width: faceDim(w), height: faceDim(h), borderRadius: faceDim(r) },
      transition: { x: tX, y: tY, width: tShape, height: tShape, borderRadius: tShape },
    };
  };

  const getMouthProps = () => {
    let w: number | number[] = 20, h: number | number[] = 6, r: number | number[] = 10;
    let t: Transition = organicSpring;

    if (isSleeping || isNapping) { w = 12; h = 6; }
    else if (isWakingUp) { w = [22, 20]; h = [14, 6]; r = [12, 10]; t = { duration: 0.5 }; }
    else if (activeReaction === "success") { w = [24, 28, 24]; h = [4, 8, 4]; t = { duration: 0.6 }; }
    else if (activeReaction === "error") { w = 16; h = 2; r = 4; }
    else if (speaking) {
      w = [20, 36, 24, 32, 22, 20]; h = [8, 26, 12, 22, 10, 8]; r = [10, 18, 12, 16, 10, 10];
      t = { duration: 0.6, repeat: Infinity };
    }
    else if (isOnPhone) { w = 12; h = 5; r = 6; }
    else if (isVibing) {
      w = [20, 23, 20]; h = [5, 7, 5]; t = { duration: 0.9, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isStretching) {
      w = [24, 20]; h = [14, 6]; r = [12, 10]; t = { duration: 1.8, ease: "easeInOut" };
    }
    else if (isThinking) { w = 16; h = 4; }
    else if (userIsTyping) { w = 12; h = 4; r = 6; }
    else if (pendingConfirmation) { w = 14; h = 8; }
    else if (microBehavior === "curious_tilt") { w = 18; h = 5; }
    else if (microBehavior === "happy_soft") {
      w = [20, 23, 20]; h = [5, 7, 5]; r = [10, 11, 10];
      t = { duration: 1.2, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isFidget) {
      w = [18, 20, 18]; h = [5, 6, 5]; t = { duration: 1.6, repeat: Infinity, ease: "easeInOut" };
    }

    return { animate: { width: faceDim(w), height: faceDim(h), borderRadius: faceDim(r) }, transition: t };
  };

  const getBodyProps = () => {
    let x: number | number[] = 0, y: number | number[] = 0, rot: number | number[] = 0;
    let scaleX: number | number[] = 1, scaleY: number | number[] = 1;
    let tX: Transition = organicSpring, tY: Transition = organicSpring, tR: Transition = organicSpring;
    let tSX: Transition = organicSpring, tSY: Transition = organicSpring;

    if (activeReaction === "error") {
      y = [0, -4, 4, -2, 0]; rot = [0, -3, 3, -1, 0];
      tY = { duration: 0.5 }; tR = { duration: 0.5 };
    }
    else if (activeReaction === "success") {
      y = [0, -10, 0]; tY = { duration: 0.6 };
    }
    else if (isWakingUp) {
      y = [0, -12, -4, 0]; tY = { duration: 0.7, ease: "easeOut" };
    }
    else if (speaking) {
      y = [0, -7, 0]; tY = { duration: 0.85, repeat: Infinity, ease: "easeInOut" };
    }
    else if (userIsTyping) {
      y = [0, -3, 0]; rot = -3;
      tY = { duration: 1.6, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isThinking) {
      if (toolCategory === "discord_post" || toolCategory === "file_write") {
        y = [0, -3, 0, -2, 0];
        rot = [0, -1, 1, 0, 0];
        tY = { duration: 0.7, repeat: Infinity, ease: "easeInOut" };
        tR = { duration: 0.7, repeat: Infinity, ease: "easeInOut" };
      } else if (toolCategory === "search" || toolCategory === "browser") {
        y = [0, -3, 0]; tY = { duration: 1.3, repeat: Infinity, ease: "easeInOut" };
      } else {
        y = [0, -2, 0]; tY = { duration: 1.5, repeat: Infinity, ease: "easeInOut" };
      }
    }
    else if (isNapping || isSleeping) {
      y = [0, -3, 0]; rot = [1.2, -1.2, 1.2];
      tY = { duration: 3.2, repeat: Infinity, ease: "easeInOut" };
      tR = { duration: 4.2, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isOnPhone) {
      // Hunched over phone — small rock, not a dance
      y = [4, 5.5, 4];
      rot = [-2.5, -1, -2.5];
      tY = { duration: 2.6, repeat: Infinity, ease: "easeInOut" };
      tR = { duration: 3.0, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isStretching) {
      y = [0, -10, -10, 0]; scaleY = [1, 1.04, 1.04, 1]; scaleX = [1, 0.98, 0.98, 1];
      tY = { duration: 2.8, ease: "easeInOut" };
      tSY = { duration: 2.8, ease: "easeInOut" };
      tSX = { duration: 2.8, ease: "easeInOut" };
    }
    // Micros interrupt soft moods (daydream / fidget / vibe / weight shift)
    else if (microBehavior === "look_down") { y = 1.5; }
    else if (microBehavior === "curious_tilt") { x = 2; y = -1; rot = -5; }
    else if (microBehavior === "soft_sway") {
      x = [-3, 3, -3]; rot = [-2, 2, -2];
      tX = { duration: 2.8, repeat: Infinity, ease: "easeInOut" };
      tR = { duration: 2.8, repeat: Infinity, ease: "easeInOut" };
      y = [0, -2, 0];
      tY = { duration: breathDuration, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" };
    }
    else if (microBehavior === "shoulder_shift") {
      x = [0, 3, 0]; rot = [0, 3.5, 0];
      tX = { duration: 1.6, ease: "easeInOut" };
      tR = { duration: 1.6, ease: "easeInOut" };
    }
    else if (microBehavior === "happy_soft") {
      y = [0, -3, 0];
      tY = { duration: 1.2, repeat: Infinity, ease: "easeInOut" };
    }
    else if (microBehavior === "glance_around") {
      rot = [0, -2, 2, 0];
      tR = { duration: 2.4, ease: "easeInOut" };
      y = [0, -2, 0];
      tY = { duration: breathDuration, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" };
    }
    else if (isDaydream) {
      y = [0, -4, 0]; x = [-6, 6, -6]; rot = [-2, 2, -2];
      tY = { duration: 4.5, repeat: Infinity, ease: "easeInOut" };
      tX = { duration: 7, repeat: Infinity, ease: "easeInOut" };
      tR = { duration: 5.5, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isVibing) {
      y = [0, -3, 0]; x = [-1.5, 1.5, -1.5]; rot = [-2, 2, -2];
      tY = { duration: 0.95, repeat: Infinity, ease: "easeInOut" };
      tX = { duration: 0.95, repeat: Infinity, ease: "easeInOut" };
      tR = { duration: 0.95, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isWeightShift) {
      x = [0, 5, 5, 0]; rot = [0, 4, 4, 0];
      y = [0, -1, -1, 0];
      tX = { duration: 3.5, ease: "easeInOut" };
      tR = { duration: 3.5, ease: "easeInOut" };
      tY = { duration: 3.5, ease: "easeInOut" };
    }
    else if (isFidget) {
      y = [0, -2, 0, -1.5, 0]; rot = [0, -1.5, 1.5, -1, 0];
      tY = { duration: 1.8, repeat: Infinity, ease: "easeInOut" };
      tR = { duration: 1.8, repeat: Infinity, ease: "easeInOut" };
    }
    else {
      // Base breathing — always the home pose
      y = [0, -3, 0];
      tY = { duration: breathDuration, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" };
    }

    return {
      animate: { x, y, rotate: rot, scaleX, scaleY },
      transition: { x: tX, y: tY, rotate: tR, scaleX: tSX, scaleY: tSY },
    };
  };

  const getBodyStyle = () => {
    const dimFactor = nightMode && !isActive ? 0.85 : 1;
    let bg = `linear-gradient(135deg, ${mergedAvatarConfig.body_color} 0%, ${mergedAvatarConfig.body_color}dd 100%)`;
    let shadow = `0 0 18px ${mergedAvatarConfig.glow_color}40, inset 0 0 20px rgba(0,0,0,0.05)`;

    if (isSleeping || isNapping) {
      bg = "linear-gradient(135deg, #e2e8f0 0%, #cbd5e1 100%)";
      shadow = "0 0 10px rgba(0,0,0,0.1), inset 0 0 20px rgba(0,0,0,0.05)";
    } else if (isWakingUp) {
      bg = "linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%)";
      shadow = "0 0 20px rgba(251,191,36,0.22), inset 0 0 20px rgba(0,0,0,0.03)";
    } else if (speaking) {
      bg = "linear-gradient(135deg, #ffffff 0%, #e2e8f0 100%)";
      shadow = "0 0 20px rgba(255,255,255,0.28), inset 0 0 20px rgba(0,0,0,0.05)";
    } else if (activeReaction === "success") {
      bg = "linear-gradient(135deg, #f0fdf4 0%, #bbf7d0 100%)";
      shadow = "0 0 22px rgba(34,197,94,0.28), inset 0 0 20px rgba(0,0,0,0.03)";
    } else if (activeReaction === "error") {
      bg = "linear-gradient(135deg, #fef2f2 0%, #fecaca 100%)";
      shadow = "0 0 22px rgba(239,68,68,0.28), inset 0 0 20px rgba(0,0,0,0.03)";
    } else if (activeReaction === "memory_saved") {
      bg = "linear-gradient(135deg, #eff6ff 0%, #bfdbfe 100%)";
      shadow = "0 0 22px rgba(59,130,246,0.28), inset 0 0 20px rgba(0,0,0,0.03)";
    } else if (isVibing) {
      bg = "linear-gradient(135deg, #faf5ff 0%, #ede9fe 100%)";
      shadow = "0 0 14px rgba(168,85,247,0.12), inset 0 0 20px rgba(0,0,0,0.03)";
    } else if (isOnPhone) {
      bg = "linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%)";
      shadow = "inset 0 0 18px rgba(0,0,0,0.06)";
    } else if (isThinking && toolCategory === "terminal") {
      bg = "linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%)";
      shadow = "0 0 12px rgba(34,197,94,0.12), inset 0 0 20px rgba(0,0,0,0.05)";
    } else if (userIsTyping) {
      bg = "linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%)";
      shadow = "inset 0 0 20px rgba(0,0,0,0.08)";
    }

    return {
      background: bg,
      boxShadow: mergedAvatarConfig.enable_glow ? shadow : "inset 0 0 20px rgba(0,0,0,0.05)",
      opacity: dimFactor,
    };
  };

  const bodyStyle = getBodyStyle();
  const eyeProps = getEyeProps();
  const mouthProps = getMouthProps();
  const bodyProps = getBodyProps();
  const showEyebrow = pendingConfirmation && !isSleeping && !speaking;
  const eyeScale = Math.max(1, Number(mergedAvatarConfig.eye_size || 1));

  return (
    <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", position: "relative", background: `radial-gradient(circle at center, ${mergedAvatarConfig.bg_color} 0%, rgba(0,0,0,0) 72%)` }}>

      {/* ── Effects Layers ────────────────────────────────────────────────── */}
      <AnimatePresence>
        {activeReaction === "success" && (
          <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1.35, opacity: [0, 0.55, 0] }} exit={{ opacity: 0 }} transition={{ duration: 0.7 }} style={{ position: "absolute", width: BODY_SIZE + 48, height: BODY_SIZE + 48, borderRadius: 40, border: "3px solid rgba(34,197,94,0.45)", zIndex: 50, pointerEvents: "none" }} />
        )}
        {activeReaction === "error" && (
          <motion.div initial={{ scale: 1, opacity: 0.45 }} animate={{ scale: [1, 1.08, 1], opacity: [0.45, 0.25, 0] }} exit={{ opacity: 0 }} transition={{ duration: 0.8 }} style={{ position: "absolute", width: BODY_SIZE + 48, height: BODY_SIZE + 48, borderRadius: 40, border: "3px solid rgba(239,68,68,0.35)", zIndex: 50, pointerEvents: "none" }} />
        )}
      </AnimatePresence>

      {/* ── Main Body ─────────────────────────────────────────────────────── */}
      <motion.div
        animate={bodyProps.animate}
        transition={bodyProps.transition}
        style={{
          width: BODY_SIZE, height: BODY_SIZE,
          borderRadius: Math.max(22, Number(mergedAvatarConfig.body_roundness || 24) * 1.35),
          border: mergedAvatarConfig.enable_glow ? `6px solid ${mergedAvatarConfig.glow_color}22` : "6px solid rgba(255,255,255,0.08)",
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          position: "relative",
          zIndex: 60,
        }}
      >
        {/* Animated background to allow smooth color crossfades */}
        <motion.div
          animate={{ background: bodyStyle.background, boxShadow: bodyStyle.boxShadow, opacity: bodyStyle.opacity }}
          transition={{ duration: 0.6 }}
          style={{ position: "absolute", inset: -5, borderRadius: Math.max(22, Number(mergedAvatarConfig.body_roundness || 24) * 1.35), zIndex: -1 }}
        />

        {/* ── Eyebrow ── */}
        {showEyebrow && (
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            style={{
              position: "absolute",
              top: 44 * FACE,
              left: 62 * FACE,
              width: 16 * FACE,
              height: 3 * FACE,
              background: "#000",
              borderRadius: 2,
              transform: "rotate(-15deg)",
              zIndex: 10,
            }}
          />
        )}

        {/* ── Eyes ── */}
        <div
          style={{
            display: "flex",
            gap: Math.max(22, 28 + mergedAvatarConfig.eye_size * 6) * FACE,
            marginTop: -20 * FACE,
            zIndex: 2,
          }}
        >
          <motion.div
            animate={eyeProps.animate}
            transition={eyeProps.transition}
            style={{
              background: mergedAvatarConfig.eye_color,
              boxShadow: mergedAvatarConfig.enable_glow
                ? `0 0 ${12 * FACE}px ${mergedAvatarConfig.glow_color}35`
                : `0 ${4 * FACE}px ${6 * FACE}px rgba(0,0,0,0.12)`,
              scale: eyeScale,
            }}
          />
          <motion.div
            animate={eyeProps.animate}
            transition={eyeProps.transition}
            style={{
              background: mergedAvatarConfig.eye_color,
              boxShadow: mergedAvatarConfig.enable_glow
                ? `0 0 ${12 * FACE}px ${mergedAvatarConfig.glow_color}35`
                : `0 ${4 * FACE}px ${6 * FACE}px rgba(0,0,0,0.12)`,
              scale: eyeScale,
            }}
          />
        </div>

        {/* ── Mouth ── */}
        <motion.div
          animate={mouthProps.animate}
          transition={mouthProps.transition}
          style={{
            marginTop: 24 * FACE,
            background: mergedAvatarConfig.eye_color,
            boxShadow: mergedAvatarConfig.enable_glow
              ? `0 0 ${12 * FACE}px ${mergedAvatarConfig.glow_color}25`
              : `0 ${4 * FACE}px ${6 * FACE}px rgba(0,0,0,0.12)`,
            zIndex: 2,
            opacity: 0.92,
          }}
        />

        {/* ── UI Bubbles ──────────────────────────────────────────────────── */}
        <AnimatePresence>
          {isThinking && !isSleeping && !isNapping && (
            <motion.div initial={{ opacity: 0, scale: 0.8, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.8, y: 6 }} transition={{ duration: 0.2 }} style={{ position: "absolute", top: toolCategory === "search" ? -85 : -75, zIndex: 100, pointerEvents: "none", display: "flex", justifyContent: "center" }}>
              {toolCategory === "discord_post" || toolCategory === "discord_read" ? (
                <div style={{ background: "#5865F2", boxShadow: "0 8px 24px rgba(88,101,242,0.3), inset 0 2px 4px rgba(255,255,255,0.2)", padding: "14px 24px", borderRadius: "24px 24px 24px 6px", color: "#ffffff", fontSize: 15, fontWeight: 700, whiteSpace: "nowrap", display: "flex", alignItems: "center", gap: 12 }}>
                  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z"/></svg>
                  <span>{thinkingText || "Discord..."}</span>
                  <div style={{ display: "flex", gap: 4, marginLeft: 4 }}>
                    {[0, 1, 2].map(i => <motion.div key={i} animate={{ y: [0, -4, 0] }} transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.15 }} style={{ width: 6, height: 6, borderRadius: "50%", background: "#ffffff" }} />)}
                  </div>
                </div>
              ) : toolCategory === "search" ? (
                <div style={{ background: "#ffffff", border: "1px solid rgba(0,0,0,0.1)", boxShadow: "0 8px 24px rgba(0,0,0,0.15)", padding: "14px 24px", borderRadius: "24px", color: "#333", fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 12, minWidth: 220, maxWidth: 300 }}>
                  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#4285F4" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
                  <div style={{ flex: 1, borderRight: "2px solid #000", animation: "blink 1s step-end infinite", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {thinkingText ? thinkingText.replace('Searching: "', '').replace('"', '') : "Searching..."}
                  </div>
                  <style>{`@keyframes blink { 50% { border-color: transparent; } }`}</style>
                </div>
              ) : (
                <div style={{ background: "#ffffff", border: "1px solid rgba(0, 0, 0, 0.1)", boxShadow: "0 8px 24px rgba(0,0,0,0.15), 0 0 20px rgba(255,255,255,0.8)", padding: "14px 24px", borderRadius: "24px 24px 24px 8px", color: "#000000", fontSize: 15, fontWeight: 700, whiteSpace: "nowrap", display: "flex", alignItems: "center", gap: 12 }}>
                  {toolCategory === "terminal" ? (
                    <motion.span animate={{ opacity: [1, 0.3, 1] }} transition={{ duration: 0.8, repeat: Infinity }} style={{ fontFamily: "monospace", fontSize: 16, fontWeight: 900, color: "#22c55e" }}>{">_"}</motion.span>
                  ) : (
                    <motion.div animate={{ rotate: 360 }} transition={{ duration: 1.2, repeat: Infinity, ease: "linear" }} style={{ width: 16, height: 16, border: "2px solid rgba(0,0,0,0.1)", borderTopColor: "#000000", borderRadius: "50%" }} />
                  )}
                  <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    {toolCategory !== "terminal" && (
                      <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#64748b" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
                    )}
                    {thinkingText || "processing..."}
                  </span>
                </div>
              )}
            </motion.div>
          )}
          {userIsTyping && !isThinking && !speaking && !isSleeping && !isNapping && (
            <motion.div initial={{ opacity: 0, scale: 0.8, y: 10 }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.8, y: 6 }} transition={{ duration: 0.2 }} style={{ position: "absolute", top: -55, background: "rgba(255,255,255,0.9)", border: "1px solid rgba(0, 0, 0, 0.08)", boxShadow: "0 4px 16px rgba(0,0,0,0.1)", padding: "6px 14px", borderRadius: "14px 14px 14px 4px", color: "#666", fontSize: 12, fontWeight: 600, zIndex: 100, display: "flex", alignItems: "center", gap: 6, pointerEvents: "none" }}>
              {[0, 1, 2].map(i => <motion.span key={i} animate={{ opacity: [0.3, 1, 0.3] }} transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}>.</motion.span>)}
            </motion.div>
          )}
          {pendingConfirmation && !isThinking && !speaking && !isSleeping && !isNapping && (
            <motion.div initial={{ opacity: 0, scale: 0.8, y: 10 }} animate={{ opacity: 1, scale: [1, 1.03, 1], y: 0 }} exit={{ opacity: 0, scale: 0.8, y: 6 }} transition={{ scale: { duration: 2, repeat: Infinity, ease: "easeInOut" }, opacity: { duration: 0.2 } }} style={{ position: "absolute", top: -60, background: "rgba(255,255,255,0.95)", border: "1px solid rgba(234,179,8,0.3)", boxShadow: "0 4px 16px rgba(234,179,8,0.15)", padding: "7px 14px", borderRadius: "14px 14px 14px 4px", color: "#92400e", fontSize: 12, fontWeight: 700, zIndex: 100, display: "flex", alignItems: "center", gap: 6, pointerEvents: "none" }}>
              <span style={{ fontSize: 14 }}>{"\u26A0\uFE0F"}</span>awaiting confirmation...
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── Idle mood props (subtle) ────────────────────────────────── */}
        <AnimatePresence>
          {isOnPhone && (
            <motion.div
              initial={{ opacity: 0, y: 10, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 8, scale: 0.92 }}
              transition={{ duration: 0.35 }}
              style={{
                position: "absolute",
                left: "50%",
                bottom: -20,
                marginLeft: -30,
                zIndex: 100,
                pointerEvents: "none",
              }}
            >
              {/* Back of phone: body + camera island + center logo */}
              <motion.div
                animate={{ y: [0, -2.5, 0], rotate: [-8, -5, -8] }}
                transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
                style={{
                  width: 60,
                  height: 104,
                  borderRadius: 14,
                  background: "linear-gradient(145deg, #2a2d33 0%, #14161a 48%, #0a0b0d 100%)",
                  boxShadow:
                    "0 12px 24px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.12), inset 0 -2px 8px rgba(0,0,0,0.45)",
                  border: "1px solid rgba(255,255,255,0.1)",
                  position: "relative",
                  overflow: "hidden",
                }}
              >
                {/* soft side edge */}
                <div
                  style={{
                    position: "absolute",
                    inset: 0,
                    borderRadius: 14,
                    boxShadow: "inset 0 0 0 1.5px rgba(255,255,255,0.06)",
                    pointerEvents: "none",
                  }}
                />

                {/* Camera island (top-left) */}
                <div
                  style={{
                    position: "absolute",
                    top: 10,
                    left: 10,
                    width: 26,
                    height: 26,
                    borderRadius: 8,
                    background: "linear-gradient(160deg, #3a3f48 0%, #1a1d24 100%)",
                    boxShadow: "0 2px 6px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.1)",
                    border: "1px solid rgba(255,255,255,0.08)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    gap: 3,
                  }}
                >
                  {/* main lens */}
                  <div
                    style={{
                      width: 11,
                      height: 11,
                      borderRadius: 999,
                      background: "radial-gradient(circle at 35% 30%, #4b5568 0%, #1f2937 45%, #0b0f14 100%)",
                      boxShadow: "inset 0 0 0 1.5px rgba(15,23,42,0.9), 0 0 0 1px rgba(255,255,255,0.06)",
                      position: "relative",
                    }}
                  >
                    <div
                      style={{
                        position: "absolute",
                        top: 2,
                        left: 2,
                        width: 3,
                        height: 3,
                        borderRadius: 999,
                        background: "rgba(147,197,253,0.45)",
                      }}
                    />
                  </div>
                  {/* flash / secondary */}
                  <div
                    style={{
                      position: "absolute",
                      bottom: 4,
                      right: 4,
                      width: 5,
                      height: 5,
                      borderRadius: 999,
                      background: "radial-gradient(circle at 40% 35%, #fef9c3 0%, #fde68a 40%, #a3a3a3 100%)",
                      boxShadow: "0 0 3px rgba(253,224,71,0.35)",
                    }}
                  />
                </div>

                {/* Center logo mark */}
                <div
                  style={{
                    position: "absolute",
                    left: "50%",
                    top: "52%",
                    transform: "translate(-50%, -50%)",
                    width: 18,
                    height: 18,
                    borderRadius: 5,
                    background: "linear-gradient(145deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0.03) 100%)",
                    boxShadow: "inset 0 1px 1px rgba(255,255,255,0.12)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <div
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: 3,
                      border: "1.5px solid rgba(255,255,255,0.28)",
                      background: "rgba(255,255,255,0.06)",
                    }}
                  />
                </div>
              </motion.div>
            </motion.div>
          )}
          {(isSleeping || isNapping) && (
            <div style={{ position: "absolute", top: -60, right: -20, pointerEvents: "none", zIndex: 100 }}>
              {[1, 2, 3].map((i) => (
                <motion.div key={`z-${i}`} initial={{ opacity: 0, y: 0, x: 0, scale: 0.5 }} animate={{ opacity: [0, 1, 0, 0], y: [-10, -70], x: [0, i % 2 === 0 ? 24 : -24], scale: [0.8, 1.7] }} exit={{ opacity: 0 }} transition={{ duration: 3, repeat: Infinity, delay: (i - 1) * 1, ease: "easeOut" }} style={{ position: "absolute", fontWeight: 900, fontSize: 24, color: "#e2e8f0", fontFamily: '"Comic Sans MS", "Chalkboard SE", monospace', textShadow: "0 2px 8px rgba(0,0,0,0.55)" }}>Z</motion.div>
              ))}
            </div>
          )}
          {isWakingUp && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: [0, 0.4, 0] }} exit={{ opacity: 0 }} transition={{ duration: 0.85 }} style={{ position: "absolute", inset: -8, borderRadius: 28, background: "radial-gradient(circle, rgba(251,191,36,0.28) 0%, transparent 70%)", pointerEvents: "none", zIndex: 50 }} />
          )}
        </AnimatePresence>

        {/* ── Hyper Mode Ring ─────────────────────────────────────────────── */}
        {isThinking && toolCategory !== "generic" && (
          <motion.div
            animate={{ opacity: [0.1, 0.3, 0.1] }} transition={{ duration: 1.5, repeat: Infinity, ease: "easeInOut" }}
            style={{ position: "absolute", inset: -6, borderRadius: 28, pointerEvents: "none", zIndex: 0,
              border: toolCategory === "terminal" ? "2px solid rgba(34,197,94,0.2)" : toolCategory === "search" || toolCategory === "browser" ? "2px solid rgba(59,130,246,0.2)" : toolCategory === "discord_read" || toolCategory === "discord_post" ? "2px solid rgba(88,101,242,0.2)" : toolCategory === "memory_store" || toolCategory === "memory_recall" ? "2px solid rgba(168,85,247,0.2)" : "2px solid rgba(255,255,255,0.1)",
            }}
          />
        )}
      </motion.div>
      {mergedAvatarConfig.custom_status_text ? (
        <div style={{ position: "absolute", bottom: 14, left: "50%", transform: "translateX(-50%)", padding: "6px 12px", borderRadius: 999, background: "rgba(10,10,10,0.65)", border: `1px solid ${mergedAvatarConfig.glow_color}33`, color: mergedAvatarConfig.body_color, fontSize: 11, fontWeight: 600, letterSpacing: 0.2, whiteSpace: "nowrap", backdropFilter: "blur(10px)" }}>
          {mergedAvatarConfig.custom_status_text}
        </div>
      ) : null}
    </div>
  );
});

SquareAvatarVisual.displayName = "SquareAvatarVisual";

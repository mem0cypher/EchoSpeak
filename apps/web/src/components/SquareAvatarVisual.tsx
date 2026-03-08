import React, { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence, Transition } from "framer-motion";
import { type ToolCategory, type EchoReaction, getToolIcon, isNightTime } from "./echoAnimationUtils";

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
    enable_particles?: boolean;
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
  enable_particles: boolean;
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
  enable_particles: true,
  enable_glow: true,
  enable_idle_activities: true,
  custom_status_text: "",
};

// ─── Animation Configs & Constants ──────────────────────────────────────────

// Organic smooth spring for general movement
const organicSpring: Transition = { type: "spring", stiffness: 120, damping: 20 };
// Snappy spring for blinks and fast reactions
const snappySpring: Transition = { type: "spring", stiffness: 400, damping: 25 };
// Gentle ease for breathing
const breathTransition: Transition = { duration: 3, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" };

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

type MicroBehavior = "look_left" | "look_right" | "look_up" | "look_down" | "curious_tilt" | "happy_bounce" | "spin_360" | "spin_dizzy" | "spin_shake" | "squint" | "wide_eyes" | "none";
const MICRO_BEHAVIORS: MicroBehavior[] = ["look_left", "look_right", "look_up", "look_down", "curious_tilt", "happy_bounce", "spin_360", "squint", "wide_eyes"];
const MICRO_BEHAVIOR_DURATIONS: Partial<Record<MicroBehavior, [number, number]>> = {
  look_down: [1200, 1800],
  curious_tilt: [1800, 2600],
  happy_bounce: [1800, 2400],
  squint: [1200, 1800],
  wide_eyes: [1200, 1800],
};

type IdleActivity = "none" | "gaming" | "gaming_intense" | "floating" | "napping" | "waking_up" | "vibing" | "stretching";
const ACTIVITY_POOL: IdleActivity[] = ["gaming","gaming","floating","napping","stretching"];
const ACT_DUR: Record<IdleActivity, [number, number]> = {
  none:           [25000, 45000], // Huge idle times so he mostly just sits and blinks
  gaming:         [12000, 20000],
  gaming_intense: [8000,  15000],
  floating:       [10000, 16000],
  napping:        [45000, 90000], // He sleeps for a long time when he decides to nap
  waking_up:      [1500,  1500],
  vibing:         [10000, 18000],
  stretching:     [4000,  6000],
};

function pickActivity(preferred?: string): IdleActivity {
  if (preferred && preferred !== "auto") {
    return (preferred === "none" ? "none" : preferred) as IdleActivity;
  }
  return ACTIVITY_POOL[Math.floor(Math.random() * ACTIVITY_POOL.length)];
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

  // ─── Spotify Vibing Override ─────────────────────────────────────────────
  // Echo vibes whenever Spotify reports music is playing.
  // When music stops (or spotifyPlaying becomes null), instantly go idle.
  const spotifyTrackRef = useRef<string>("");
  const spotifyVibingRef = useRef<boolean>(false);

  useEffect(() => {
    const trackId = spotifyPlaying?.track_id || "";
    const isPlaying = !!spotifyPlaying?.is_playing;

    if (!isPlaying) {
      // Music stopped or Spotify disconnected — release override immediately
      spotifyVibingRef.current = false;
      spotifyTrackRef.current = "";
      if (idleActivity === "vibing") {
        if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
        if (microTimerRef.current) clearTimeout(microTimerRef.current);
        setMicroBehavior("none");
        setIdleActivity("none");
      }
      return;
    }

    // New track started — Echo decides if he vibes
    if (trackId && trackId !== spotifyTrackRef.current) {
      spotifyTrackRef.current = trackId;
    }

    spotifyVibingRef.current = true;

    // If Echo decided to vibe, override idle activity
    if (spotifyVibingRef.current) {
      if (idleActivity !== "vibing") {
        if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
        if (microTimerRef.current) clearTimeout(microTimerRef.current);
        setMicroBehavior("none");
        setIdleActivity("vibing");
      }
    }
  }, [spotifyPlaying, idleActivity]);

  useEffect(() => {
    if (!mergedAvatarConfig.enable_idle_activities) {
      if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
      setIdleActivity("none");
      return;
    }
    if (mergedAvatarConfig.idle_activity && mergedAvatarConfig.idle_activity !== "auto") {
      if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
      setIdleActivity(mergedAvatarConfig.idle_activity as IdleActivity);
    }
  }, [mergedAvatarConfig.enable_idle_activities, mergedAvatarConfig.idle_activity]);

  // ─── Derived States ───────────────────────────────────────────────────────
  // Sleep only when offline. Napping is the voluntary activity.
  const isSleeping = backendOnline === false;
  const isNapping = idleActivity === "napping";
  const isWakingUp = idleActivity === "waking_up";
  const isGaming = idleActivity === "gaming" || idleActivity === "gaming_intense";
  const isFloating = idleActivity === "floating";
  const isVibing = idleActivity === "vibing";
  const isStretching = idleActivity === "stretching";
  const isActive = speaking || isThinking || userIsTyping || pendingConfirmation || activeReaction !== null;

  // ─── Idle Activity Engine ─────────────────────────────────────────────────
  const cycleToNext = useCallback((prev: IdleActivity) => {
    if (activityTimerRef.current) clearTimeout(activityTimerRef.current);

    // Don't cycle if Spotify vibing is active — let the override hold
    if (spotifyVibingRef.current) return;

    // If we just finished napping, we must wake up
    if (prev === "napping") {
      setIdleActivity("waking_up");
      activityTimerRef.current = window.setTimeout(() => cycleToNext("waking_up"), ACT_DUR.waking_up[0]);
      return;
    }

    // ALTERNATING LOGIC:
    // If we just finished an activity (or waking up), go into a long "none" phase (just blinking/breathing).
    // If we just finished a long "none" phase, pick a short activity.
    if (prev !== "none" && prev !== "waking_up") {
      setIdleActivity("none");
      const [mn, mx] = ACT_DUR.none;
      activityTimerRef.current = window.setTimeout(() => cycleToNext("none"), mn + Math.random() * (mx - mn));
    } else {
      const next = pickActivity(mergedAvatarConfig.idle_activity);
      setIdleActivity(next);
      const [mn, mx] = ACT_DUR[next];
      activityTimerRef.current = window.setTimeout(() => cycleToNext(next), mn + Math.random() * (mx - mn));
    }
  }, [mergedAvatarConfig.idle_activity]);

  // Main lifecycle for idle engine
  useEffect(() => {
    if (isActive) {
      // Pause idle cycle while busy
      setIdleActivity("none");
      setMicroBehavior("none");
      if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
      if (microTimerRef.current) clearTimeout(microTimerRef.current);
      if (microPhaseTimerRef.current) clearTimeout(microPhaseTimerRef.current);
      return;
    }

    if (!mergedAvatarConfig.enable_idle_activities) return;
    if (spotifyVibingRef.current) return;

    // Start purely idle
    setIdleActivity("none");
    setMicroBehavior("none");

    // Initial delay before first random activity
    const [mn, mx] = ACT_DUR.none;
    activityTimerRef.current = window.setTimeout(() => cycleToNext("none"), (mn + Math.random() * (mx - mn)) / 2); // start halfway through a none cycle

    return () => {
      if (microTimerRef.current) clearTimeout(microTimerRef.current);
      if (microPhaseTimerRef.current) clearTimeout(microPhaseTimerRef.current);
      if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
    };
  }, [isActive, cycleToNext, mergedAvatarConfig.enable_idle_activities]);

  useEffect(() => {
    if (isActive || idleActivity !== "none" || isSleeping || isNapping) {
      if (microTimerRef.current) clearTimeout(microTimerRef.current);
      if (microBehavior !== "none") setMicroBehavior("none");
      return;
    }
    if (microBehavior !== "none") return;

    const delay = 5000 + Math.random() * 10000;
    microTimerRef.current = window.setTimeout(() => {
      setMicroBehavior(MICRO_BEHAVIORS[Math.floor(Math.random() * MICRO_BEHAVIORS.length)]);
    }, delay);

    return () => {
      if (microTimerRef.current) clearTimeout(microTimerRef.current);
    };
  }, [isActive, idleActivity, microBehavior, isSleeping, isNapping]);

  useEffect(() => {
    if (microPhaseTimerRef.current) clearTimeout(microPhaseTimerRef.current);
    if (isActive || idleActivity !== "none" || isSleeping || isNapping) return;

    if (microBehavior === "spin_360") {
      microPhaseTimerRef.current = window.setTimeout(() => setMicroBehavior("spin_dizzy"), 720);
    } else if (microBehavior === "spin_dizzy") {
      microPhaseTimerRef.current = window.setTimeout(() => setMicroBehavior("spin_shake"), 1350);
    } else if (microBehavior === "spin_shake") {
      microPhaseTimerRef.current = window.setTimeout(() => setMicroBehavior("none"), 650);
    } else if (microBehavior !== "none") {
      const [mn, mx] = MICRO_BEHAVIOR_DURATIONS[microBehavior] ?? [1400, 2100];
      microPhaseTimerRef.current = window.setTimeout(() => setMicroBehavior("none"), mn + Math.random() * (mx - mn));
    }

    return () => {
      if (microPhaseTimerRef.current) clearTimeout(microPhaseTimerRef.current);
    };
  }, [isActive, idleActivity, microBehavior]);

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

  // ─── Blink Engine (Untouched rhythm, just decoupled from shapes) ────────
  useEffect(() => {
    let timeout: number;
    const scheduleBlink = () => {
      if (!isSleeping && !isNapping && !isThinking && activeReaction !== "error" && microBehavior !== "spin_360" && microBehavior !== "spin_dizzy" && microBehavior !== "spin_shake") {
        setBlink(true);
        setTimeout(() => setBlink(false), 150);
      }
      timeout = window.setTimeout(scheduleBlink, 2000 + Math.random() * 4000);
    };
    timeout = window.setTimeout(scheduleBlink, 1000);
    return () => window.clearTimeout(timeout);
  }, [isSleeping, isNapping, isThinking, activeReaction, microBehavior]);

  // ─── Animation Generators (Framer Motion Native) ──────────────────────────

  // 1. Eye Animation Props
  const getEyeProps = () => {
    // Defaults: resting blinky state
    let w: number | number[] = 20, h: number | number[] = 24, r: number | number[] = 12;
    let x: number | number[] = 0, y: number | number[] = 0;
    let tX: Transition = organicSpring;
    let tY: Transition = organicSpring;
    let tShape: Transition = organicSpring;

    // --- State Overrides (Hierarchy) ---

    // Shape overrides
    if (isSleeping || isNapping) { h = 4; r = 4; w = 24; }
    else if (isWakingUp) { w = 26; h = 28; r = 14; }
    else if (activeReaction === "error") { w = 24; h = 28; r = 14; }
    else if (activeReaction === "success") { w = 22; h = 6; r = 6; }
    else if (activeReaction === "memory_saved") { w = 20; h = 8; r = 8; }
    else if (userIsTyping) { w = 20; h = 20; r = 10; } // Focused eyes for listening
    else if (pendingConfirmation) { w = 20; h = 24; r = 12; }
    else if (isVibing) { w = 22; h = 10; r = 6; }
    else if (isStretching) { w = 24; h = 14; r = 8; }
    else if (isFloating) { w = 20; h = 22; r = 11; }
    else if (microBehavior === "look_down") { w = 20; h = 20; r = 10; }
    else if (microBehavior === "curious_tilt") { w = 22; h = 22; r = 11; }
    else if (microBehavior === "happy_bounce") { w = 22; h = 16; r = 8; }
    else if (microBehavior === "spin_360") { w = 22; h = 22; r = 11; }
    else if (microBehavior === "spin_dizzy") { w = 20; h = 20; r = 10; }
    else if (microBehavior === "spin_shake") { w = 22; h = 22; r = 11; }
    else if (microBehavior === "squint") { w = 22; h = 10; r = 6; }
    else if (microBehavior === "wide_eyes") { w = 24; h = 28; r = 14; }
    else if (isThinking) {
      switch (toolCategory) {
        case "search": w=20; h=22; r=11; break;
        case "terminal": w=18; h=16; r=8; break;
        case "browser": w=24; h=26; r=13; break;
        case "memory_store": w=20; h=10; r=8; break;
        case "memory_recall": w=22; h=26; r=13; break;
        case "file_write": w=18; h=18; r=9; break;
        case "discord_post": w=18; h=20; r=10; break;
      }
    }

    // ** CRITICAL: Blink overrides any active shape **
    if (blink && !isSleeping && !isNapping) {
      h = 3; r = 4;
      tShape = snappySpring; // Fast snap shut and open
    }

    // Position overrides
    if (isSleeping || isNapping) { x = 0; y = 0; }
    else if (isWakingUp) { y = [0, -3, 0]; tY = { duration: 0.4, ease: "easeOut" }; }
    else if (activeReaction === "error") { x = [0, -4, 4, -2, 0]; tX = { duration: 0.4 }; }
    else if (userIsTyping) {
      // Leaning forward to listen
      x = 4; y = -2;
    }
    else if (microBehavior === "spin_dizzy") {
      x = [0, 7, 0, -7, 0];
      y = [0, -4, 0, 4, 0];
      tX = { duration: 1.6, repeat: Infinity, ease: "easeInOut" };
      tY = { duration: 0.8, repeat: Infinity, ease: "easeInOut" };
    }
    else if (microBehavior === "look_down") {
      y = 6;
    }
    else if (microBehavior === "curious_tilt") {
      x = 4;
      y = -2;
    }
    else if (microBehavior === "happy_bounce") {
      y = [0, -2, 0];
      tY = { duration: 1.1, repeat: Infinity, ease: "easeInOut" };
    }
    else if (microBehavior === "spin_360" || microBehavior === "spin_shake") {
      x = 0;
      y = 0;
    }
    else if (isThinking) {
      if (toolCategory === "discord_post" || toolCategory === "file_write") {
        // Typing squint
        w = 22; h = 8; r = 6;
      } else if (toolCategory === "search" || toolCategory === "browser" || toolCategory === "discord_read" || toolCategory === "file_read") {
        // Reading/scanning eyes
        w = 22; h = 24; r = 12;
        x = [-8, 8, -6, 6, -8]; 
        tX = { duration: 1.5, repeat: Infinity, ease: "easeInOut" };
      } else if (toolCategory === "terminal") {
        // Terminal focus
        w = 20; h = 18; r = 8;
        y = -2;
      } else {
        const cfg = TOOL_EYE_ANIMS[toolCategory] || TOOL_EYE_ANIMS.generic;
        x = cfg.x; y = cfg.y;
        if (cfg.durX > 0) tX = { duration: cfg.durX, repeat: Infinity, ease: "easeInOut" };
        if (cfg.durY > 0) tY = { duration: cfg.durY, repeat: Infinity, ease: "easeInOut" };
      }
    }
    else if (isGaming && !speaking) {
      w = 18; h = 8; r = 5;
      y = [9, 10, 9]; x = [0, 1, 0, -1, 0];
      tY = { duration: 2.4, repeat: Infinity, ease: "easeInOut" };
      tX = { duration: 2.8, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isFloating) { x = [-6, 0, 6, 0, -6]; tX = { duration: 3.0, repeat: Infinity, ease: "easeInOut" }; }
    else if (isStretching) { y = [-4, 0]; tY = { duration: 0.8 }; }
    else if (microBehavior === "look_left") { x = -8; }
    else if (microBehavior === "look_right") { x = 8; }
    else if (microBehavior === "look_up") { y = -6; }

    return {
      animate: { x, y, width: w, height: h, borderRadius: r },
      transition: { x: tX, y: tY, width: tShape, height: tShape, borderRadius: tShape }
    };
  };

  // 2. Mouth Animation Props
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
    else if (isVibing) {
      w = [20, 24, 20]; h = [4, 6, 4]; t = { duration: 0.6, repeat: Infinity };
    }
    else if (isStretching) {
      w = [24, 20]; h = [16, 6]; r = [14, 10]; t = { duration: 1.5 };
    }
    else if (isGaming && !speaking) {
      w = 12; h = 8; r = 6; // Slack open mouth for brainrot scrolling
    }
    else if (microBehavior === "spin_dizzy") {
      w = [14, 18, 14]; h = [4, 6, 4]; r = [8, 9, 8]; t = { duration: 1.2, repeat: Infinity, ease: "easeInOut" };
    }
    else if (microBehavior === "spin_shake") {
      w = 14; h = 3; r = 6;
    }
    else if (isThinking) { w = 16; h = 4; }
    else if (userIsTyping) {
      // Small focused mouth
      w = 12; h = 4; r = 6;
    }
    else if (pendingConfirmation) { w = 14; h = 8; }
    else if (microBehavior === "curious_tilt") { w = 18; h = 5; }
    else if (microBehavior === "happy_bounce") {
      w = [20, 24, 20]; h = [5, 8, 5]; r = [10, 12, 10]; t = { duration: 1.1, repeat: Infinity, ease: "easeInOut" };
    }

    return { animate: { width: w, height: h, borderRadius: r }, transition: t };
  };

  // 3. Body Animation Props
  const getBodyProps = () => {
    let x: number | number[] = 0, y: number | number[] = 0, rot: number | number[] = 0;
    let tX: Transition = organicSpring, tY: Transition = organicSpring, tR: Transition = organicSpring;

    if (activeReaction === "error") {
      y = [0, -4, 4, -2, 0]; rot = [0, -3, 3, -1, 0];
      tY = { duration: 0.5 }; tR = { duration: 0.5 };
    }
    else if (activeReaction === "success") {
      y = [0, -10, 0]; tY = { duration: 0.6 };
    }
    else if (isWakingUp) {
      y = [0, -15, -5, 0]; tY = { duration: 0.6, ease: "easeOut" };
    }
    else if (speaking) {
      y = [0, -8, 0]; tY = { duration: 0.8, repeat: Infinity, ease: "easeInOut" };
    }
    else if (userIsTyping) {
      // Listening lean
      y = [0, -4, 0]; rot = -4; tY = { duration: 1.5, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isThinking) {
      if (toolCategory === "discord_post" || toolCategory === "file_write") {
        // Frantic typing bounce
        y = [0, -4, 0, -2, 0];
        rot = [0, -1, 1, 0, 0];
        tY = { duration: 0.6, repeat: Infinity, ease: "easeInOut" };
        tR = { duration: 0.6, repeat: Infinity, ease: "easeInOut" };
      } else if (toolCategory === "search" || toolCategory === "browser") {
        // Leaning in, focused
        y = [0, -4, 0]; tY = { duration: 1.2, repeat: Infinity, ease: "easeInOut" };
      } else {
        // Generic thinking bounce
        y = [0, -2, 0]; tY = { duration: 1.5, repeat: Infinity, ease: "easeInOut" };
      }
    }
    else if (isNapping || isSleeping) {
      y = [0, -4, 0]; rot = [1, -1, 1]; // Full loop back to start to prevent snapping
      tY = { duration: 3, repeat: Infinity, ease: "easeInOut" };
      tR = { duration: 4, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isGaming) {
      if (idleActivity === "gaming_intense") {
        y = [0, -6, 0, -4, 0]; rot = [-2, 2, -1, 1, -2];
        tY = { duration: 0.8, repeat: Infinity }; tR = { duration: 0.5, repeat: Infinity };
      } else {
        y = [6, 8, 6];
        rot = [-2, 1, -2];
        tY = { duration: 2.4, repeat: Infinity, ease: "easeInOut" };
        tR = { duration: 2.8, repeat: Infinity, ease: "easeInOut" };
      }
    }
    else if (isFloating) {
      y = [0, -6, 0]; x = [-15, 15, -15]; rot = [-4, 4, -4];
      tY = { duration: 4, repeat: Infinity, ease: "easeInOut" };
      tX = { duration: 6, repeat: Infinity, ease: "easeInOut" };
      tR = { duration: 5, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isVibing) {
      y = [0, -5, 0]; x = [-2, 2, -2]; rot = [-3, 3, -3];
      tY = { duration: 0.7, repeat: Infinity, ease: "easeInOut" };
      tX = { duration: 0.7, repeat: Infinity, ease: "easeInOut" };
      tR = { duration: 0.7, repeat: Infinity, ease: "easeInOut" };
    }
    else if (isStretching) {
      y = [0, -12, 0]; tY = { duration: 2, ease: "easeInOut" };
    }
    else if (microBehavior === "look_down") {
      y = 1;
    }
    else if (microBehavior === "curious_tilt") {
      x = 3;
      y = -1;
      rot = -6;
    }
    else if (microBehavior === "happy_bounce") {
      y = [0, -5, 0, -2, 0];
      rot = [0, -1, 1, 0, 0];
      tY = { duration: 1.1, repeat: Infinity, ease: "easeInOut" };
      tR = { duration: 1.1, repeat: Infinity, ease: "easeInOut" };
    }
    else if (microBehavior === "spin_360") {
      rot = [0, 120, 240, 360];
      y = [0, -6, 0];
      tR = { duration: 0.72, ease: "easeInOut" };
      tY = { duration: 0.72, ease: "easeInOut" };
    }
    else if (microBehavior === "spin_dizzy") {
      rot = [0, -4, 4, -3, 3, 0];
      y = [0, -1, 0, 1, 0];
      tR = { duration: 1.2, repeat: Infinity, ease: "easeInOut" };
      tY = { duration: 1.2, repeat: Infinity, ease: "easeInOut" };
    }
    else if (microBehavior === "spin_shake") {
      x = [0, -2, 2, -1, 1, 0];
      rot = [0, -12, 12, -10, 10, -6, 6, 0];
      y = [0, -1, 0];
      tX = { duration: 0.8, ease: "easeInOut" };
      tR = { duration: 0.8, ease: "easeInOut" };
      tY = { duration: 0.8, ease: "easeInOut" };
    }
    else {
      // BASE IDLE BREATHING (The core "sitting there" loop)
      y = [0, -3, 0]; tY = { duration: breathDuration, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" };
    }

    return {
      animate: { x, y, rotate: rot },
      transition: { x: tX, y: tY, rotate: tR }
    };
  };

  // ─── Style Derivations ────────────────────────────────────────────────────
  const getBodyStyle = () => {
    const dimFactor = nightMode && !isActive ? 0.85 : 1;
    let bg = `linear-gradient(135deg, ${mergedAvatarConfig.body_color} 0%, ${mergedAvatarConfig.body_color}dd 100%)`;
    let shadow = `0 0 18px ${mergedAvatarConfig.glow_color}40, inset 0 0 20px rgba(0,0,0,0.05)`;

    if (isSleeping || isNapping) { bg = "linear-gradient(135deg, #e2e8f0 0%, #cbd5e1 100%)"; shadow = "0 0 10px rgba(0,0,0,0.1), inset 0 0 20px rgba(0,0,0,0.05)"; }
    else if (isWakingUp) { bg = "linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%)"; shadow = "0 0 20px rgba(251,191,36,0.25), inset 0 0 20px rgba(0,0,0,0.03)"; }
    else if (speaking) { bg = "linear-gradient(135deg, #ffffff 0%, #e2e8f0 100%)"; shadow = "0 0 20px rgba(255,255,255,0.3), inset 0 0 20px rgba(0,0,0,0.05)"; }
    else if (activeReaction === "success") { bg = "linear-gradient(135deg, #f0fdf4 0%, #bbf7d0 100%)"; shadow = "0 0 24px rgba(34,197,94,0.3), inset 0 0 20px rgba(0,0,0,0.03)"; }
    else if (activeReaction === "error") { bg = "linear-gradient(135deg, #fef2f2 0%, #fecaca 100%)"; shadow = "0 0 24px rgba(239,68,68,0.3), inset 0 0 20px rgba(0,0,0,0.03)"; }
    else if (activeReaction === "memory_saved") { bg = "linear-gradient(135deg, #eff6ff 0%, #bfdbfe 100%)"; shadow = "0 0 24px rgba(59,130,246,0.3), inset 0 0 20px rgba(0,0,0,0.03)"; }
    else if (isVibing) { bg = "linear-gradient(135deg, #fdf4ff 0%, #e9d5ff 100%)"; shadow = "0 0 16px rgba(168,85,247,0.15), inset 0 0 20px rgba(0,0,0,0.03)"; }
    else if (isThinking && toolCategory === "terminal") { bg = "linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%)"; shadow = "0 0 12px rgba(34,197,94,0.15), inset 0 0 20px rgba(0,0,0,0.05)"; }
    else if (userIsTyping) { bg = "linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%)"; shadow = "inset 0 0 20px rgba(0,0,0,0.08)"; } // Neutral/listening color instead of yellow

return { background: bg, boxShadow: mergedAvatarConfig.enable_glow ? shadow : "inset 0 0 20px rgba(0,0,0,0.05)", opacity: dimFactor };
};

const bodyStyle = getBodyStyle();
const eyeProps = getEyeProps();
const mouthProps = getMouthProps();
const bodyProps = getBodyProps();
const showEyebrow = pendingConfirmation && !isSleeping && !speaking;

return (
<div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", position: "relative", background: `radial-gradient(circle at center, ${mergedAvatarConfig.bg_color} 0%, rgba(0,0,0,0) 72%)` }}>
      
      {/* ── Effects Layers ────────────────────────────────────────────────── */}
      <AnimatePresence>
        {activeReaction === "success" && (
          <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1.4, opacity: [0, 0.6, 0] }} exit={{ opacity: 0 }} transition={{ duration: 0.7 }} style={{ position: "absolute", width: 160, height: 160, borderRadius: 30, border: "3px solid rgba(34,197,94,0.5)", zIndex: 50, pointerEvents: "none" }} />
        )}
        {activeReaction === "error" && (
          <motion.div initial={{ scale: 1, opacity: 0.5 }} animate={{ scale: [1, 1.1, 1], opacity: [0.5, 0.3, 0] }} exit={{ opacity: 0 }} transition={{ duration: 0.8 }} style={{ position: "absolute", width: 160, height: 160, borderRadius: 30, border: "3px solid rgba(239,68,68,0.4)", zIndex: 50, pointerEvents: "none" }} />
        )}
      </AnimatePresence>

      {mergedAvatarConfig.enable_particles && !isThinking && !isSleeping && !isNapping && (
        <div style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
          {[0, 1, 2].map((i) => (
            <motion.div
              key={`particle-${i}`}
              animate={{ y: [0, -18, 0], x: [0, i === 1 ? 10 : -10, 0], opacity: [0.18, 0.5, 0.18] }}
              transition={{ duration: 3 + i, repeat: Infinity, ease: "easeInOut", delay: i * 0.35 }}
              style={{
                position: "absolute",
                top: `${28 + i * 14}%`,
                left: `${32 + i * 14}%`,
                width: 8,
                height: 8,
                borderRadius: 999,
                background: mergedAvatarConfig.glow_color,
                filter: "blur(1px)",
              }}
            />
          ))}
        </div>
      )}

      {/* ── Main Body ─────────────────────────────────────────────────────── */}
      <motion.div
        animate={bodyProps.animate}
        transition={bodyProps.transition}
        style={{
          width: 140, height: 140,
          borderRadius: mergedAvatarConfig.body_roundness,
          border: mergedAvatarConfig.enable_glow ? `4px solid ${mergedAvatarConfig.glow_color}22` : "4px solid rgba(255,255,255,0.08)",
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          position: "relative",
          zIndex: 60,
        }}
      >
        {/* Animated background to allow smooth color crossfades */}
        <motion.div
          animate={{ background: bodyStyle.background, boxShadow: bodyStyle.boxShadow, opacity: bodyStyle.opacity }}
          transition={{ duration: 0.6 }}
          style={{ position: "absolute", inset: -4, borderRadius: mergedAvatarConfig.body_roundness, zIndex: -1 }}
        />

        {/* ── Eyebrow ── */}
        {showEyebrow && (
          <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} style={{ position: "absolute", top: 22, left: 62, width: 16, height: 3, background: "#000", borderRadius: 2, transform: "rotate(-15deg)", zIndex: 10 }} />
        )}

        {/* ── Eyes ── */}
        <div style={{ display: "flex", gap: Math.max(22, 28 + mergedAvatarConfig.eye_size * 6), marginTop: -20, zIndex: 2 }}>
          <motion.div animate={eyeProps.animate} transition={eyeProps.transition} style={{ background: mergedAvatarConfig.eye_color, boxShadow: mergedAvatarConfig.enable_glow ? `0 0 12px ${mergedAvatarConfig.glow_color}35` : "0 4px 6px rgba(0,0,0,0.1)", scale: mergedAvatarConfig.eye_size }} />
          <motion.div animate={eyeProps.animate} transition={eyeProps.transition} style={{ background: mergedAvatarConfig.eye_color, boxShadow: mergedAvatarConfig.enable_glow ? `0 0 12px ${mergedAvatarConfig.glow_color}35` : "0 4px 6px rgba(0,0,0,0.1)", scale: mergedAvatarConfig.eye_size }} />
        </div>

        {/* ── Mouth ── */}
        <motion.div animate={mouthProps.animate} transition={mouthProps.transition} style={{ marginTop: 24, background: mergedAvatarConfig.eye_color, boxShadow: mergedAvatarConfig.enable_glow ? `0 0 10px ${mergedAvatarConfig.glow_color}25` : "0 4px 6px rgba(0,0,0,0.1)", zIndex: 2, opacity: 0.92 }} />

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

        {/* ── Idle Activities Addons ──────────────────────────────────────── */}
        <AnimatePresence>
          {isGaming && (
            <motion.div
              initial={{ opacity: 0, y: 10, scale: 0.9 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 10, scale: 0.9 }}
              style={{
                position: "absolute",
                left: "50%",
                bottom: -16,
                marginLeft: -44,
                zIndex: 100,
                pointerEvents: "none"
              }}
            >
              <div style={{ position: "absolute", left: 8, top: -26, width: 72, display: "flex", justifyContent: "center", gap: 6 }}>
                {[
                  { label: "•••", delay: 0, width: 18 },
                  { label: "1", delay: 0.28, width: 14 },
                  { label: "♥", delay: 0.56, width: 16 },
                ].map((item) => (
                  <motion.div
                    key={`${item.label}-${item.delay}`}
                    animate={{ y: [6, -3, -12], opacity: [0, 1, 0], scale: [0.85, 1, 0.92] }}
                    transition={{ duration: 1.8, repeat: Infinity, delay: item.delay, ease: "easeOut" }}
                    style={{
                      minWidth: item.width,
                      height: 16,
                      padding: "0 6px",
                      borderRadius: 999,
                      background: "rgba(255,255,255,0.92)",
                      boxShadow: "0 4px 12px rgba(0,0,0,0.12)",
                      color: item.label === "♥" ? "#f43f5e" : "#0f172a",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 10,
                      fontWeight: 800,
                      letterSpacing: item.label === "•••" ? 0.5 : 0,
                    }}
                  >
                    {item.label}
                  </motion.div>
                ))}
              </div>
               <motion.div
                 animate={{ y: [0, -3, 0], rotate: [-2, 0, -2], scale: [1, 1.015, 1] }}
                 transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
                 style={{ width: 88, height: 104, position: "relative" }}
               >
                <motion.div
                  animate={{ y: [0, -1.2, 0], rotate: [-8, -6, -8] }}
                  transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
                  style={{
                    position: "absolute",
                    left: 8,
                    bottom: 14,
                    width: 22,
                    height: 38,
                    borderRadius: 999,
                    background: "linear-gradient(180deg, #f8fafc 0%, #e2e8f0 100%)",
                    boxShadow: "inset -2px -3px 6px rgba(148,163,184,0.35)",
                    transform: "rotate(-8deg)",
                    zIndex: 1,
                  }}
                />
                <motion.div
                  animate={{ y: [0, -1.4, 0], rotate: [8, 6, 8] }}
                  transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
                  style={{
                    position: "absolute",
                    right: 8,
                    bottom: 14,
                    width: 22,
                    height: 38,
                    borderRadius: 999,
                    background: "linear-gradient(180deg, #f8fafc 0%, #e2e8f0 100%)",
                    boxShadow: "inset -2px -3px 6px rgba(148,163,184,0.3)",
                    transform: "rotate(8deg)",
                    zIndex: 1,
                  }}
                />
                <motion.div
                  animate={{ y: [0, -2, 0], rotate: [-6, -3, -6] }}
                  transition={{ duration: 1.15, repeat: Infinity, ease: "easeInOut" }}
                  style={{
                    position: "absolute",
                    left: 18,
                    top: 12,
                    width: 52,
                    height: 82,
                    borderRadius: 16,
                    background: "linear-gradient(145deg, #334155 0%, #1e293b 48%, #0f172a 100%)",
                    boxShadow: "10px 12px 18px rgba(15,23,42,0.32), inset 1px 1px 0 rgba(255,255,255,0.16)",
                    overflow: "hidden",
                    transformStyle: "preserve-3d",
                    zIndex: 3,
                  }}
                >
                  <div style={{ position: "absolute", inset: 3, borderRadius: 13, background: "linear-gradient(160deg, #64748b 0%, #334155 30%, #1e293b 68%, #0f172a 100%)" }} />
                  <div style={{ position: "absolute", top: 9, left: 8, width: 19, height: 19, borderRadius: 10, background: "rgba(15,23,42,0.72)", boxShadow: "inset 0 1px 0 rgba(255,255,255,0.1)" }}>
                    <div style={{ position: "absolute", top: 3, left: 3, width: 5, height: 5, borderRadius: 999, background: "#94a3b8", boxShadow: "0 0 0 2px rgba(15,23,42,0.45)" }} />
                    <div style={{ position: "absolute", top: 3, right: 3, width: 5, height: 5, borderRadius: 999, background: "#cbd5e1", boxShadow: "0 0 0 2px rgba(15,23,42,0.45)" }} />
                    <div style={{ position: "absolute", bottom: 3, left: 7, width: 5, height: 5, borderRadius: 999, background: "#e2e8f0", boxShadow: "0 0 0 2px rgba(15,23,42,0.45)" }} />
                  </div>
                  <div style={{ position: "absolute", top: 15, left: 31, width: 5, height: 5, borderRadius: 999, background: "rgba(248,250,252,0.7)" }} />
                  <motion.div
                    animate={{ opacity: [0.14, 0.22, 0.14], x: [-2, 1, -2] }}
                    transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
                    style={{
                      position: "absolute",
                      top: -6,
                      bottom: -6,
                      left: 11,
                      width: 16,
                      background: "linear-gradient(180deg, rgba(255,255,255,0.28) 0%, rgba(255,255,255,0.03) 100%)",
                      transform: "skewX(-12deg)",
                    }}
                  />
                  <div style={{ position: "absolute", right: 0, top: 7, bottom: 7, width: 5, background: "linear-gradient(180deg, rgba(15,23,42,0.4) 0%, rgba(2,6,23,0.85) 100%)" }} />
                  <div style={{ position: "absolute", left: 19, bottom: 14, width: 14, height: 14, borderRadius: 999, border: "1px solid rgba(255,255,255,0.12)", opacity: 0.5 }} />
                  <div style={{ position: "absolute", bottom: 8, left: "50%", marginLeft: -8, width: 16, height: 2, borderRadius: 999, background: "rgba(255,255,255,0.09)" }} />
                </motion.div>
              </motion.div>
            </motion.div>
          )}
          {(isSleeping || isNapping) && (
            <div style={{ position: "absolute", top: -60, right: -20, pointerEvents: "none", zIndex: 100 }}>
              {[1, 2, 3].map((i) => (
                <motion.div key={`z-${i}`} initial={{ opacity: 0, y: 0, x: 0, scale: 0.5 }} animate={{ opacity: [0, 1, 0, 0], y: [-10, -80], x: [0, i % 2 === 0 ? 30 : -30], scale: [0.8, 2.0] }} exit={{ opacity: 0 }} transition={{ duration: 3, repeat: Infinity, delay: (i - 1) * 1, ease: "easeOut" }} style={{ position: "absolute", fontWeight: "900", fontSize: 28, color: "#e2e8f0", fontFamily: '"Comic Sans MS", "Chalkboard SE", monospace', textShadow: "0 4px 12px rgba(255,255,255,0.4), 0 2px 4px rgba(0,0,0,0.8)" }}>Z</motion.div>
              ))}
            </div>
          )}
          {isWakingUp && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: [0, 0.5, 0] }} exit={{ opacity: 0 }} transition={{ duration: 0.8 }} style={{ position: "absolute", inset: -8, borderRadius: 28, background: "radial-gradient(circle, rgba(251,191,36,0.3) 0%, transparent 70%)", pointerEvents: "none", zIndex: 50 }} />
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

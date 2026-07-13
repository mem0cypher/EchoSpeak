const fixtureState = { ready: true };

export function fixtureStatus() {
  return fixtureState.ready ? "ready" : "paused";
}

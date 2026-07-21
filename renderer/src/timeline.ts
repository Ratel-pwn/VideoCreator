export const sceneAtFrame = <
  T extends {fromFrame: number; durationInFrames: number},
>(
  scenes: T[],
  frame: number,
) =>
  scenes.find(
    (scene) =>
      frame >= scene.fromFrame &&
      frame < scene.fromFrame + scene.durationInFrames,
  );

export const assertContinuousTimeline = (
  scenes: Array<{id: string; fromFrame: number; durationInFrames: number}>,
  durationInFrames: number,
) => {
  let cursor = 0;
  for (const scene of scenes) {
    if (scene.fromFrame !== cursor) {
      throw new Error(
        `${scene.id}: expected frame ${cursor}, got ${scene.fromFrame}`,
      );
    }
    cursor += scene.durationInFrames;
  }
  if (cursor !== durationInFrames) {
    throw new Error(`Timeline ends at ${cursor}, expected ${durationInFrames}`);
  }
};

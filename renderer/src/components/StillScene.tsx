import type {CSSProperties} from 'react';
import {AbsoluteFill, Img, interpolate, staticFile} from 'remotion';
import type {RenderScene} from '../schema';

export const StillScene = ({scene, frame}: {scene: RenderScene; frame: number}) => {
  const progress = interpolate(
    frame,
    [0, Math.max(1, scene.durationInFrames - 1)],
    [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  const translate = 1.5 - progress * 3;
  const signedTranslate =
    scene.motionPreset === 'none'
      ? 0
      : scene.motionPreset === 'push-right'
        ? -translate
        : translate;
  const scale = interpolate(progress, [0, 1], [1, 1.05]);
  const style: CSSProperties = {
    width: '100%',
    height: '100%',
    objectFit: scene.fitMode,
    transform: `scale(${scale}) translateX(${signedTranslate}%)`,
  };

  return (
    <AbsoluteFill>
      <Img src={staticFile(scene.assetPath)} style={style} />
    </AbsoluteFill>
  );
};

import {AbsoluteFill, useCurrentFrame} from 'remotion';
import type {RenderScene} from '../schema';
import {StillScene} from './StillScene';
import {VideoScene} from './VideoScene';

export const Scene = ({scene}: {scene: RenderScene}) => {
  const frame = useCurrentFrame();
  if (scene.assetType === 'image') {
    return <StillScene scene={scene} frame={frame} />;
  }
  if (scene.assetType === 'video') {
    return <VideoScene scene={scene} />;
  }
  return <AbsoluteFill />;
};

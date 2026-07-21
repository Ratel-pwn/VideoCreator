import {AbsoluteFill, useCurrentFrame} from 'remotion';
import type {AnyRenderScene} from '../schema';
import {EntityCardScene} from './EntityCardScene';
import {FullBleedScene} from './FullBleedScene';
import {ExplainerScene} from './ExplainerScene';
import {StillScene} from './StillScene';
import {VideoScene} from './VideoScene';

export const Scene = ({scene}: {scene: AnyRenderScene}) => {
  const frame = useCurrentFrame();
  if ('presentationMode' in scene) {
    if (scene.presentationMode === 'footage' || scene.presentationMode === 'still') return <FullBleedScene scene={scene} />;
    if (scene.presentationMode === 'entity_card') return <EntityCardScene scene={scene} />;
    if (scene.presentationMode === 'explainer') return <ExplainerScene scene={scene} frame={frame} />;
    return <AbsoluteFill />;
  }
  if (scene.assetType === 'image') {
    return <StillScene scene={scene} frame={frame} />;
  }
  if (scene.assetType === 'video') {
    return <VideoScene scene={scene} />;
  }
  return <AbsoluteFill />;
};

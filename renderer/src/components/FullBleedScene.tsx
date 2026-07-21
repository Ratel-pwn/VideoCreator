import {Video} from '@remotion/media';
import {AbsoluteFill, Img, staticFile} from 'remotion';
import type {MixedMediaScene} from '../schema';

type FullScene = Extract<MixedMediaScene, {presentationMode: 'footage' | 'still'}>;
export const FullBleedScene = ({scene}: {scene: FullScene}) => {
  const asset = scene.primaryAsset;
  const style = {width: '100%', height: '100%', objectFit: asset.fitMode};
  return <AbsoluteFill>{asset.assetType === 'video' ? <Video src={staticFile(asset.assetPath)} muted trimBefore={asset.trimBeforeFrames} style={style} /> : <Img src={staticFile(asset.assetPath)} style={style} />}</AbsoluteFill>;
};

import {Video} from '@remotion/media';
import {AbsoluteFill, staticFile} from 'remotion';
import type {MediaAsset} from '../schema';

export const BlurredBackground = ({asset}: {asset: MediaAsset}) => {
  const style = {width: '100%', height: '100%', objectFit: 'cover' as const, filter: 'blur(22px) brightness(0.48) saturate(0.75)', transform: 'scale(1.08)'};
  return <AbsoluteFill>{asset.assetType === 'video' ? <Video src={staticFile(asset.assetPath)} muted trimBefore={asset.trimBeforeFrames} style={style} /> : <img src={staticFile(asset.assetPath)} style={style} />}<AbsoluteFill style={{backgroundColor: 'rgba(5,8,12,.2)'}} /></AbsoluteFill>;
};

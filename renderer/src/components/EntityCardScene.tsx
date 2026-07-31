import {AbsoluteFill, staticFile} from 'remotion';
import type {MixedMediaScene} from '../schema';
import {BlurredBackground} from './BlurredBackground';

type EntityScene = Extract<MixedMediaScene, {presentationMode: 'entity_card'}>;
export const EntityCardScene = ({scene}: {scene: EntityScene}) => <AbsoluteFill>
  <BlurredBackground asset={scene.backgroundAsset} />
  <div style={{position: 'absolute', isolation: 'isolate', inset: '54px 72px 132px', borderRadius: 20, background: 'rgba(247,246,242,.92)', boxShadow: '0 18px 50px rgba(0,0,0,.28)', overflow: 'hidden'}}>
    <div data-layer="entity-label" style={{position: 'absolute', zIndex: 2, left: 52, top: 48, borderLeft: '4px solid #272725', paddingLeft: 20, color: '#272725', fontFamily: 'Noto Sans SC, Microsoft YaHei, sans-serif'}}>
      <div style={{fontSize: 48, fontWeight: 700}}>{scene.entity.primaryLabel}</div>
      {scene.entity.secondaryLabel ? <div style={{fontSize: 25, marginTop: 6, color: '#696762'}}>{scene.entity.secondaryLabel}</div> : null}
    </div>
    <img data-layer="entity-display" src={staticFile(scene.displayAsset.assetPath)} style={{position: 'absolute', zIndex: 1, left: 360, right: 40, top: 30, bottom: 30, width: 'calc(100% - 400px)', height: 'calc(100% - 60px)', objectFit: 'contain'}} />
  </div>
</AbsoluteFill>;

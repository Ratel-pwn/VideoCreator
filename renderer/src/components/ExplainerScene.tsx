import {AbsoluteFill} from 'remotion';
import type {MixedMediaScene} from '../schema';
import {BlurredBackground} from './BlurredBackground';
import {FlowExplainer} from './explainers/FlowExplainer';
import {ListExplainer} from './explainers/ListExplainer';
import {QuoteHighlightExplainer} from './explainers/QuoteHighlightExplainer';
import {RelationLoopExplainer} from './explainers/RelationLoopExplainer';
type Scene = Extract<MixedMediaScene, {presentationMode: 'explainer'}>;
export const ExplainerScene = ({scene, frame}: {scene: Scene; frame: number}) => {const props = {config: scene.explainer, frame}; let content; switch (scene.explainer.kind) {case 'flow': content = <FlowExplainer {...props} />; break; case 'list': content = <ListExplainer {...props} />; break; case 'quote_highlight': content = <QuoteHighlightExplainer {...props} />; break; case 'relation_loop': content = <RelationLoopExplainer {...props} />; break;} return <AbsoluteFill><BlurredBackground asset={scene.backgroundAsset} /><div style={{position: 'absolute', inset: '48px 64px 136px', borderRadius: 24, background: 'rgba(10,13,18,.82)', overflow: 'hidden', boxShadow: '0 20px 60px rgba(0,0,0,.3)'}}>{content}</div></AbsoluteFill>};

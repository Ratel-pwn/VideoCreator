import {Audio} from '@remotion/media';
import {AbsoluteFill, Sequence, staticFile, useCurrentFrame} from 'remotion';
import type {RenderInput} from './schema';
import {EditorialFrame} from './components/EditorialFrame';
import {Scene} from './components/Scene';
import {SubtitleTrack} from './components/SubtitleTrack';

export const VideoComposition = (props: RenderInput) => {
  const frame = useCurrentFrame();
  const timeline = (
    <>
      {props.scenes.map((scene) => (
        <Sequence
          key={scene.id}
          from={scene.fromFrame}
          durationInFrames={scene.durationInFrames}
          name={scene.id}
        >
          <Scene scene={scene} />
        </Sequence>
      ))}
      <SubtitleTrack
        captions={props.captions}
        frame={frame}
        fps={props.fps}
        layout={props.frame ? 'editorial' : 'full-bleed'}
      />
    </>
  );

  return (
    <AbsoluteFill style={{backgroundColor: props.backgroundColor}}>
      {props.frame ? (
        <EditorialFrame frame={props.frame}>{timeline}</EditorialFrame>
      ) : (
        timeline
      )}
      <Audio src={staticFile(props.audioPath)} />
    </AbsoluteFill>
  );
};

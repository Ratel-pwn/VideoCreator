import {Audio} from '@remotion/media';
import {AbsoluteFill, Sequence, staticFile, useCurrentFrame} from 'remotion';
import type {RenderInput} from './schema';
import {Scene} from './components/Scene';
import {SubtitleTrack} from './components/SubtitleTrack';

export const VideoComposition = (props: RenderInput) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{backgroundColor: props.backgroundColor}}>
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
      <Audio src={staticFile(props.audioPath)} />
      <SubtitleTrack captions={props.captions} frame={frame} fps={props.fps} />
    </AbsoluteFill>
  );
};

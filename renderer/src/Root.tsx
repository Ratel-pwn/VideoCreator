import {Composition, type CalculateMetadataFunction} from 'remotion';
import {renderInputSchema, type RenderInput} from './schema';
import {assertContinuousTimeline} from './timeline';
import {VideoComposition} from './VideoComposition';

const defaultProps: RenderInput = {
  videoId: 'preview',
  width: 1920,
  height: 1080,
  fps: 25,
  durationInFrames: 25,
  audioPath: 'audio/voice.cleaned.mp3',
  subtitlePath: 'audio/voice.cleaned.srt',
  backgroundColor: '#080b0f',
  scenes: [
    {
      id: 'preview',
      fromFrame: 0,
      durationInFrames: 25,
      assetType: 'subtitle_only',
      assetPath: '',
      fitMode: 'cover',
      trimBeforeFrames: 0,
      mediaDurationInFrames: 0,
      shortVideoPolicy: 'reject',
      motionPreset: 'none',
    },
  ],
  captions: [],
};

const calculateMetadata: CalculateMetadataFunction<RenderInput> = ({props}) => {
  const parsed = renderInputSchema.parse(props);
  assertContinuousTimeline(parsed.scenes, parsed.durationInFrames);
  return {
    width: parsed.width,
    height: parsed.height,
    fps: parsed.fps,
    durationInFrames: parsed.durationInFrames,
    props: parsed,
  };
};

export const Root = () => (
  <Composition
    id="NarratedVideo"
    component={VideoComposition}
    width={1920}
    height={1080}
    fps={25}
    durationInFrames={25}
    defaultProps={defaultProps}
    calculateMetadata={calculateMetadata}
  />
);

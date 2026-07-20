import {Video} from '@remotion/media';
import {Freeze, Loop, Sequence, staticFile} from 'remotion';
import type {RenderScene} from '../schema';

const media = (scene: RenderScene) => (
  <Video
    src={staticFile(scene.assetPath)}
    muted
    trimBefore={scene.trimBeforeFrames}
    objectFit={scene.fitMode}
    style={{width: '100%', height: '100%'}}
  />
);

export const VideoScene = ({scene}: {scene: RenderScene}) => {
  if (scene.mediaDurationInFrames <= 0) {
    throw new Error(`${scene.id}: video duration is missing`);
  }
  if (scene.shortVideoPolicy === 'loop') {
    return <Loop durationInFrames={scene.mediaDurationInFrames}>{media(scene)}</Loop>;
  }
  if (scene.shortVideoPolicy === 'freeze_last_frame') {
    const playableFrames = Math.min(
      scene.durationInFrames,
      scene.mediaDurationInFrames,
    );
    return (
      <>
        <Sequence durationInFrames={playableFrames}>{media(scene)}</Sequence>
        {playableFrames < scene.durationInFrames ? (
          <Sequence from={playableFrames}>
            <Freeze frame={Math.max(0, scene.mediaDurationInFrames - 1)}>
              {media(scene)}
            </Freeze>
          </Sequence>
        ) : null}
      </>
    );
  }
  if (scene.mediaDurationInFrames < scene.durationInFrames) {
    throw new Error(`${scene.id}: video is shorter than its scene`);
  }
  return media(scene);
};

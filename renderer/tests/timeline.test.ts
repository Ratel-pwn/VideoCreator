import {describe, expect, it} from 'vitest';
import {sceneAtFrame} from '../src/timeline';

const scenes = [
  {id: 'one', fromFrame: 0, durationInFrames: 25},
  {id: 'two', fromFrame: 25, durationInFrames: 25},
];

describe('hard-cut timeline', () => {
  it('switches scenes exactly on the boundary frame', () => {
    expect(sceneAtFrame(scenes, 24)?.id).toBe('one');
    expect(sceneAtFrame(scenes, 25)?.id).toBe('two');
  });
});

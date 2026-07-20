import {z} from 'zod';

export const captionSchema = z.object({
  text: z.string(),
  startMs: z.number().nonnegative(),
  endMs: z.number().positive(),
  timestampMs: z.number().nonnegative(),
  confidence: z.number(),
});

export const renderSceneSchema = z.object({
  id: z.string().min(1),
  fromFrame: z.number().int().nonnegative(),
  durationInFrames: z.number().int().positive(),
  assetType: z.enum(['image', 'video', 'subtitle_only']),
  assetPath: z.string(),
  fitMode: z.enum(['cover', 'contain']),
  trimBeforeFrames: z.number().int().nonnegative(),
  mediaDurationInFrames: z.number().int().nonnegative(),
  shortVideoPolicy: z.enum(['loop', 'freeze_last_frame', 'reject']),
  motionPreset: z.enum(['push-left', 'push-right', 'none']),
});

export const renderInputSchema = z.object({
  videoId: z.string().min(1),
  width: z.literal(1920),
  height: z.literal(1080),
  fps: z.literal(25),
  durationInFrames: z.number().int().positive(),
  audioPath: z.string().min(1),
  subtitlePath: z.string().min(1),
  backgroundColor: z.string().min(1),
  scenes: z.array(renderSceneSchema).min(1),
  captions: z.array(captionSchema).default([]),
});

export type RenderScene = z.infer<typeof renderSceneSchema>;
export type RenderInput = z.infer<typeof renderInputSchema>;

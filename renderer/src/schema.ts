import {z} from 'zod';

export const captionSchema = z.object({
  text: z.string(),
  startMs: z.number().nonnegative(),
  endMs: z.number().positive(),
  timestampMs: z.number().nonnegative(),
  confidence: z.number(),
});

export const frameSchema = z.object({
  preset: z.literal('editorial-wide'),
  videoTitle: z.string().trim().min(1),
  publicationDate: z.string().trim().min(1),
  creatorHandle: z.string().trim().min(1),
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

export const mediaAssetSchema = z.object({
  assetType: z.enum(['image', 'video']),
  assetPath: z.string().min(1),
  fitMode: z.enum(['cover', 'contain']),
  trimBeforeFrames: z.number().int().nonnegative(),
  mediaDurationInFrames: z.number().int().nonnegative(),
  shortVideoPolicy: z.enum(['loop', 'freeze_last_frame', 'reject']),
});

const v2Base = z.object({id: z.string().min(1), fromFrame: z.number().int().nonnegative(), durationInFrames: z.number().int().positive()});
const nodeSchema = z.object({id: z.string(), label: z.string(), x: z.number().optional(), y: z.number().optional()});
const edgeSchema = z.object({from: z.string(), to: z.string(), label: z.string().optional()});
export const explainerSchema = z.discriminatedUnion('kind', [
  z.object({kind: z.literal('flow'), title: z.string().optional(), nodes: z.array(nodeSchema), edges: z.array(edgeSchema)}),
  z.object({kind: z.literal('list'), title: z.string().optional(), items: z.array(z.string()).min(1)}),
  z.object({kind: z.literal('quote_highlight'), source: z.string().optional(), lines: z.array(z.object({text: z.string(), highlight: z.boolean().optional()})).min(1)}),
  z.object({kind: z.literal('relation_loop'), title: z.string().optional(), nodes: z.array(nodeSchema), edges: z.array(edgeSchema)}),
]);
export const mixedMediaSceneSchema = z.discriminatedUnion('presentationMode', [
  v2Base.extend({presentationMode: z.literal('footage'), primaryAsset: mediaAssetSchema}),
  v2Base.extend({presentationMode: z.literal('still'), primaryAsset: mediaAssetSchema}),
  v2Base.extend({presentationMode: z.literal('entity_card'), backgroundAsset: mediaAssetSchema, displayAsset: mediaAssetSchema, entity: z.object({primaryLabel: z.string(), secondaryLabel: z.string().nullable().optional()})}),
  v2Base.extend({presentationMode: z.literal('explainer'), backgroundAsset: mediaAssetSchema, explainer: explainerSchema}),
  v2Base.extend({presentationMode: z.literal('subtitle_only')}),
]);

export const renderInputSchema = z.object({
  videoId: z.string().min(1),
  width: z.literal(1920),
  height: z.literal(1080),
  fps: z.literal(25),
  durationInFrames: z.number().int().positive(),
  audioPath: z.string().min(1),
  subtitlePath: z.string().min(1),
  backgroundColor: z.string().min(1),
  scenes: z.array(z.union([renderSceneSchema, mixedMediaSceneSchema])).min(1),
  captions: z.array(captionSchema).default([]),
  frame: frameSchema.optional(),
});

export type RenderScene = z.infer<typeof renderSceneSchema>;
export type RenderInput = z.infer<typeof renderInputSchema>;
export type EditorialFrameConfig = z.infer<typeof frameSchema>;
export type MediaAsset = z.infer<typeof mediaAssetSchema>;
export type MixedMediaScene = z.infer<typeof mixedMediaSceneSchema>;
export type AnyRenderScene = RenderScene | MixedMediaScene;

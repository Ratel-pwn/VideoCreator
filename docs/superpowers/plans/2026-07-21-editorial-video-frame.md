# Editorial Video Frame Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reusable configured editorial frame to the Remotion timeline and re-render `资本主义潘多拉魔盒` inside it.

**Architecture:** Python maps optional snake-case project presentation data into a validated camel-case render contract. Remotion renders either the existing full-bleed timeline or a static `EditorialFrame` containing the scene timeline and viewport-relative subtitles. The target project enables the frame without changing source media, narration, SRT timing, or scene boundaries.

**Tech Stack:** Python 3.12, pytest, TypeScript 5.8, React 19, Remotion 4.0.494, Zod 3, Vitest 3, FFmpeg 8.

## Global Constraints

- Output stays 1920x1080, 25fps, H.264/AAC.
- Media viewport is x=110, y=120, width=1700, height=852 with 24px corners.
- Title is `资本主义的潘多拉魔盒是如何开启的？`; title punctuation is retained.
- Publication date is `2026.07.21` and creator handle is `@通职者Ratel`.
- `DAWN RATEL`, vertical labels, and all other frame copy are absent.
- Subtitles remain one line and strip trailing punctuation.
- The frame is static; scenes retain hard cuts and existing internal motion.
- Existing projects without `presentation` remain full bleed.

---

## File Structure

- `videocreator/render_contract.py`: validates and maps project presentation data.
- `main.py`: passes project presentation configuration into render input creation.
- `renderer/src/schema.ts`: owns the optional frame schema and TypeScript type.
- `renderer/src/components/EditorialFrame.tsx`: owns card, header, viewport, footer, clipping, and metadata.
- `renderer/src/components/SubtitleTrack.tsx`: owns full-bleed versus editorial caption geometry.
- `renderer/src/VideoComposition.tsx`: places the timeline inside the optional frame.
- `projects/资本主义潘多拉魔盒/project.json`: stores reusable project presentation metadata.
- `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/render-input.json`: records the frame for the existing render run.

### Task 1: Extend The Python Render Contract

**Files:**
- Modify: `tests/test_render_contract.py`
- Modify: `videocreator/render_contract.py`
- Modify: `main.py`

**Interfaces:**
- Consumes: optional `presentation: dict[str, Any] | None` from `ctx.project_config`.
- Produces: `build_render_input(..., presentation=None)` and optional `frame` in the returned dictionary.

- [ ] **Step 1: Write failing contract tests**

Add tests that pass:

```python
presentation = {
    "frame_preset": "editorial-wide",
    "video_title": "资本主义的潘多拉魔盒是如何开启的？",
    "publication_date": "2026.07.21",
    "creator_handle": "@通职者Ratel",
}
```

Assert the result contains:

```python
{
    "preset": "editorial-wide",
    "videoTitle": "资本主义的潘多拉魔盒是如何开启的？",
    "publicationDate": "2026.07.21",
    "creatorHandle": "@通职者Ratel",
}
```

Also assert an incomplete presentation raises `ValueError` naming the missing key.

- [ ] **Step 2: Verify the tests fail**

Run: `python -m pytest tests/test_render_contract.py -v`

Expected: FAIL because `build_render_input` does not accept `presentation`.

- [ ] **Step 3: Implement presentation mapping**

Add a focused mapper:

```python
def normalize_frame_config(presentation: dict[str, Any]) -> dict[str, str]:
    fields = {
        "frame_preset": "preset",
        "video_title": "videoTitle",
        "publication_date": "publicationDate",
        "creator_handle": "creatorHandle",
    }
    missing = [key for key in fields if not str(presentation.get(key, "")).strip()]
    if missing:
        raise ValueError(f"Presentation config is missing: {', '.join(missing)}")
    return {target: str(presentation[source]).strip() for source, target in fields.items()}
```

Extend `build_render_input` with `presentation: dict[str, Any] | None = None` and add `frame` only when presentation is present. In `main.py`, pass `ctx.project_config.get("presentation")`.

- [ ] **Step 4: Verify the contract**

Run: `python -m pytest tests/test_render_contract.py -v`

Expected: all render-contract tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add tests/test_render_contract.py videocreator/render_contract.py main.py
git commit -m "feat: add editorial frame render contract"
```

### Task 2: Implement The Remotion Editorial Frame

**Files:**
- Modify: `renderer/tests/schema.test.ts`
- Modify: `renderer/tests/components.test.tsx`
- Modify: `renderer/src/schema.ts`
- Create: `renderer/src/components/EditorialFrame.tsx`
- Modify: `renderer/src/components/SubtitleTrack.tsx`
- Modify: `renderer/src/VideoComposition.tsx`
- Modify: `renderer/src/Root.tsx`

**Interfaces:**
- Consumes: optional `RenderInput.frame` with `preset`, `videoTitle`, `publicationDate`, and `creatorHandle`.
- Produces: `EditorialFrame` with a 1700x852 clipped media viewport and an editorial subtitle layout.

- [ ] **Step 1: Write failing schema and component tests**

Add a complete frame fixture and assert `renderInputSchema` accepts it. Assert incomplete frame data is rejected. Add a component test that renders:

```tsx
<EditorialFrame frame={frame}>
  <div>timeline</div>
</EditorialFrame>
```

Verify markup contains all three configured strings, `border-radius:24px`, `overflow:hidden`, and the 1700x852 viewport dimensions. Add a subtitle test for `layout="editorial"` that verifies editorial side inset and bottom position while retaining `white-space:nowrap`.

- [ ] **Step 2: Verify the tests fail**

Run: `npm test -- --run tests/schema.test.ts tests/components.test.tsx`

Expected: FAIL because `frameSchema`, `EditorialFrame`, and editorial subtitle layout do not exist.

- [ ] **Step 3: Add the schema and frame component**

Add:

```ts
export const frameSchema = z.object({
  preset: z.literal('editorial-wide'),
  videoTitle: z.string().trim().min(1),
  publicationDate: z.string().trim().min(1),
  creatorHandle: z.string().trim().min(1),
});
```

Add `frame: frameSchema.optional()` to `renderInputSchema` and export its inferred type. Implement `EditorialFrame` with a charcoal canvas, white card from y=36 to y=1044, header metadata, a positioned 1700x852 media viewport, and the footer handle. Use `Noto Sans SC, Microsoft YaHei, sans-serif` and no frame animation.

- [ ] **Step 4: Make subtitles viewport-relative**

Extend `SubtitleTrack` with `layout?: 'full-bleed' | 'editorial'`. Editorial geometry uses 80px side insets, 54px bottom offset, and 1500px maximum text width. Change `getSingleLineFontSize` to accept the active maximum width so long captions cannot overflow the smaller viewport.

- [ ] **Step 5: Compose the framed timeline**

Extract the scene sequences and subtitle track into one timeline fragment. When `props.frame` exists, render that fragment inside `EditorialFrame`; otherwise preserve current full-bleed behavior. Keep `<Audio>` outside the visual wrapper. Add a complete frame to `Root.tsx` default props only if required by TypeScript; optional schema behavior must remain intact.

- [ ] **Step 6: Verify renderer behavior**

Run:

```powershell
npm test
npm run typecheck
```

Expected: all Vitest tests PASS and TypeScript exits 0.

- [ ] **Step 7: Commit**

```powershell
git add renderer/src renderer/tests
git commit -m "feat: add configured editorial video frame"
```

### Task 3: Configure, Render, And Audit The Target Video

**Files:**
- Modify locally: `projects/资本主义潘多拉魔盒/project.json`
- Modify generated artifact: `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/render-input.json`
- Regenerate: `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/final.mp4`
- Regenerate: `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/render-report.json`
- Create: `projects/资本主义潘多拉魔盒/runs/20260720-remotion-final/review/editorial-frame-47s.jpg`

**Interfaces:**
- Consumes: the committed Python and Remotion frame contract.
- Produces: a verified framed MP4 and review still.

- [ ] **Step 1: Add target presentation configuration**

Add the exact `presentation` object from the design to `project.json`. Add the corresponding camel-case `frame` object to the existing run's `render-input.json` so the current approved timeline can be re-rendered without regenerating audio or assets.

- [ ] **Step 2: Run complete automated verification**

Run:

```powershell
python -m pytest -q
npm --prefix renderer test
npm --prefix renderer run typecheck
git diff --check
```

Expected: 22 or more Python tests PASS, all renderer tests PASS, typecheck exits 0, and diff check exits 0.

- [ ] **Step 3: Render the target video**

Run:

```powershell
python scripts/render_video.py --project-root "E:\Projects\AIGC\VideoCreator\projects\资本主义潘多拉魔盒" --input "E:\Projects\AIGC\VideoCreator\projects\资本主义潘多拉魔盒\runs\20260720-remotion-final\render-input.json" --output "E:\Projects\AIGC\VideoCreator\projects\资本主义潘多拉魔盒\runs\20260720-remotion-final\final.mp4"
```

Expected: exit 0 and `render-report.json.status` equals `verified`.

- [ ] **Step 4: Audit media and representative frame**

Run a full FFmpeg decode, use FFprobe to verify H.264/AAC 1920x1080 at 25fps for 5462 frames, and extract frame 47s. Inspect that frame for the configured title/date/handle, white space, rounded clipping, one-line subtitle, and absent trailing punctuation.

- [ ] **Step 5: Final repository check**

Run: `git status --short` and `git log -3 --oneline`.

Expected: tracked worktree is clean after implementation commits. Project-local generated artifacts remain ignored.

# AiPainter Functions Reference

This document describes key functions and workflows in AiPainter for assistant grounding.

## Core Canvas

- Non-destructive layer stack with visibility and opacity controls.
- Paint brushes with pressure size/opacity curves and stabilize control.
- Selection tools: `Rect Select`, `Lasso`, `Magic Wand`.
- Transformable floating selection for copy/cut/paste workflow.
- Local tools: `Smudge`, `Liquify`, `Text`, `Picker`, `Fill Select`.

## AI Features

- `Auto Generate`: text-to-image generation workflow.
- `Auto Sketch`: refines rough line art into cleaner line art (new layer output).
- `Auto Color`: colorizes line art (new layer output).
- `Auto Optimize`: structure/aesthetic improvement (new layer output).
- `Auto Resolution`: local super-resolution with `General` and `Illustration` styles (new layer output).

## File IO

- Project save/load: `.glp`.
- Import: image files and PSD.
- Export: flat image and PSD.

## Undo/Redo Model

- Supports global undo/redo via command stack.
- Layer pixel edits should be reversible with no loss of prior states.
- Redo is `Ctrl+Shift+Z` (also `Ctrl+Y`).

## Chat Assistant Guidance

- Prefer practical feedback with step-by-step next actions.
- For visual critique, focus on composition, value hierarchy, color harmony, edges, and structure correctness.
- Keep suggestions actionable and aligned with current visible result.

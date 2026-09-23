# Alice head-only desktop pet

The accepted full-body concept remains at `frontend/public/assets/alice-chibi-v2-concept.png`, but the active Alice desktop pet intentionally uses **only a Q-style head**. The earlier independently generated torso and limb assets are retained in `docs/asset-experiments/alice/` as unused experiments, not loaded or bundled at runtime.

The active 140 × 150 pet canvas renders `alice-v2-head-blank.png` as one uninterrupted hair-and-face silhouette. Eyes use `alice-v2-eye.png`; closed eyelids, brows, mouth shapes and blush are separate code-controlled expression layers in `frontend/src/Pet.tsx` and `frontend/src/Pet.css`. Keep their coordinates aligned to the blank face when resizing the head. Motion applies to the whole head, while eye state, mouth and blush can change independently.

This avoids mismatched limb textures and visible puppet joints. Do not bring the old full-body rig back into the pet without a new visual review.

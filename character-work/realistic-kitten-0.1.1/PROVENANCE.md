# Realistic Kitten 0.1.1 provenance

This directory contains the editable high-resolution sources for the repaired
`community.retirementpet/realistic-kitten` 0.1.1 PetPack.

## Why 0.1.1 exists

The 0.1.0 authoring script estimated a pale studio background and flood-filled
similar edge-connected pixels.  Because the kitten has pale cream fur, the
fill leaked into the left face, neck, and chest.  The broken alpha channel was
already present in the packaged PNG; Runtime geometry and clipping were not
the cause.

Version 0.1.1 preserves 0.1.0 as an immutable historical Revision and replaces
all eight sprites with newly extracted transparent sources.  The authoring
pass removes only the background, then trims a one-pixel contaminated fringe
before normalising each sprite to a 512 x 512 transparent canvas.

## Creation record

- Date: 2026-09-15
- Tool: OpenAI built-in image generation, image-edit/background-extraction mode
- Inputs: the eight original pale-background kitten scene images used for
  0.1.0
- Output role: `generated-masters/*.png` are high-resolution authoring sources;
  they are not loaded by the desktop runtime
- Processing: `scripts/process_kitten_assets_v011.py`
- Pack build: `scripts/build_kitten_pack.py`

The edit prompt required genuine transparency while preserving the kitten,
pose, expression, framing, fine fur, whiskers, and each action prop.  It also
forbade internal transparent holes, coloured fringes, residual floors and
shadows, added text, logos, and watermarks.  Each action named its required
prop explicitly: food bowl, laptop, microphone, or music-side object.

## Acceptance record

- Eight PNGs, each RGBA 512 x 512
- Transparent corners and visible foreground content
- Runtime validator: ACCEPT
- Author lint: clean
- GUI import preflight: ACCEPT
- Runtime preview: seven semantic rows rendered through the shared geometry
  path
- Regression probes cover the previously missing left face, neck, and chest in
  `work.png`

The final PetPack and its manifest live under
`assets/petpack/realistic-kitten-0.1.1/`; the distributable archive is
`assets/petpack/realistic-kitten-0.1.1.petpack`.

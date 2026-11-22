# Infrastructure models

Put supplied 3D models here, one `.glb` per component type:

    <category>/<component_type>.glb      e.g. compute/physical-server.glb

The exact path for every type is its `asset_path` in `src/lib/scene3d/catalogue.ts`, and the 3D view lists each model it is still missing. Conventions (units, orientation, `anchor_<port>` nodes) are in `docs/3D_INFRASTRUCTURE.md`.

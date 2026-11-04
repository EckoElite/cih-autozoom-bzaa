Build files for the patched Kodi (see docs/KODI_FORK_PLAN.md).
The workflow is .github/workflows/kodi-android.yml; it runs only on pushes to the spike/kodi-build branch.
Every tools/kodi-build/*.patch is applied, in name order, to the Kodi 21.2-Omega source:

| Patch | What it changes | Why |
|---|---|---|
| 0001 | `CMediaCodecVideoBuffer::RenderUpdate` renders the frame instead of releasing it unrendered when the view rectangle changes | Kodi dropped one picture frame at every zoom change (stage 2) |
| 0002 | `XBMCVideoView.setSurfaceRect` places the picture with a `SurfaceControl` transaction from the render thread instead of a layout change on the UI thread; falls back to the layout change on any failure | The UI thread merged about a third of the resize requests of a ramp (135 requested, 89 applied) and applied each late (stage 5) |

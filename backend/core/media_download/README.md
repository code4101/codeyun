# Media download

CodeYun's open media downloader accepts explicit Bilibili video URLs and stores
verified media in the standard three-stage video workflow:

- `1、video`: accepted library
- `2、video`: current review batch, capped at 20 videos
- `3、video`: downloaded reservoir

It requests the best video and audio streams normally available to the current
browser session, remuxes both tracks into the final MP4 without transcoding,
and deduplicates by BVID. It does not create a
redundant standalone audio file, discover content, or bypass account,
membership, payment, geographic, or DRM restrictions.

Source metadata is stored in CodeYun's database rather than scattered into
sidecar JSON files. `DeviceFile` keeps physical media facts such as the path,
size, duration and dimensions; `MediaSyncSourceItem` keeps the platform,
canonical source URL, external video ID, title, codecs and selected format IDs.
An optional same-prefix HTML document stays beside the MP4 as the human-readable
and directly playable analysis projection.

```powershell
uv run python -m backend.core.media_download `
  --root-dir "E:\data\media" `
  "https://www.bilibili.com/video/BV1K4411m7jx/"
```

For authenticated quality, log in to Bilibili in the Chromium instance used by
DrissionPage before running the command. Temporary cookie files are written only
under the system temporary directory and removed after extraction.

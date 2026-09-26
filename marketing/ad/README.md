# 15-second ad

A code-rendered promo for TikTok / Reels / Shorts (1080x1920) and a 16:9 cut
(1920x1080). Every frame is a function of time in `ad.html` (`render(t)`), so
editing the page and re-rendering gives the same video every time.

- `ad.html` -- the animation. Open it with `?w=1080&h=1920&t=5` to see any moment.
- `synth.py` -- the original soundtrack (numpy), synced to the beats; writes `music.wav`.
- `render.py` -- screenshots all 450 frames with Playwright and encodes an MP4 with ffmpeg.

```bash
cd marketing/ad
pip install numpy imageio-ffmpeg playwright
python synth.py
FF=$(python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())")
python render.py 1080 1920 ticker-council-ad-9x16.mp4 $FF
python render.py 1920 1080 ticker-council-ad-16x9.mp4 $FF
```

`render.py` launches Chromium from `/opt/pw-browsers/chromium`; change that path
(or drop `executable_path`) to use Playwright's own browser. The verdict shown is
a stylised example and is labelled as one on screen.

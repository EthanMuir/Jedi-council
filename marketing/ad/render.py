import os, subprocess, sys, time
from playwright.sync_api import sync_playwright
W, H, OUT = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
FPS, DUR = 30, 15.0
FF = sys.argv[4]
d = f"frames_{W}x{H}"; os.makedirs(d, exist_ok=True)
start = time.time()
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--allow-file-access-from-files"])
    pg = b.new_page(viewport={"width": W, "height": H})
    pg.goto(f"file://{os.getcwd()}/ad.html?w={W}&h={H}"); pg.wait_for_timeout(800)
    for i in range(int(FPS * DUR)):
        pg.evaluate(f"render({i / FPS})")
        pg.screenshot(path=f"{d}/f{i:04d}.png")
    b.close()
print("frames", round(time.time() - start), "s")
subprocess.run([FF, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", f"{d}/f%04d.png", "-i", "music.wav",
                "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-profile:v", "high",
                "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", OUT], check=True)
print("wrote", OUT, os.path.getsize(OUT) // 1024, "KB")

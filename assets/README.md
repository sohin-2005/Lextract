# Screenshots

The README embeds the five images below. Replace each placeholder with a real
capture — the filenames are referenced directly, so keep them exactly as-is.

| File | What to capture |
|---|---|
| `dashboard-light.png` | Dashboard in light mode: stat row populated, a few receipts listed, leaderboard chart visible |
| `dashboard-dark.png` | The same view with the theme toggled to dark |
| `bill-detail.png` | A bill detail page with the model-comparison grid showing colour-coded score badges |
| `disagreements.png` | The model-disagreement panel with at least one real disagreement |
| `splash.png` | The loading screen mid-typewriter |

## Capturing

Run the app locally so the URL bar does not show a deployment host:

```bash
# terminal 1
cd backend && source venv/bin/activate && uvicorn app.main:app --reload
# terminal 2
cd frontend && npm run dev
```

- **macOS:** `Cmd-Shift-4`, then `Space`, then click the window. This captures
  the window without the desktop behind it.
- Use a browser window around **1440px** wide. Narrower and the leaderboard
  table wraps; wider and the text looks lost.
- Zoom to 100%. A zoomed screenshot renders blurry when GitHub scales it down.

## Before committing

- [ ] No API keys visible — check the browser devtools panel is closed
- [ ] No unredacted receipt content in any thumbnail
- [ ] Each file under ~500 KB (`pngquant` or `squoosh.app` if larger)

# Screenshots

The README embeds these by filename. Replace each placeholder with your capture
and **keep the name exactly as-is** — the paths are referenced directly.

| File | What it shows |
|---|---|
| `01-dashboard.png` | Dashboard top: stat cards, upload zone, model picker |
| `02-receipts.png` | Model picker + the receipt table with run counts |
| `03-leaderboard.png` | Leaderboard chart, per-field table, recommendation |
| `04-comparison.png` | Bill detail: original receipt beside the comparison grid |
| `05-ground-truth.png` | Ground-truth form and the Zoho push panel |
| `06-disagreements.png` | Model disagreements and raw responses |
| `07-zoho-synced.png` | The "Synced to Zoho Books as expense …" confirmation |

Optional extras, referenced only if you add them:

| File | What it shows |
|---|---|
| `08-dark-mode.png` | Any view with the theme toggled dark |

## Capturing

- Browser window around **1440px** wide, zoom at **100%**. Narrower and the
  leaderboard table wraps; zoomed and GitHub renders it blurry.
- macOS: `Cmd-Shift-4` then `Space` then click the window — captures the window
  without the desktop behind it.
- Capture *after* uploading receipts and running an evaluation. An empty
  dashboard is a much weaker first impression than a populated leaderboard.

## Before committing

- [ ] No API keys visible — close devtools first
- [ ] No unredacted receipt content in any thumbnail
- [ ] Each file under ~500 KB (`squoosh.app` or `pngquant` if larger)

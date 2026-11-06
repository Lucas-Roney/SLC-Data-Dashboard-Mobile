# SLC Data Dashboard (iPhone app)

Pick a TracCloud visits CSV on your phone and the dashboard is built on the phone itself (Python runs in the browser). The file is never uploaded anywhere.

## Put it online (GitHub Pages, free)
1. On github.com click **New repository**, name it `slc-data`, set it to **Public**, create it.
2. Click **uploading an existing file**, drag in EVERYTHING from this folder (including the `pyodide` folder), and click **Commit changes**. (If the folder drag fails, use Add file > Upload files in a few batches.)
3. Go to **Settings > Pages**, set Source to **Deploy from a branch**, Branch **main**, folder **/ (root)**, Save.
4. After a minute the page shows `https://YOURNAME.github.io/slc-data/`.

## Install on iPhone
1. Open that address in **Safari** (it must be Safari). Wait for "Getting ready" to disappear (about 45 MB, first time only).
2. Tap Share, then **Add to Home Screen**.
3. Open it from the home screen, tap **Choose CSV file**, pick the export from Files. Building takes roughly 1 minute; keep the screen on.

After the first load it works with no internet. The last dashboard is remembered ("Open last dashboard").

## Updating
If `tutoring_dashboard.py` changes, replace it in the repo and bump `CACHE = 'slc-data-v1'` in `sw.js` to `v2`.

The LightGBM/neural-net forecasting app is not included; the phone dashboard uses the built-in forecasts.

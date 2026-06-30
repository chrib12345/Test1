# Deploying the SOTP Builder web app

The app is **zero-config** and lives at the repo root:

```
vercel.json            # functions config (Python serverless)
requirements.txt       # openpyxl + PyYAML (Vercel installs these)
api/generate.py        # POST config JSON  -> .xlsx download
api/market.py          # GET ?ticker=XXX   -> live price/52-wk (Yahoo)
public/index.html      # the form UI (prefilled with SPGI)
public/spgi.json       # default config the form loads
sotp_generator/        # the engine (bundled into the generate function)
```

No build step. Static files in `public/` are served at `/`; `api/*.py` become
serverless functions at `/api/generate` and `/api/market`.

## Option A — Vercel Git integration (recommended, no secrets)

1. Go to **https://vercel.com/new** and **Import** `chrib12345/Test1`.
2. Framework preset: **Other**. Root directory: **`./`** (leave default).
   Build command: *(none)*. Output dir: *(none)*. Install runs from
   `requirements.txt` automatically.
3. Click **Deploy**.
   - The app currently lives on branch
     `claude/stock-sotp-excel-generator-a7m8ac`. Vercel auto-creates a
     **preview URL** for that branch on every push — that URL works immediately.
   - For a stable **production** URL, merge the branch into `main` (or set the
     project's Production Branch to the feature branch in
     *Settings → Git*), then redeploy.
4. Open the URL, type a ticker, click **Fetch live market data**, fill the
   segment inputs, **Generate Excel**.

## Option B — Vercel CLI (one command)

From a machine with the repo checked out and a Vercel account:

```bash
npm i -g vercel
vercel            # first run links/creates the project (interactive)
vercel --prod     # production deploy -> prints the URL
```

## Notes

- **Live fetch**: `api/market.py` calls Yahoo's public chart endpoint. Vercel's
  egress reaches it; if a network blocks it, the UI says so and you enter price
  manually. Segment financials & multiples are always manual (analyst judgment).
- **Function size**: deps are small (openpyxl + PyYAML), well under Vercel limits.
- **Verify after deploy**: load the site, keep the SPGI defaults, click
  *Generate Excel* — the Base case should read **$485.45/sh**, matching the
  master template.

# CXE Dealer Portal

Flask web app for dealers to browse inventory, place orders and view invoices.

## Railway deployment

### Step 1 — Create new service in your existing Railway project
1. Go to railway.app → your CXE project
2. Click **+ New** → **GitHub Repo** → select this repo (or upload as new repo)
3. Set root directory to `/portal` if deploying from monorepo

### Step 2 — Environment variables
Add these in Railway → Variables:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Same PostgreSQL URL as Optic (copy from Optic service) |
| `PORTAL_SECRET_KEY` | Any random string e.g. `portal-cxe-secret-2026-xyz` |
| `WHEELSIZE_API_KEY` | `ec8f53e4a758566f89605631bb5a5fe3` |

### Step 3 — Custom domain
1. Railway → your portal service → Settings → Domains
2. Add custom domain: `dealers.cxeglobal.com`
3. Add CNAME record in your DNS: `dealers` → Railway provided hostname

### Step 4 — Done
- Dealers visit `dealers.cxeglobal.com/portal/login`
- Register at `dealers.cxeglobal.com/portal/register`
- You approve in Optic → Dealer Portal → Approvals

## How it connects to Optic
- Reads `wheel_inventory` table — products you manage in Optic ERP → Inventory
- Reads `dealers` table — managed via Optic ERP → Dealer Portal
- Writes `dealer_orders` — appears in Optic ERP → Dealer Portal → Portal Orders
- Reads `invoices` — invoices you create in Optic Finance → Invoices

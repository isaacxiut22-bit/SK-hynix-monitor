# SK hynix Litigation Watch — deployment package

This contains a static dashboard with a scheduled data-collection script. **This is not a published or fully connected website until deployed and configured.** Its initial records are a saved baseline, not the result of a live API call from this package.

## Publish with GitHub Pages + scheduled updates

1. Create a **public** GitHub repository and upload the **contents** of this directory into the repository's `main` branch. The `index.html` and `tracker-data.json` files must be in the repository root.
2. In **Settings → Pages**, select **Build and deployment → Source: GitHub Actions**.
3. Create a CourtListener account and API token from https://www.courtlistener.com/help/api/ . Add it to GitHub under **Settings → Secrets and variables → Actions → New repository secret**, named `COURTLISTENER_TOKEN`. Do not place the token in a public file or HTML.
4. In the **Actions** tab, run **Update litigation watch and deploy** using `workflow_dispatch`. The same workflow is scheduled at 8 a.m. and 5 p.m. Pacific, including daylight saving changes.
5. Verify both feeds' last-success and last-attempt indicators in the published dashboard and check that the Actions job completed. GitHub Pages will provide a public URL, usually `https://YOUR-USERNAME.github.io/YOUR-REPOSITORY/`.

## Actual integration and important limitations

- **Federal cases:** CourtListener v4 `search/?type=d` for `party:(hynix)`, using an API token. Results are docket-search matches labeled **“Docket match — review”** until independently verified; the search does not establish liability or even that the named party is a defendant. RECAP does **not** provide complete instant PACER coverage. Party aliases, acquisitions, and subsidiaries can be missed. A paid docket aggregator with a broader party-identity search is needed for more complete coverage.
- **Solicitations:** Google News RSS queries for announcements, securities investigations, and litigation, labeled **“Unverified solicitation lead”**. This is a discovery feed and can be incomplete, delayed, syndicated, or contain false positives. Some direct firm pages are never indexed, and some results are merely news reports. The baseline records are preserved.
- **State courts:** No comprehensive nationwide state-court filing feed is included. This requires separate provider access; do **not** describe the deployment as exhaustive for state courts.
- **Scheduling:** GitHub Actions schedules are best-effort, not exact-minute or instant updates, and can be delayed or disabled for inactivity. Source caches and publication delays add further latency.
- **Notifications:** This publishes a browsable tracker but does not send push/email notifications. Email or Teams alerts are additional integrations.
- **Review:** Do not represent automated, unverified matches as established court filings or firm solicitations without reviewing linked sources. Original baseline records remain as saved and may need manual source review.

## Run locally

`python scripts/monitor.py`

This is expected to report the federal cases source as not configured unless you set `COURTLISTENER_TOKEN` in your environment. The script will retrieve public RSS if outbound internet access is available. Run `python -m http.server 8000`, then open `http://localhost:8000/` (opening HTML via `file://` may block `fetch`).

To test the dashboard without live connectivity, the supplied `tracker-data.json` includes the initial saved baseline.

Source references:
- https://wiki.free.law/c/courtlistener/help/api/rest/v4/search
- https://www.courtlistener.com/help/search-operators/
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax

This is an independent public-source monitoring tool and does not constitute legal advice or a comprehensive docket search.
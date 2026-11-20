# SK hynix Litigation Watch — zero-fee public-source monitoring

Public dashboard: https://isaacxiut22-bit.github.io/SK-hynix-monitor/

The dashboard is an **early-warning lead feed**, NOT comprehensive state or federal litigation surveillance. It preserves the original baseline, displays source health, and never promotes scraped/news findings to confirmed filings. Public metadata is published to GitHub Pages; never commit private legal information or secrets to this repository.

## Automated monitoring (currently configured)

The GitHub Actions workflow (`.github/workflows/monitor-and-deploy.yml`) is scheduled for 8:17 a.m. and 5:17 p.m. America/Los_Angeles, on `main` pushes, and when manually dispatched. GitHub cron execution is best-effort and can be delayed. It refreshes `tracker-data.json`, persists the file, and publishes Pages.

| Data source | Purpose | Requirements / caveats |
| --- | --- | --- |
| CourtListener v4 Search `type=d`, 3 party-name searches, up to 2 pages/query | Federal docket match candidates | **Needs `COURTLISTENER_TOKEN`**, otherwise inactive. Docket match does not establish defendant role. Coverage incomplete. |
| Google News RSS — filing-report queries | Possible newly reported federal/state complaints or lawsuits | **Not a docket search**; results can be old, inaccurate, duplicated, or unrelated to a new filing. |
| Google News RSS — plaintiff solicitation queries | Investor notices, investigations, potential class actions | Newswire syndications and false positives possible; verify with firm. |
| Google Alerts Atom RSS — Web-source alerts | Broader search-index results, including law-firm investigation webpages | **Optional, awaits private `GOOGLE_ALERTS_RSS_URLS` secret.** Google account setup is manual; results may be delayed, limited or missing. RSS is fetched on the existing GitHub schedule. |
| Direct public plaintiff-firm pages | Kessler Topaz, Rosen Law, and Hagens Berman case/investigation listings | Only public visible HTML links, once per run, where robots.txt allows; JavaScript lists or disallowed fetches may yield no results. |

**Solicitation deduplication:** Known plaintiff-firm campaigns are consolidated conservatively by identified firm, claim subject, and a 45-day announcement window. The source links, original publication titles, and earliest announcement date are preserved under one entry. A campaign remains an unverified lead unless independently reviewed. Unknown firms, distinct claim subjects and sufficiently separated campaigns stay separate. Deduplication runs on *every* refresh to prevent repeat RSS items from recreating duplicate entries.

`runs.cases`, `runs.case_news`, `runs.solicitations`, `runs.google_alerts`, and `runs.law_firms` track independent source outcomes (success, partial, failed). Prior successful timestamps are retained if a later run fails. Empty results on a source **do not** prove no new cases/solicitations.


## Set up free Google Alerts Web monitoring

Google Alerts requires you to create alerts while signed in to your own Google account; the monitor cannot create them automatically. RSS feeds are Atom XML with publisher URLs embedded in some Google redirect links. The collector handles both, filters results for SK hynix plus litigation/solicitation wording, and groups related campaigns. **It cannot guarantee discovery of every web page or provide real-time notifications.**

1. Sign in at https://www.google.com/alerts . Create several separate alerts using queries such as:
   - \`"SK hynix" investigation\`
   - \`"SK hynix" antitrust\`
   - \`"SK hynix" "class action"\`
   - \`"SK hynix" "law firm"\`
   - \`"Solidigm" investigation\`
   - Optional: \`site:lieffcabraser.com "SK hynix"\`
2. For each query, choose **Show options → Sources: Web → Language: English → Region: United States → How many: All results → Deliver to: RSS feed**, then **Create Alert**. Once you choose RSS, Google may set the frequency to **As-it-happens**; GitHub still polls at its own scheduled times.
3. From **My alerts**, right-click the RSS icon beside each alert and **Copy link address**. Each link should look like \`https://www.google.com/alerts/feeds/123456789/987654321\` (actual numbers differ).
4. Open this repository's **Settings → Secrets and variables → Actions → New repository secret**. Name it **\`GOOGLE_ALERTS_RSS_URLS\`**. Paste all your feed URLs, **one URL per line**, into the secret value. Keep the URLs private: anyone possessing one can read that feed.
5. Open **Actions → Update litigation watch and deploy → Run workflow**. Confirm **Google Alerts (Web): success** or **partial** in the public dashboard. When no URLs are configured, the dashboard reports **not_configured**, not a successful scan. The secret's full URLs are never committed to the repository or published in JSON.

Google Alerts can return only a limited set of indexed links and may omit older or low-ranked results; creating an alert today is **not** a backfill of every historical solicitation. Results in this tracker remain unverified until the underlying firm page is reviewed.

## Free state-court portals (manual only)

These portals can be accessed by site visitors using the links on the dashboard:

- New York NYSCEF — https://www.nycourts.gov/efile/ (guest public search; covered courts/case types vary)
- Delaware CourtConnect — https://www.courts.delaware.gov/docket.aspx (Superior, Common Pleas and JP civil dockets)
- San Francisco Superior Court — https://sf.courts.ca.gov/online-services/case-information
- Santa Clara Superior Court — https://portal.scscourt.org/search

**No code automatically queries, scrapes, signs in to, or bypasses limitations on these portals.** Free online availability does not imply a stable, legal, machine-accessible data API. Los Angeles civil party-name searches incur fees. There is no free comprehensive nationwide state-court new-case feed integrated here.

## Activate CourtListener (optional, free within applicable usage limits)

1. Create a CourtListener account and get your token at https://www.courtlistener.com/profile/api-token/ .
2. In this repository go to Settings → Secrets and variables → Actions → New repository secret.
3. Name it **`COURTLISTENER_TOKEN`** and paste the secret value. Never paste or commit the token to the dashboard or GitHub code.
4. In Actions, run **Update litigation watch and deploy** manually, then inspect the workflow log and dashboard federal docket status. CourtListener search results still require human docket verification.

More thorough federal searches require PACER or professional docket aggregation; this project does not connect to paid sources.

## Development and verification

Run `python -m unittest discover -s tests -v`, then `python scripts/monitor.py`. The monitor uses only the Python standard library. The script will report CourtListener as unavailable without its token. The dashboard is served as static HTML/JSON (use `python -m http.server 8000` locally).

**No guarantees:** Public-source availability changes. The script is intentionally conservative: source failures and limitations are visible; leads are not filed cases unless verified against the court docket. Listings may refer to subsidiaries, former names, or unrelated parties with similar names. Do not use this as the sole source for consequential litigation decisions.

Official references:
- CourtListener Search API: https://wiki.free.law/c/courtlistener/help/api/rest/v4/search
- NYSCEF guest search: https://www.nycourts.gov/efile/
- Delaware CourtConnect: https://www.courts.delaware.gov/docket.aspx
- SF Superior Court: https://sf.courts.ca.gov/online-services/case-information
- Santa Clara: https://portal.scscourt.org/search
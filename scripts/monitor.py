#!/usr/bin/env python3
"""Zero-fee, best-effort SK hynix litigation discovery.

Sources: CourtListener federal docket API (requires private token), Google News RSS
for litigation/filing reports and plaintiff solicitations, and public plaintiff-firm
case listings. State-court portal access is provided as manual search links in
index.html; this script does NOT scrape state courts or claim state-docket coverage.
No item from a discovery feed is promoted to a verified lawsuit/solicitation.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
from html.parser import HTMLParser
import html
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / 'tracker-data.json'
NOW = dt.datetime.now(dt.timezone.utc)
STAMP = NOW.isoformat(timespec='seconds').replace('+00:00', 'Z')
DATE = NOW.date().isoformat()
AGENT = 'SKHynixLitigationWatch/2.0 (noncommercial public monitoring; GitHub repository)'

# Multiple modest queries outperform a single large OR expression, per CL guidance.
COURT_QUERIES = ['party:(hynix)', 'party:("Hynix Semiconductor")', 'party:(solidigm)']
MAX_COURTLISTENER_PAGES = 2  # per query; metadata only; existing IDs are deduplicated

# Google News is a discovery feed, not a court's official case docket.
RSS_QUERIES = {
    'case_news': [
        '"SK hynix" "filed" "lawsuit"',
        '"SK hynix" "sued" "court"',
        '"SK hynix America" "complaint"',
        '"SK hynix" "state court" lawsuit',
        '"Hynix" "new lawsuit"',
    ],
    'solicitations': [
        '"SK hynix" securities class action investigation law firm',
        '"SK hynix" plaintiff firm investors contact',
        '"SK hynix" antitrust price fixing lawsuit law firm',
        '"SK Hynix" shareholder rights investigation',
        '"SKHY" investigation shareholder law firm',
        '"SK hynix" mass arbitration plaintiffs counsel',
        'site:ktmc.com "SK hynix"',
        'site:rosenlegal.com "SK hynix"',
        'site:hbsslaw.com "SK hynix"',
    ],
}

# Public pages only. Each is fetched once, no recursive crawling or login, and
# only where the site's robots.txt permits this user agent. HTML/JS limitations
# are reported; direct listings are NOT a complete inventory of a firm's work.
FIRM_PAGES = [
    ('Kessler Topaz (investigations)', 'https://www.ktmc.com/investigations/'),
    ('Kessler Topaz (new cases)', 'https://www.ktmc.com/newcases/'),
    ('Rosen Law Firm (active cases)', 'https://rosenlegal.com/cases/'),
    ('Hagens Berman (cases)', 'https://www.hbsslaw.com/cases'),
]

ALIASES = re.compile(r'\b(?:sk\s*[- ]?\s*hynix|hynix\s+semiconductor|solidigm|\bSKHY\b)\b', re.I)
LEGAL = re.compile(r'\b(?:lawsuit|litigat\w*|legal action|investigat\w*|securit\w* fraud|class[ -]?action|plaintiff|antitrust|price[ -]?fix\w*|shareholder|law firm|lead plaintiff|investor alert|investors? (?:encouraged|urged))\b', re.I)
SOLICIT = re.compile(r'\b(?:investigat\w*|securit\w* fraud|class[ -]?action|law firm|investors? (?:encouraged|urged)|lead plaintiff|seeking investors|investor alert|shareholder rights|join (?:the |a )?(?:class action|lawsuit))\b', re.I)
FILED_CASE = re.compile(r'\b(?:filed (?:a|an|the|new|proposed|class action)?\s*(?:lawsuit|complaint|case|action|suit)|lawsuit (?:was |has been )?filed|new(?:ly)? filed (?:lawsuit|complaint)|files (?:a|an|new|class action)?\s*(?:lawsuit|complaint|suit)|(?:has been |was )?sued|suing|new lawsuit|class[ -]?action lawsuit (?:against|targets)|complaint (?:was |has been )?filed)\b', re.I)


def get(url: str, *, token: str = '', timeout: int = 20) -> bytes:
    headers = {'User-Agent': AGENT, 'Accept': 'application/json, application/rss+xml, application/xml, text/html'}
    if token:
        headers['Authorization'] = 'Token ' + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f'HTTP {response.status}')
        return response.read(4_000_000)


def as_text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', str(s or '')))).strip()


def short(s, limit=500):
    s = as_text(s)
    return s[:limit].rstrip() + ('…' if len(s) > limit else '')


def case_category(s):
    s = (s or '').lower()
    if any(w in s for w in ('antitrust', 'price fix', 'monopol', 'competition', 'collusion')):
        return 'Antitrust'
    if any(w in s for w in ('securit', 'shareholder', 'exchange act', 'investors')):
        return 'Securities'
    if any(w in s for w in ('patent', 'copyright', 'trademark', 'intellectual property')):
        return 'IP'
    if any(w in s for w in ('employment', 'labor', 'discrimination', 'wage')):
        return 'Employment'
    return 'Other Litigation'


def parse_date(raw):
    try:
        date = str(raw)[:10]
        dt.date.fromisoformat(date)
        return date
    except (TypeError, ValueError):
        return None


def stable_id(prefix, *pieces):
    return prefix + hashlib.sha256('|'.join(str(p).strip().lower() for p in pieces).encode()).hexdigest()[:16]


def courtlistener(token):
    if not token:
        raise RuntimeError('COURTLISTENER_TOKEN not configured (federal docket search inactive)')
    out = {}
    successes, errors = 0, []
    for query in COURT_QUERIES:
        url = 'https://www.courtlistener.com/api/rest/v4/search/?' + urllib.parse.urlencode(
            {'q': query, 'type': 'd', 'order_by': 'dateFiled desc'})
        for page in range(MAX_COURTLISTENER_PAGES):
            try:
                payload = json.loads(get(url, token=token))
                rows = payload.get('results')
                if not isinstance(rows, list):
                    raise ValueError('CourtListener response missing results')
                successes += 1
                for r in rows:
                    docket_id = r.get('docket_id') or r.get('docketId') or r.get('id')
                    if not docket_id:
                        continue
                    caption = as_text(r.get('caseName') or r.get('case_name') or r.get('caseNameFull') or '')
                    docket_no = as_text(r.get('docketNumber') or r.get('docket_number') or '')
                    court = as_text(r.get('court') or r.get('court_id') or 'Federal court')
                    case_url = r.get('absolute_url') or r.get('absoluteUrl') or ''
                    case_url = urllib.parse.urljoin('https://www.courtlistener.com/', case_url)
                    if urllib.parse.urlsplit(case_url).hostname not in ('www.courtlistener.com', 'courtlistener.com'):
                        case_url = 'https://www.courtlistener.com/?type=d&q=' + urllib.parse.quote('docket_id:' + str(docket_id))
                    out['cl-' + str(docket_id)] = {
                        'id': 'cl-' + str(docket_id), 'tracker': 'cases',
                        'title': caption or f'Federal docket {docket_no or docket_id}',
                        'entity': 'SK hynix or subsidiary (party-query result; verify exact party and role)',
                        'date': parse_date(r.get('dateFiled') or r.get('date_filed')),
                        'firstSeen': DATE, 'category': case_category(caption + ' ' + str(r.get('suitNature', ''))),
                        'status': 'Docket match — review',
                        'forum': court + ((' · ' + docket_no) if docket_no else ''),
                        'firm': 'Not established',
                        'summary': 'Federal docket returned by a CourtListener party-name search. Review the court record to confirm entity identity, defendant status, and whether this is a new case.',
                        'scope': 'U.S. federal CourtListener/RECAP docket coverage; incomplete and potentially delayed versus PACER.',
                        'sources': [{'label': 'CourtListener federal docket', 'url': case_url}], 'baseline': False,
                    }
                next_url = payload.get('next')
                if not next_url:
                    break
                parsed = urllib.parse.urlsplit(next_url)
                if parsed.scheme != 'https' or parsed.hostname not in ('www.courtlistener.com', 'courtlistener.com'):
                    raise ValueError('CourtListener pagination refused off-domain link')
                url = next_url
            except (ValueError, RuntimeError, urllib.error.URLError, TimeoutError) as exc:
                errors.append(short(f'{query} page {page + 1}: {exc}', 150))
                break
    if not successes:
        raise RuntimeError('All CourtListener searches failed: ' + '; '.join(errors))
    return list(out.values()), errors


def rss_articles(xml_bytes):
    root = ET.fromstring(xml_bytes)
    for item in root.findall('.//item'):
        def tag(name):
            t = item.find(name)
            return (t.text or '') if t is not None else ''
        yield dict(title=as_text(tag('title')), desc=as_text(tag('description')),
                   link=tag('link').strip(), published=tag('pubDate'),
                   publisher=as_text(tag('source')))


def publication_date(article):
    raw = article.get('published', '')
    try:
        return email.utils.parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        try:
            return dt.datetime.fromisoformat(str(raw).replace('Z', '+00:00')).date().isoformat()
        except (TypeError, ValueError):
            return None


def solicitation_record(article):
    title, desc, link = article['title'], article['desc'], article['link']
    combined = title + ' ' + desc
    if not (ALIASES.search(combined) and LEGAL.search(combined) and SOLICIT.search(combined)):
        return None
    if not link.startswith('https://'):
        return None
    when = publication_date(article)
    clean_title = re.sub(r'\s+-\s+[^-]{2,80}$', '', title) or title
    publisher = article.get('publisher') or 'Public news/press-release feed'
    category = case_category(combined)
    return {'id': stable_id('news-', clean_title, when), 'tracker': 'solicitations',
            'title': short(clean_title, 200), 'entity': 'SK hynix or subsidiary (unverified reference)',
            'date': when, 'firstSeen': DATE, 'category': category,
            'status': 'Unverified solicitation lead', 'forum': 'Public announcement or media report',
            'firm': publisher,
            'summary': short(desc or title, 450) + ' Check the original publication for a direct client solicitation; a news article does not prove a filing.',
            'scope': 'Google News RSS discovery; not a court docket or verified firm outreach.',
            'sources': [{'label': 'News/press-release source via Google News', 'url': link}], 'baseline': False}


def case_news_record(article):
    title, desc, link = article['title'], article['desc'], article['link']
    combined = title + ' ' + desc
    if not (ALIASES.search(combined) and FILED_CASE.search(combined) and link.startswith('https://')):
        return None
    when = publication_date(article)
    # A news report is deliberately NOT a confirmed filing, even if the headline says "filed".
    low = combined.lower()
    forum = 'Reported state-court proceeding — unverified' if 'state court' in low or 'superior court' in low else 'Court and filing jurisdiction unverified'
    return {'id': stable_id('filingnews-', title, when), 'tracker': 'cases',
            'title': short(title, 200), 'entity': 'SK hynix or subsidiary (media reference; verify role)',
            'date': when, 'firstSeen': DATE, 'category': case_category(combined),
            'status': 'Unverified filing report', 'forum': forum,
            'firm': article.get('publisher') or 'Public news source',
            'summary': short(desc or title, 450) + ' This is a media-source lead; verify court, docket number, party role and original filing.',
            'scope': 'May concern federal or state court; NOT identified from a court docket.',
            'sources': [{'label': 'Media filing report via Google News', 'url': link}], 'baseline': False}


def rss_feed(kind):
    results, errors, successes = {}, [], 0
    for query in RSS_QUERIES[kind]:
        url = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})
        try:
            for article in rss_articles(get(url)):
                found = case_news_record(article) if kind == 'case_news' else solicitation_record(article)
                if found:
                    results[found['id']] = found
            successes += 1
        except (urllib.error.URLError, ValueError, ET.ParseError, TimeoutError) as exc:
            errors.append(short(f'{query}: {exc}', 180))
    if not successes:
        raise RuntimeError('All ' + kind + ' RSS queries failed: ' + '; '.join(errors))
    return list(results.values()), errors



# Google Alerts RSS delivery uses the Atom format. Unlike Google News RSS search,
# these URLs are created manually in a Google account and treated as bearer links.
# Never include them in public JSON, commits, source labels or error messages.
ATOM_NS = '{http://www.w3.org/2005/Atom}'


def google_alerts_urls(raw):
    """Validate newline-separated, private Google Alerts Atom feed URLs."""
    urls = []
    for raw_url in str(raw or '').splitlines():
        url = raw_url.strip()
        if not url:
            continue
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https'
                or parsed.hostname not in ('www.google.com', 'google.com')
                or not re.fullmatch(r'/alerts/feeds/[0-9]+/[0-9]+/?', parsed.path)
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError('Invalid Google Alerts RSS URL (expected an https://www.google.com/alerts/feeds/... link)')
        if url not in urls:
            urls.append(url)
    if len(urls) > 40:
        raise ValueError('Too many Google Alerts feeds; maximum is 40')
    return urls


def google_alerts_articles(xml_bytes):
    """Extract plain-text Atom entries and underlying publisher URLs."""
    root = ET.fromstring(xml_bytes)
    if root.tag != ATOM_NS + 'feed':
        raise ValueError('Expected Google Alerts Atom feed')
    for entry in root.findall(ATOM_NS + 'entry'):
        title = as_text(html.unescape(entry.findtext(ATOM_NS + 'title') or ''))
        desc = as_text(html.unescape(entry.findtext(ATOM_NS + 'content')
                                    or entry.findtext(ATOM_NS + 'summary') or ''))
        raw_link = ''
        for node in entry.findall(ATOM_NS + 'link'):
            if node.get('rel', 'alternate') == 'alternate' and node.get('href'):
                raw_link = node.get('href')
                break
        parsed = urllib.parse.urlsplit(raw_link)
        if parsed.hostname in ('www.google.com', 'google.com') and parsed.path == '/url':
            # Google Alerts commonly wraps publisher links in ?url=https://...
            raw_link = urllib.parse.parse_qs(parsed.query).get('url', [''])[0]
        if not raw_link.startswith('https://'):
            continue
        yield {'title': title, 'desc': desc, 'link': raw_link,
               'published': entry.findtext(ATOM_NS + 'published')
                            or entry.findtext(ATOM_NS + 'updated') or '',
               'publisher': 'Google Alerts web result'}


def google_alerts_feed():
    """Check saved Alert feeds; partial results are explicitly reported."""
    urls = google_alerts_urls(os.environ.get('GOOGLE_ALERTS_RSS_URLS', ''))
    if not urls:
        raise RuntimeError('Google Alerts feeds not configured')
    results, errors, successes = {}, [], 0
    for index, url in enumerate(urls, 1):
        try:
            for article in google_alerts_articles(get(url)):
                record = solicitation_record(article)
                if record is None:
                    continue
                # Use the destination URL so overlaps across separate alerts
                # don't create multiple records for the same web page.
                record['id'] = stable_id('webalert-', article['link'])
                record['forum'] = 'Web search result — prospective U.S. litigation (unverified)'
                record['scope'] = ('Google Alerts Web RSS discovery. A web result '
                                   'does not establish a direct solicitation or filed lawsuit.')
                record['sources'] = [{'label': 'Google Alerts web result',
                                      'url': article['link']}]
                results[record['id']] = record
            successes += 1
        except (urllib.error.URLError, ValueError, ET.ParseError, TimeoutError) as exc:
            # Avoid printing the source URL, which embeds identifiers for
            # the user's private Google Alert.
            detail = 'HTTP ' + str(exc.code) if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            errors.append(f'Google Alert feed #{index}: {detail}')
    if not successes:
        raise RuntimeError('All Google Alerts feeds failed: ' + '; '.join(errors))
    return list(results.values()), errors


class ListingParser(HTMLParser):
    """Extract text-bearing anchors and HTML table rows, without crawling links."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors, self.rows = [], []
        self._link = None
        self._row = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr':
            self._row = {'parts': [], 'urls': []}
        if tag == 'a':
            self._link = {'url': attrs.get('href', ''), 'parts': []}
            if self._row is not None:
                self._row['urls'].append(attrs.get('href', ''))

    def handle_data(self, data):
        if self._link is not None:
            self._link['parts'].append(data)
        if self._row is not None:
            self._row['parts'].append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self._link is not None:
            self.anchors.append((as_text(' '.join(self._link['parts'])), self._link['url']))
            self._link = None
        if tag == 'tr' and self._row is not None:
            self.rows.append((as_text(' '.join(self._row['parts'])), self._row['urls']))
            self._row = None


def direct_firm_records(html_bytes, firm, base_url):
    parser = ListingParser()
    parser.feed(html_bytes.decode('utf-8', errors='replace'))
    candidates = []
    for label, href in parser.anchors:
        if ALIASES.search(label + ' ' + href):
            candidates.append((label, href))
    for label, hrefs in parser.rows:
        if ALIASES.search(label):
            # A row often contains a company name in one cell and "Learn More" in a separate link.
            for href in hrefs[:3]:
                candidates.append((label, href))
    results = {}
    base_host = urllib.parse.urlsplit(base_url).hostname
    for label, href in candidates:
        url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != base_host or url == base_url:
            continue
        label = short(label or 'SK hynix firm case listing', 180)
        rec_id = stable_id('firm-', url)
        results[rec_id] = {
            'id': rec_id, 'tracker': 'solicitations', 'title': label,
            'entity': 'SK hynix / related name (firm listing match; review)',
            'date': None, 'firstSeen': DATE, 'category': case_category(label),
            'status': 'Unverified firm website lead', 'forum': 'Plaintiff-firm public website',
            'firm': firm,
            'summary': 'Company or ticker match found directly on a public plaintiff-firm case/investigation listing. Review the linked page to determine the client solicitation, litigation posture, and case status.',
            'scope': 'Direct listing-page discovery; firm websites may use JavaScript and not expose all entries in HTML.',
            'sources': [{'label': 'Plaintiff-firm website', 'url': url}], 'baseline': False,
        }
    return list(results.values())


def robots_allowed(url):
    """Fail closed when robots rules cannot be retrieved; no CAPTCHA or access bypass."""
    origin = urllib.parse.urlsplit(url)
    robots_url = f'{origin.scheme}://{origin.netloc}/robots.txt'
    try:
        text = get(robots_url, timeout=12).decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # robots.txt absent: no robots restrictions declared
            return True
        raise RuntimeError(f'robots.txt returned HTTP {exc.code}') from exc
    except (OSError, ValueError, TimeoutError) as exc:
        raise RuntimeError(f'robots.txt unavailable: {exc}') from exc
    rules = urllib.robotparser.RobotFileParser()
    rules.parse(text.splitlines())
    return rules.can_fetch(AGENT, url)


def firm_websites():
    results, errors, successes = {}, [], 0
    for firm, url in FIRM_PAGES:
        try:
            if not robots_allowed(url):
                raise RuntimeError('robots.txt disallows automatic fetch')
            matches = direct_firm_records(get(url), firm, url)
            successes += 1
            for record in matches:
                results[record['id']] = record
        except (ValueError, RuntimeError, urllib.error.URLError, TimeoutError) as exc:
            errors.append(f'{firm}: {short(exc, 140)}')
    if not successes:
        raise RuntimeError('No public plaintiff-firm listing could be retrieved: ' + '; '.join(errors))
    return list(results.values()), errors



# Known plaintiff-firm names are recognized in the headline, identified counsel,
# or direct listing. A wire-service publisher is *not* treated as the firm.
FIRM_IDENTITIES = (
    ('Lieff Cabraser', r'lieff\s+cabraser|\blieffcabraser\.com\b'),
    ('Kessler Topaz Meltzer & Check', r'kessler\s+topaz|\bktmc\b'),
    ('Rosen Law Firm', r'\brosen\s+law\s+firm\b|\brosenlegal\b'),
    ('Levi & Korsinsky', r'levi\s*(?:&|and)\s*korsinsky|\bzlk\.com\b'),
    ('Hagens Berman', r'hagens\s+berman|\bhbsslaw\b'),
    ('Kahn Swick & Foti', r'kahn\s+swick\s*(?:&|and)\s*foti'),
)
CAMPAIGN_WINDOW_DAYS = 45


def solicitation_firm(record):
    """Return a known firm only when its name appears in the record's own text."""
    body = ' '.join(str(record.get(k) or '') for k in ('title', 'firm', 'summary'))
    # Direct firm pages can identify counsel even when their anchor is generic.
    body += ' ' + ' '.join(str(src.get('url') or '') for src in record.get('sources', []))
    for name, pattern in FIRM_IDENTITIES:
        if re.search(pattern, body, re.I):
            return name
    return None


def solicitation_topic(record):
    """Conservative campaign family. Ambiguous subjects are *not* merged."""
    body = ' '.join(str(record.get(k) or '') for k in ('title', 'summary', 'forum'))
    body += ' ' + ' '.join(str(src.get('url') or '') for src in record.get('sources', []))
    body = body.lower()
    if 'solidigm' in body and re.search(r'asset sale|proposed sale|transaction|acquisition|merger', body):
        return 'solidigm-transaction'
    # Investor firms often cite the underlying DRAM antitrust suit as the basis
    # for a *separate securities investigation*. Classify the actual counsel
    # solicitation, not merely the background lawsuit it mentions.
    if record.get('category') == 'Securities' and re.search(r'securit|shareholder|investors?|\bskhy\b', body):
        return 'securities-investigation'
    if re.search(r'price[ -]?fix|antitrust|anti-competitive|collusion', body):
        if re.search(r'nand|flash memory', body):
            return 'nand-antitrust'
        if re.search(r'dram|dynamic random.access memory', body):
            return 'dram-antitrust'
        return 'memory-antitrust'
    if re.search(r'data breach|privacy breach|personal information leak', body):
        return 'data-breach'
    if re.search(r'patent infringement|copyright infringement|trade secret', body):
        return 'intellectual-property'
    if re.search(r'employment discrimination|unpaid wages|wage theft', body):
        return 'employment'
    if re.search(r'\bfiled (?:a |an )?(?:securities )?class[ -]?action lawsuit\b', body):
        return 'filed-securities-class-action'
    if re.search(r'securit|shareholder|investors?|\bskhy\b|shareholder rights', body):
        return 'securities-investigation'
    return None


def campaign_key(record):
    """A match requires a recognized *soliciting firm*, company and specific claim type."""
    if record.get('tracker') != 'solicitations':
        return None
    firm, topic = solicitation_firm(record), solicitation_topic(record)
    if not firm or not topic:
        return None
    # SKHY by itself is used only as a company ticker in this feed; every feed
    # match must already concern SK hynix. Separate Solidigm transaction topics.
    return (firm, topic)


def representative_priority(record):
    """Prefer manually reviewed / original firm material over syndicated items."""
    return (int(record.get('status') == 'Reviewed solicitation'),
            int(bool(record.get('baseline'))),
            int(record.get('status') not in ('Unverified solicitation lead',
                                            'Unverified firm website lead')),
            int(str(record.get('id', '')).startswith('firm-')))


def combined_sources(records):
    """Keep original release, syndications and firm listing, once per URL."""
    seen, combined = set(), []
    for record in records:
        for source in record.get('sources', []):
            url = str(source.get('url') or '').strip()
            if not url or url in seen:
                continue
            seen.add(url)
            combined.append({'label': str(source.get('label') or 'Source'), 'url': url})
    return combined


def merge_campaign(records, firm, topic):
    """Combine one firm's closely related notices into one stable campaign card."""
    ordered = sorted(records, key=lambda r: (representative_priority(r),
                                              str(r.get('id') or '')), reverse=True)
    primary = dict(ordered[0])  # preserve baseline ID and its reviewed status
    primary['sources'] = combined_sources(ordered)
    dates = [r.get('date') for r in records if parse_date(r.get('date'))]
    first_seen = [r.get('firstSeen') for r in records if parse_date(r.get('firstSeen'))]
    if dates:
        primary['date'] = min(dates)  # first announcement, not latest syndication
    if first_seen:
        primary['firstSeen'] = min(first_seen)
    primary['firm'] = firm
    primary['campaignTopic'] = topic
    primary['mergedRecordIds'] = sorted(set(
        str(id_) for r in records for id_ in [r.get('id'), *(r.get('mergedRecordIds') or [])] if id_))
    primary['relatedHeadlines'] = sorted(set(t for r in records
                                             for t in ([*(r.get('relatedHeadlines') or [])] if r.get('campaignTopic') else [str(r.get('title') or '')])
                                             if t and t != firm))
    if topic == 'securities-investigation':
        primary['title'] = firm + ' — SK hynix securities investigation'
        primary['category'] = 'Securities'
    elif topic == 'solidigm-transaction':
        primary['title'] = firm + ' — Solidigm transaction investigation'
        primary['category'] = 'Securities'
    elif topic in ('dram-antitrust', 'nand-antitrust', 'memory-antitrust'):
        subject = {'dram-antitrust': 'DRAM',
                   'nand-antitrust': 'NAND memory',
                   'memory-antitrust': 'memory-chip'}[topic]
        primary['title'] = firm + ' — SK hynix ' + subject + ' antitrust matter'
        primary['category'] = 'Antitrust'
    elif len(records) > 1:
        primary['title'] = firm + ' — SK hynix ' + topic.replace('-', ' ')
    if len(records) > 1 or len(primary['sources']) > 1:
        suffix = ('Related notices and firm links consolidated as one potential campaign; '
                  'verify that each source concerns the same matter.')
        current_scope = str(ordered[0].get('scope') or '')
        if suffix not in current_scope:  # idempotent across scheduled refreshes
            primary['scope'] = (current_scope + ' ' + suffix).strip()
    return primary


def consolidate_solicitations(records):
    """Deduplicate known-firm campaigns without collapsing unrelated litigation.

    Uses firm + subject, plus a 45-day window for *dated* announcements. A
    date-less direct firm listing joins only when there is a single matching
    dated campaign; if multiple separate periods exist, it stays independent.
    Unrecognized firms and vague subjects remain separate for human review.
    """
    groups, unchanged = {}, []
    for record in records:
        key = campaign_key(record)
        if key:
            groups.setdefault(key, []).append(record)
        else:
            unchanged.append(record)

    for (firm, topic), members in groups.items():
        dated, undated = [], []
        for r in members:
            (dated if parse_date(r.get('date')) else undated).append(r)
        dated.sort(key=lambda r: (r['date'], r['id']))
        clusters = []
        for r in dated:
            if clusters:
                start = dt.date.fromisoformat(clusters[-1][0]['date'])
                current = dt.date.fromisoformat(r['date'])
                if (current - start).days <= CAMPAIGN_WINDOW_DAYS:
                    clusters[-1].append(r)
                    continue
            clusters.append([r])
        if len(clusters) == 1:
            clusters[0].extend(undated)
        elif not clusters and undated:
            clusters = [undated]
        else:
            # Do not arbitrarily link a date-less listing to one of many campaigns.
            clusters.extend([[r] for r in undated])
        for cluster in clusters:
            if len(cluster) > 1 or any(r.get('mergedRecordIds') for r in cluster):
                unchanged.append(merge_campaign(cluster, firm, topic))
            else:
                unchanged.append(cluster[0])
    return unchanged


def main():
    payload = json.loads(DATA_FILE.read_text(encoding='utf-8'))
    existing = {r['id']: r for r in payload.get('records', [])}
    runs = payload.setdefault('runs', {})
    tasks = [
        ('cases', lambda: courtlistener(os.environ.get('COURTLISTENER_TOKEN', ''))),
        ('case_news', lambda: rss_feed('case_news')),
        ('solicitations', lambda: rss_feed('solicitations')),
        ('google_alerts', google_alerts_feed),
        ('law_firms', firm_websites),
    ]
    for kind, fetcher in tasks:
        if kind == 'google_alerts' and not os.environ.get('GOOGLE_ALERTS_RSS_URLS', '').strip():
            prev = runs.get(kind, {})
            runs[kind] = {'lastSuccessAt': prev.get('lastSuccessAt'),
                          'lastAttemptAt': STAMP, 'outcome': 'not_configured',
                          'summary': 'Create Web-source Google Alerts and add their RSS URLs to the GOOGLE_ALERTS_RSS_URLS GitHub Actions secret.'}
            print('google_alerts: not configured (no private RSS feed URLs)')
            continue
        try:
            matches, errors = fetcher()
            for found in matches:
                if found['id'] in existing:
                    found['firstSeen'] = existing[found['id']].get('firstSeen', DATE)
                    if existing[found['id']].get('status') in ('Confirmed filing', 'Reviewed solicitation'):
                        found['status'] = existing[found['id']]['status']
                existing[found['id']] = found
            runs[kind] = {'lastSuccessAt': STAMP, 'lastAttemptAt': STAMP,
                          'outcome': 'partial' if errors else 'success',
                          'summary': f'{len(matches)} matched records; {len(errors)} source failures. Leads require review.'}
            print(f'{kind}: {len(matches)} matches, {len(errors)} source failures')
        except (ValueError, RuntimeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            previous = runs.get(kind, {})
            runs[kind] = {'lastSuccessAt': previous.get('lastSuccessAt'), 'lastAttemptAt': STAMP,
                          'outcome': 'failed', 'summary': short(exc, 260)}
            print(f'{kind}: unavailable: {exc}', file=sys.stderr)
    # Consolidate after every refresh so syndicated copies cannot reappear later.
    consolidated = consolidate_solicitations(list(existing.values()))
    payload['records'] = sorted(consolidated, key=lambda r: (r.get('date') or '', r['id']), reverse=True)[:1500]
    active = [k for k in ('cases', 'case_news', 'solicitations', 'google_alerts', 'law_firms') if runs.get(k, {}).get('outcome') in ('success', 'partial')]
    note = 'Free-source discovery active: ' + ', '.join(active) if active else 'All monitored sources failed; showing saved records.'
    if 'cases' not in active:
        note += '. Federal CourtListener docket search inactive; add COURTLISTENER_TOKEN.'
    if runs.get('google_alerts', {}).get('outcome') == 'not_configured':
        note += ' Google Alerts Web feeds await configuration.'
    note += ' State trial-court filing portals are not automatically searched.'
    payload['monitoring'] = {'deployed': True, 'source': 'zero-cost-public-source-discovery',
                             'lastAttemptAt': STAMP, 'note': note}
    DATA_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return 0 if active else 2


if __name__ == '__main__':
    sys.exit(main())
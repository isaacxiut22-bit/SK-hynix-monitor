#!/usr/bin/env python3
"""Refresh SK hynix public-source litigation leads, without upgrading leads to confirmed claims.

CourtListener API token is supplied only through the environment; it never goes in the website.
Google News RSS is a discovery feed, not a docket database or definitive solicitation list.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / 'tracker-data.json'
NOW = dt.datetime.now(dt.timezone.utc)
STAMP = NOW.isoformat(timespec='seconds').replace('+00:00','Z')
DATE = NOW.date().isoformat()
AGENT = 'SKHynixLitigationWatch/1.0 (+public-source monitoring; contact site maintainer)'
USER_QUERY = 'party:(hynix)'
RSS_QUERIES = [
    '"SK hynix" lawsuit law firm investigation',
    '"SK hynix" securities fraud shareholder class action',
    '"SK hynix" plaintiff firms investors encouraged contact',
    '"SK Hynix" antitrust price fixing litigation firm',
]
ALIASES = re.compile(r'\b(?:sk\s*[- ]?\s*hynix|hynix semiconductor|solidigm)\b',re.I)
LEGAL = re.compile(r'\b(?:lawsuit|litigat\w*|legal action|investigat\w*|securit\w* fraud|class[ -]?action|plaintiff|antitrust|price[ -]?fix\w*|shareholder|law firm|investors? (?:encouraged|urged)|lead plaintiff|investor alert)\b',re.I)
SOLICIT = re.compile(r'\b(?:investigat\w*|securit\w* fraud|class[ -]?action|law firm|investors? (?:encouraged|urged)|lead plaintiff|seeking investors|investor alert|shareholder rights)\b',re.I)


def get(url: str, *, token: str = '', timeout: int = 30) -> bytes:
    headers = {'User-Agent': AGENT, 'Accept': 'application/json, application/rss+xml, application/xml'}
    if token:
        headers['Authorization'] = 'Token ' + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        if r.status != 200:
            raise ValueError('HTTP '+str(r.status))
        return r.read(4_000_000)


def as_text(s):
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',str(s or '')))).strip()


def short(s, limit=500):
    s=as_text(s)
    return s[:limit].rstrip() + ('…' if len(s)>limit else '')


def case_category(s):
    s=(s or '').lower()
    if any(w in s for w in ('antitrust','price fix','monopol','competition')):
        return 'Antitrust'
    if any(w in s for w in ('securit','shareholder','exchange act')):
        return 'Securities'
    if any(w in s for w in ('patent','copyright','trademark','intellectual property')):
        return 'IP'
    if any(w in s for w in ('employment','labor','discrimination','wage')):
        return 'Employment'
    return 'Other Litigation'


def parse_date(raw):
    if not raw:
        return None
    candidate=str(raw)[:10]
    try:
        dt.date.fromisoformat(candidate)
        return candidate
    except ValueError:
        return None


def courtlistener(token):
    if not token:
        raise RuntimeError('COURTLISTENER_TOKEN not configured')
    # Party-name field limits accidental hits from pleadings simply mentioning SK hynix.
    params=urllib.parse.urlencode({'q': USER_QUERY, 'type':'d', 'order_by':'dateFiled desc'})
    url='https://www.courtlistener.com/api/rest/v4/search/?'+params
    payload=json.loads(get(url,token=token))
    if not isinstance(payload.get('results'),list):
        raise ValueError('CourtListener response missing results')
    out=[]
    for r in payload['results']:
        docket_id=r.get('docket_id') or r.get('docketId') or r.get('id')
        if not docket_id:
            continue
        caption=as_text(r.get('caseName') or r.get('case_name') or r.get('caseNameFull') or '')
        docket_no=as_text(r.get('docketNumber') or r.get('docket_number') or '')
        court=as_text(r.get('court') or r.get('court_id') or 'Federal court')
        case_url=r.get('absolute_url') or r.get('absoluteUrl') or ''
        case_url=urllib.parse.urljoin('https://www.courtlistener.com/',case_url) if case_url.startswith('/') else case_url
        if not (case_url.startswith('https://www.courtlistener.com/') or case_url.startswith('https://courtlistener.com/')):
            case_url=f'https://www.courtlistener.com/?type=d&q={urllib.parse.quote("docket_id:"+str(docket_id))}'
        # CourtListener docket metadata represents a matching indexed result; source is linked.
        out.append({
            'id':'cl-'+str(docket_id),'tracker':'cases',
            'title':caption or f'Federal docket {docket_no or docket_id}',
            'entity':'SK hynix / subsidiary (party-name search match; confirm on docket)',
            'date':parse_date(r.get('dateFiled') or r.get('date_filed')),
            'firstSeen':DATE,'category':case_category(caption+' '+str(r.get('suitNature',''))),
            'status':'Docket match — review',
            'forum':court+((' · '+docket_no) if docket_no else ''),
            'firm':'Not established',
            'summary':'Federal docket matched CourtListener’s SK hynix party-name search. Review the linked docket for the exact party identity, claims, and filing status. A search match alone does not establish that SK hynix is a defendant.',
            'scope':'U.S. federal docket; RECAP coverage is not comprehensive and can lag PACER.',
            'sources':[{'label':'CourtListener federal docket','url':case_url}],
            'baseline':False,
        })
    return out


def rss_articles(xml_bytes):
    root=ET.fromstring(xml_bytes)
    for item in root.findall('.//item'):
        def tag(name):
            t=item.find(name)
            return (t.text or '') if t is not None else ''
        yield dict(title=as_text(tag('title')),desc=as_text(tag('description')),
                   link=tag('link').strip(),published=tag('pubDate'),
                   publisher=as_text(tag('source')))


def solicitation_record(article):
    title=article['title']; desc=article['desc']; link=article['link']
    combined=title+' '+desc
    if not (ALIASES.search(combined) and LEGAL.search(combined) and SOLICIT.search(combined)):
        return None
    if not link.startswith('https://'):
        return None
    # RSS can contain reporting rather than direct plaintiff-firm outreach.
    # Keep the status explicitly unverified.
    try:
        when=email.utils.parsedate_to_datetime(article['published']).date().isoformat()
    except (TypeError,ValueError):
        when=None
    title=re.sub(r'\s+-\s+[^-]{2,80}$','',title) or title
    publisher=article['publisher'] or 'Public news/press-release feed'
    identity=(title.lower()+'|'+str(when)).encode('utf-8')
    key=hashlib.sha256(identity).hexdigest()[:16]
    category=case_category(combined)
    if category=='Other Litigation' and re.search(r'investor|shareholder|securities',combined,re.I):
        category='Securities'
    return {'id':'news-'+key,'tracker':'solicitations','title':short(title,200),
            'entity':'SK hynix (reporting/announcement match)', 'date':when,
            'firstSeen':DATE, 'category':category,
            'status':'Unverified solicitation lead',
            'forum':'Public announcement or media report',
            'firm':publisher,
            'summary':short(desc or title,500)+' Review original publication to determine if a plaintiff firm is actually soliciting clients. A news report does not imply that a lawsuit was filed.',
            'scope':'Automated RSS discovery; not verified against court records or direct firm website.',
            'sources':[{'label':'News/press-release source via Google News','url':link}],
            'baseline':False}


def news_feed():
    results={}
    successful=0
    errors=[]
    for q in RSS_QUERIES:
        url='https://news.google.com/rss/search?'+urllib.parse.urlencode({'q':q,'hl':'en-US','gl':'US','ceid':'US:en'})
        try:
            for article in rss_articles(get(url)):
                found=solicitation_record(article)
                if found:
                    results[found['id']]=found
            successful+=1
        except (urllib.error.URLError,ValueError,ET.ParseError,TimeoutError) as exc:
            errors.append(f'{q}: {str(exc)[:150]}')
    if not successful:
        raise RuntimeError('No Google News RSS sources could be retrieved: '+'; '.join(errors))
    return list(results.values()),errors


def main():
    payload=json.loads(DATA_FILE.read_text(encoding='utf-8'))
    existing={r['id']:r for r in payload.get('records',[])}
    runs=payload.setdefault('runs',{})
    results={}
    for tracker,fn in [('cases',lambda:courtlistener(os.environ.get('COURTLISTENER_TOKEN',''))),
                       ('solicitations',news_feed)]:
        try:
            value=fn()
            matches,errors=value if tracker=='solicitations' else (value,[])
            for new in matches:
                if new['id'] in existing:
                    # Preserve the original firstSeen and any previous manual review.
                    new['firstSeen']=existing[new['id']].get('firstSeen',DATE)
                    if existing[new['id']].get('status') in ('Confirmed filing','Reviewed solicitation'):
                        new['status']=existing[new['id']]['status']
                existing[new['id']]=new
            runs[tracker]={'lastSuccessAt':STAMP,'lastAttemptAt':STAMP,
                           'outcome':'partial' if errors else 'success',
                           'summary':f'{len(matches)} source matches this run, {len(errors)} feed failures. Automated matches require review.'}
            print(f'{tracker}: {len(matches)} matches, {len(errors)} feed errors')
        except (ValueError,RuntimeError,urllib.error.URLError,TimeoutError,json.JSONDecodeError) as exc:
            previous=runs.get(tracker,{})
            runs[tracker]={'lastSuccessAt':previous.get('lastSuccessAt'),
                           'lastAttemptAt':STAMP, 'outcome':'failed','summary':str(exc)[:240]}
            print(f'{tracker}: unavailable: {exc}',file=sys.stderr)
    payload['records']=sorted(existing.values(),key=lambda r:(r.get('date') or '',r['id']),reverse=True)[:1200]
    case_ok=runs.get('cases',{}).get('outcome')=='success'
    news_ok=runs.get('solicitations',{}).get('outcome') in ('success','partial')
    if case_ok and news_ok:
        note='Automated public-source checks completed. New matches are leads, not independently verified legal conclusions.'
    elif news_ok:
        note='Public solicitation feeds updated; CourtListener federal docket monitoring is unavailable or not configured.'
    elif case_ok:
        note='Federal docket search updated; solicitation news feeds unavailable.'
    else:
        note='Automated source checks failed or are not configured; the dashboard retains its saved records.'
    payload['monitoring']={'deployed':True,'source':'automated-public-feeds','lastAttemptAt':STAMP,'note':note}
    DATA_FILE.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    # Never mark the dashboard fully connected merely because execution finished.
    if not (case_ok or news_ok):
        return 2
    return 0

if __name__=='__main__':
    sys.exit(main())
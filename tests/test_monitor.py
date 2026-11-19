import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import json

PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'monitor.py'
spec = importlib.util.spec_from_file_location('monitor', PATH)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def article(title, desc='', link='https://example.com/story', published='Thu, 19 Nov 2026 10:00:00 GMT'):
    return {'title': title, 'desc': desc, 'link': link, 'published': published, 'publisher': 'Example News'}


class MonitorTests(unittest.TestCase):
    def test_rss_preserves_unverified_status(self):
        feed = b'<rss><channel><item><title>SK hynix investors encouraged to contact Example Law Firm</title><link>https://example.com/notice</link><pubDate>Thu, 19 Nov 2026 10:00:00 GMT</pubDate><description>Example Law Firm investigating potential securities fraud class action involving SK hynix.</description><source>Example Press Wire</source></item></channel></rss>'
        r = monitor.solicitation_record(list(monitor.rss_articles(feed))[0])
        self.assertEqual(r['status'], 'Unverified solicitation lead')
        self.assertEqual(r['category'], 'Securities')
        self.assertEqual(r['date'], '2026-11-19')
        self.assertEqual(r['sources'][0]['url'], 'https://example.com/notice')

    def test_irrelevant_article_not_captured(self):
        a = article('SK hynix announces new DRAM product', 'chip launch')
        self.assertIsNone(monitor.solicitation_record(a))
        self.assertIsNone(monitor.case_news_record(a))

    def test_news_filing_is_not_confirmed_case(self):
        r = monitor.case_news_record(article('New lawsuit filed against SK hynix America', 'Complaint filed in California state court.'))
        self.assertEqual(r['tracker'], 'cases')
        self.assertEqual(r['status'], 'Unverified filing report')
        self.assertIn('state-court', r['forum'])
        self.assertIn('NOT identified from a court docket', r['scope'])

    def test_motion_not_new_filing_report(self):
        self.assertIsNone(monitor.case_news_record(article('SK hynix filed motion to dismiss', 'Judge denied motion yesterday')))

    def test_firm_link_extracted_and_kept_unverified(self):
        page = b'<table><tr><td>SK hynix Inc.</td><td><a href="/sk-hynix-investigation/">Learn More</a></td></tr></table>'
        r = monitor.direct_firm_records(page, 'Example Firm', 'https://www.ktmc.com/newcases/')
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['status'], 'Unverified firm website lead')
        self.assertEqual(r[0]['sources'][0]['url'], 'https://www.ktmc.com/sk-hynix-investigation/')

    def test_no_external_firm_link_or_cross_domain(self):
        page = b'<a href="https://malicious.invalid/case">SK hynix investigation</a>'
        self.assertEqual(monitor.direct_firm_records(page, 'Example', 'https://www.ktmc.com/newcases/'), [])

    def test_courtlistener_paginates_and_deduplicates(self):
        obj = {'results': [{'docket_id': 2026, 'caseName': 'Example v. SK hynix America', 'dateFiled': '2026-11-19', 'court': 'N.D. California', 'docketNumber': '5:26-cv-99999', 'absolute_url': '/docket/2026/test/'}], 'next': None}
        with patch.object(monitor, 'get', return_value=json.dumps(obj).encode()) as getter:
            rows, errors = monitor.courtlistener('fake-token')
        self.assertEqual(len(rows), 1)
        self.assertEqual(errors, [])
        self.assertEqual(getter.call_count, len(monitor.COURT_QUERIES))
        self.assertEqual(rows[0]['status'], 'Docket match — review')
        self.assertTrue(rows[0]['sources'][0]['url'].startswith('https://www.courtlistener.com/docket/'))

    def test_courtlistener_token_is_required(self):
        with self.assertRaisesRegex(RuntimeError, 'not configured'):
            monitor.courtlistener('')

    def test_courtlistener_invalid_response_fails(self):
        with patch.object(monitor, 'get', return_value=b'{}'):
            with self.assertRaisesRegex(RuntimeError, 'All CourtListener searches failed'):
                monitor.courtlistener('fake-token')

    def test_rss_group_matches_and_partial_status(self):
        feed = b'<rss><channel><item><title>SK hynix sued in a new lawsuit</title><link>https://example.com/a</link><description>SK hynix sued in California state court</description></item></channel></rss>'
        with patch.object(monitor, 'get', return_value=feed):
            rows, errors = monitor.rss_feed('case_news')
        self.assertEqual(len(rows), 1)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]['tracker'], 'cases')

    def test_robots_disallow_respected(self):
        with patch.object(monitor, 'get', return_value=b'User-agent: *\nDisallow: /cases\n'):
            self.assertFalse(monitor.robots_allowed('https://www.hbsslaw.com/cases'))


    def test_kessler_campaign_collapses_baseline_news_and_direct_firm_link(self):
        baseline = dict(id='ktmc-securities-2026-11', tracker='solicitations',
                        title='Kessler Topaz Meltzer & Check', firm='Kessler Topaz Meltzer & Check, LLP',
                        date='2026-11-18', firstSeen='2026-11-19', baseline=True,
                        category='Securities', status='Investigation',
                        summary='Firm investigation of potential SK hynix federal securities violations.',
                        scope='SK hynix securities; investors.',
                        sources=[{'label':'Original PR Newswire release',
                                  'url':'https://www.prnewswire.com/example'}])
        news = dict(id='news-kessler', tracker='solicitations',
                    title='NASDAQ: SKHY Investigation Alert: SK hynix Investors Encouraged to Contact Kessler Topaz',
                    firm='PR Newswire', date='2026-11-19', firstSeen='2026-11-20',
                    category='Securities', status='Unverified solicitation lead',
                    summary='SK hynix investors urged to contact Kessler Topaz.',
                    sources=[{'label':'Google News', 'url':'https://news.google.com/rss/articles/a'}])
        direct = dict(id='firm-kessler', tracker='solicitations',
                      title='SK hynix Inc. NASDAQ SKHY Learn More',
                      firm='Kessler Topaz (investigations)', date=None,
                      firstSeen='2026-11-20', category='Other Litigation',
                      status='Unverified firm website lead',
                      summary='Direct plaintiff-firm investigation listing.',
                      sources=[{'label':'Firm website',
                                'url':'https://www.ktmc.com/skhy-sk-hyix-inc-investigation/'}])
        merged = monitor.consolidate_solicitations([news, baseline, direct])
        self.assertEqual(len(merged), 1)
        result = merged[0]
        self.assertEqual(result['id'], baseline['id'])
        self.assertEqual(result['date'], '2026-11-18')
        self.assertEqual(result['firstSeen'], '2026-11-19')
        self.assertEqual(result['status'], 'Investigation')
        self.assertEqual(result['category'], 'Securities')
        self.assertIn('securities investigation', result['title'])
        self.assertEqual(len(result['sources']), 3)
        self.assertEqual(len(result['mergedRecordIds']), 3)
        # Simulate repeated scheduled RSS pulls: no duplicate or runaway scope.
        again = monitor.consolidate_solicitations([result, news, direct])
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]['id'], baseline['id'])
        self.assertEqual(len(again[0]['sources']), 3)
        self.assertEqual(again[0]['scope'], result['scope'])
        self.assertEqual(again[0]['relatedHeadlines'], result['relatedHeadlines'])

    def test_rosen_same_campaign_merges_but_different_firm_stays_separate(self):
        base = dict(id='rosen-baseline', tracker='solicitations',
                    title='Rosen Law Firm', firm='Rosen Law Firm',
                    date='2026-11-18', category='Securities', baseline=True,
                    summary='Rosen announces SK hynix securities class action investigation.',
                    sources=[{'label':'Firm press release', 'url':'https://example.com/rosen'}])
        syndication = dict(base, id='news-rosen', baseline=False,
                           title='Rosen Law Firm Encourages SK hynix Investors to Inquire About Securities Class Action Investigation',
                           firm='Business Wire', date='2026-11-17',
                           sources=[{'label':'Google News', 'url':'https://news.google.com/rosen'}])
        another_firm = dict(base, id='levi', title='Levi & Korsinsky SK hynix investor investigation',
                            firm='Levi & Korsinsky, LLP')
        results = monitor.consolidate_solicitations([base, syndication, another_firm])
        self.assertEqual(len(results), 2)
        self.assertEqual({r['firm'] for r in results}, {'Rosen Law Firm', 'Levi & Korsinsky, LLP'})

    def test_distinct_campaigns_are_not_grouped(self):
        base = dict(id='securities', tracker='solicitations',
                    title='Rosen Law Firm launches SK hynix securities investigation',
                    firm='Rosen Law Firm', date='2026-11-18',
                    category='Securities', sources=[{'url':'https://example.com/a','label':'A'}])
        antitrust = dict(base, id='antitrust', title='Rosen Law Firm launches SK hynix price-fixing antitrust litigation',
                         date='2026-11-18', category='Antitrust',
                         sources=[{'url':'https://example.com/b','label':'B'}])
        later = dict(base, id='later', date='2027-03-18',
                     sources=[{'url':'https://example.com/c','label':'C'}])
        no_firm = dict(base, id='no-firm', firm='Business Wire',
                       title='SK hynix securities investigation noted by media',
                       sources=[{'url':'https://example.com/d','label':'D'}])
        case = dict(base, id='case', tracker='cases')
        groups = monitor.consolidate_solicitations([base, antitrust, later, no_firm, case])
        self.assertEqual(len(groups), 5)
        self.assertEqual(len([r for r in groups if r['tracker']=='cases']), 1)


    def test_securities_investigation_does_not_become_antitrust_from_background(self):
        baseline = dict(id='levi-baseline', tracker='solicitations',
                        title='Levi & Korsinsky', firm='Levi & Korsinsky, LLP',
                        date=None, category='Securities', baseline=True,
                        forum='Prospective U.S. securities litigation',
                        summary='Investigation page concerning disclosures about DRAM price-fixing litigation.',
                        sources=[{'label':'Firm investigation page',
                                  'url':'https://zlk.com/investigations/sk-hynix-inc-investigation-skhy'}])
        news = dict(id='levi-news', tracker='solicitations',
                    title='SK hynix Inc. Investigation Notice: Levi & Korsinsky Notifies Investors of Pending Investigation (SKHY)',
                    firm='ACCESS Newswire', category='Securities', date='2026-11-18',
                    summary='Levi & Korsinsky notifies SK hynix investors of investigation.',
                    sources=[{'label':'Newswire', 'url':'https://news.google.com/levi'}])
        self.assertEqual(monitor.solicitation_topic(baseline), 'securities-investigation')
        self.assertEqual(monitor.solicitation_topic(news), 'securities-investigation')
        merged = monitor.consolidate_solicitations([baseline, news])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['id'], 'levi-baseline')
        self.assertEqual(len(merged[0]['sources']), 2)

    def test_merging_sources_keeps_every_unique_url(self):
        base = dict(id='b', tracker='solicitations', firm='Kessler Topaz Meltzer & Check',
                    title='Kessler Topaz SKHY shareholder investigation',
                    date='2026-11-18', summary='',
                    sources=[{'url':'https://example.com/a','label':'Original'}])
        repeated = dict(base, id='n',
                        sources=[{'url':'https://example.com/a','label':'Duplicate'},
                                 {'url':'https://example.com/b','label':'Syndication'}])
        result = monitor.consolidate_solicitations([base, repeated])
        self.assertEqual(len(result), 1)
        self.assertEqual([s['url'] for s in result[0]['sources']],
                         ['https://example.com/a', 'https://example.com/b'])


if __name__ == '__main__':
    unittest.main()
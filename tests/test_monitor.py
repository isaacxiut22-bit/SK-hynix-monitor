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


if __name__ == '__main__':
    unittest.main()
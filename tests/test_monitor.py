import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import json

PATH=Path(__file__).resolve().parents[1]/'scripts'/'monitor.py'
spec=importlib.util.spec_from_file_location('monitor', PATH)
monitor=importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)

class MonitorTests(unittest.TestCase):
    def test_rss_preserves_unverified_status(self):
        feed=b'''<rss><channel><item><title>SK hynix investors encouraged to contact Example Law Firm</title><link>https://example.com/notice</link><pubDate>Thu, 19 Nov 2026 10:00:00 GMT</pubDate><description>Example Law Firm is investigating potential securities fraud class action involving SK hynix.</description><source>Example Press Wire</source></item></channel></rss>'''
        article=list(monitor.rss_articles(feed))[0]
        r=monitor.solicitation_record(article)
        self.assertEqual(r['status'],'Unverified solicitation lead')
        self.assertEqual(r['category'],'Securities')
        self.assertEqual(r['date'],'2026-11-19')
        self.assertEqual(r['sources'][0]['url'],'https://example.com/notice')
    def test_discard_irrelevant_feed(self):
        r=monitor.solicitation_record({'title':'SK hynix announces new DRAM product','desc':'a chip launch','link':'https://example.com','published':'','publisher':'Example'})
        self.assertIsNone(r)
    def test_docket_remains_unverified(self):
        mocked=json.dumps({'results':[{'docket_id':2026,'caseName':'Example v. SK hynix America', 'dateFiled':'2026-11-19', 'court':'N.D. California', 'docketNumber':'5:26-cv-99999','absolute_url':'/docket/2026/test/'}]}).encode()
        with patch.object(monitor,'get',return_value=mocked):
            rows=monitor.courtlistener('fake-token')
        self.assertEqual(rows[0]['status'],'Docket match — review')
        self.assertEqual(rows[0]['id'],'cl-2026')
        self.assertTrue(rows[0]['sources'][0]['url'].startswith('https://www.courtlistener.com/docket/'))
    def test_invalid_docket_response(self):
        with patch.object(monitor,'get',return_value=b'{}'):
            with self.assertRaises(ValueError):
                monitor.courtlistener('fake-token')

if __name__=='__main__':unittest.main()
import unittest
from urllib.parse import parse_qs, urlparse
from legal_news import core


class OfficialAPIReaderTests(unittest.TestCase):
    def setUp(self):
        self.item = dict(eoNumber="0001202611020001",title="О персональных данных",publishDateShort="2026-11-02T00:00:00")

    def test_complete_pagination_and_deduplication(self):
        calls=[]
        def fetch(url):
            query=parse_qs(urlparse(url).query)
            calls.append(query)
            item=dict(self.item)
            item["eoNumber"]="000120261102000"+query["index"][0]
            return dict(items=[item],pagesTotalCount=2)
        rows=core.publications("2026-11-02","2026-11-02",fetch)
        self.assertEqual(len(rows),2)
        self.assertEqual(len(calls),8)
        self.assertNotIn("court",[q["block"][0] for q in calls])

    def test_invalid_responses_fail(self):
        for payload in [{},{"items":[]},{"items":[],"pagesTotalCount":1},
                        {"items":[self.item],"pagesTotalCount":201}]:
            with self.subTest(payload=payload),self.assertRaises(ValueError):
                core.publications("2026-11-02","2026-11-02",lambda url:payload)

    def test_repeated_page_fails(self):
        with self.assertRaises(ValueError):
            core.publications("2026-11-02","2026-11-02",lambda url:dict(items=[self.item],pagesTotalCount=2))

    def test_source_ignoring_date_filter_fails(self):
        item=dict(self.item,publishDateShort="2020-01-01T00:00:00")
        with self.assertRaises(ValueError):
            core.publications("2026-11-02","2026-11-02",lambda url:dict(items=[item],pagesTotalCount=1))

    def test_empty_day_is_valid(self):
        self.assertEqual(core.publications("2026-11-02","2026-11-02",lambda url:dict(items=[],pagesTotalCount=0)),[])


if __name__=="__main__": unittest.main()

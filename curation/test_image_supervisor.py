import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from curation import image_supervisor as s

class SupervisorTests(unittest.TestCase):
    def test_remaining_does_not_loop_on_exhausted_or_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            run=Path(temp)
            with sqlite3.connect(run/'annotations.sqlite') as c:
                c.execute('CREATE TABLE images(status TEXT,attempts INTEGER)')
                c.executemany('INSERT INTO images VALUES(?,?)',[('done',1),('missing',1),('error',3)])
            self.assertFalse(s.remaining(run))
            with sqlite3.connect(run/'annotations.sqlite') as c:
                c.execute("INSERT INTO images VALUES('error',2)")
            self.assertTrue(s.remaining(run))
    def test_wrong_model_is_not_ready(self):
        from io import BytesIO
        class Opener:
            def open(self,*a,**kw): return BytesIO(json.dumps({'data':[{'id':'other'}]}).encode())
        with patch.object(s.urllib.request,'build_opener',return_value=Opener()):
            self.assertEqual(s.health(),'wrong_model')
    def test_dead_process_is_not_adopted(self):
        self.assertEqual(s.process_args(999999999),[])

if __name__=='__main__': unittest.main()

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_audit',ROOT/'checks/audit_release.py')
audit_module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class PublicationAuditTests(unittest.TestCase):
    def setUp(self):
        work=ROOT/'work'
        work.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)

    def test_detects_token_without_printing_value(self):
        token='123456789'+':'+'A'*35
        (self.folder/'config.txt').write_text(token)
        result=audit_module.audit(self.folder,True)
        self.assertFalse(result['passed'])
        self.assertEqual(result['findings'][0]['rule'],'telegram_token')
        self.assertNotIn(token,str(result))

    def test_detects_private_files_and_history(self):
        (self.folder/'.env').write_text('PRIVATE_VALUE=not_for_publication')
        (self.folder/'.git').mkdir()
        (self.folder/'export.sqlite').write_bytes(b'private database')
        result=audit_module.audit(self.folder,True)
        self.assertEqual(len(result['findings']),3)

    def test_allows_empty_example_and_variable_templates(self):
        (self.folder/'.env.example').write_text('DATABASE_URL=\nTELEGRAM_TOKEN=\n')
        (self.folder/'compose.yml').write_text('postgresql://${SERVICE_USER_POSTGRES}:${SERVICE_PASSWORD_POSTGRES}@postgres/db')
        self.assertTrue(audit_module.audit(self.folder,True)['passed'])

    def test_rejects_populated_example_and_real_password_uri(self):
        (self.folder/'.env.example').write_text('TELEGRAM_TOKEN=nonempty\n')
        uri='postgresql://'+'demo'+':'+'not-a-real-password'+'@localhost/db'
        (self.folder/'config.txt').write_text(uri)
        result=audit_module.audit(self.folder,True)
        self.assertEqual({row['rule'] for row in result['findings']},{'connection_password','nonempty_environment_example'})


if __name__=='__main__': unittest.main()

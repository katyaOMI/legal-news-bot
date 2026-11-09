import contextlib
from io import StringIO
import os
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import urlopen

import db
from db.privacy import redact
from bot import main as bot


class PrivacyTests(unittest.TestCase):
    def test_plain_and_encoded_environment_values_are_hidden(self):
        secret='local-test-'+'credential/a?b=1'
        with patch.dict(os.environ,{'TELEGRAM_TOKEN':secret}):
            output=redact('failure '+secret+' '+quote(secret,safe=''))
        self.assertNotIn(secret,output)
        self.assertNotIn(quote(secret,safe=''),output)

    def test_token_formats_and_credential_urls_are_hidden(self):
        token='123456789'+':'+'A'*35
        uri='postgresql://'+'demo'+':'+'local-test-password'+'@localhost/db'
        output=redact('failure '+token+' '+uri)
        self.assertNotIn(token,output)
        self.assertNotIn('local-test-password',output)
        self.assertIn('[REDACTED]',output)

    def test_short_environment_secret_is_also_hidden(self):
        value='a'+'7z'
        with patch.dict(os.environ,{'POSTGRES_PASSWORD':value}):
            self.assertNotIn(value,redact('failure '+value))

    def test_database_detail_does_not_reveal_telegram_identifier(self):
        value=str(987654321)
        output=redact('Key (chat_id)=('+value+') already exists')
        self.assertNotIn(value,output)

    def test_headers_and_password_parameters_are_hidden(self):
        credential='local-'+'test-value'
        output=redact('Authorization: Bearer '+credential+' password='+credential)
        self.assertNotIn(credential,output)

    def test_db_log_redacts_before_printing(self):
        value='test-'+'credential-hidden'
        stream=StringIO()
        with patch.dict(os.environ,{'POSTGRES_PASSWORD':value}),contextlib.redirect_stdout(stream):
            db.log('test','error '+value)
        self.assertNotIn(value,stream.getvalue())

    def test_fail_run_redacts_before_persistence(self):
        values=[]
        class Conn:
            def execute(self,sql,params):
                values.append(params)
        secret='test-'+'database-credential'
        with patch.dict(os.environ,{'DATABASE_URL':secret}),patch.object(db,'log'):
            db.fail_run(Conn(),'collector',1,RuntimeError(secret))
        self.assertNotIn(secret,str(values))

    def test_subscription_does_not_store_name_or_log_user_id(self):
        saved=[]
        class Conn:
            def execute(self,sql,params):
                saved.append(params)
        with patch.object(bot,'say'),patch.object(bot.db,'log') as log:
            bot.handle(Conn(),{'chat':{'id':987654321,'type':'private','username':'private-test-user'},'text':'/start'})
        self.assertEqual(saved[0][1],'')
        self.assertNotIn('private-test-user',str(log.call_args_list))
        self.assertNotIn('987654321',str(log.call_args_list))


class PublicHTTPTests(unittest.TestCase):
    def test_private_file_paths_are_not_served(self):
        server=bot.ThreadingHTTPServer(('127.0.0.1',0),bot.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch.object(bot.db,'connect') as connect:
                for path in ('/.env','/.git/config','/work/survey-source-results.json','/db/schema.sql'):
                    with self.subTest(path=path),self.assertRaises(HTTPError) as error:
                        urlopen('http://127.0.0.1:'+str(server.server_port)+path,timeout=5)
                    self.assertEqual(error.exception.code,404)
                    error.exception.close()
                connect.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_healthcheck_returns_no_database_details(self):
        server=bot.ThreadingHTTPServer(('127.0.0.1',0),bot.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch.object(bot.db,'connect') as connect:
                with urlopen('http://127.0.0.1:'+str(server.server_port)+'/health',timeout=5) as response:
                    self.assertEqual(response.read(),b'ok')
                connect.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_disabled_web_ui_does_not_read_or_publish_database(self):
        server=bot.ThreadingHTTPServer(('127.0.0.1',0),bot.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with patch.dict(os.environ,{'ENABLE_MINI_APP':''}),patch.object(bot.db,'connect') as connect:
                with urlopen('http://127.0.0.1:'+str(server.server_port)+'/',timeout=5) as response:
                    self.assertEqual(response.status,200)
                    self.assertEqual(response.read(),b'bot service is running')
                connect.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__=='__main__':
    unittest.main()

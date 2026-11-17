"""QA-only sample RCAR client for the pinned Trustee. Never used in SNP/TDX mode."""
import base64
import hashlib
import http.client
import json
import socket
import ssl
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlencode

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def decode(value):
    return base64.urlsafe_b64decode(value+'='*(-len(value)%4))


def integer(value):
    return base64.urlsafe_b64encode(value.to_bytes((value.bit_length()+7)//8,'big')).decode().rstrip('=')


class LocalKBS(http.client.HTTPSConnection):
    def connect(self):
        sock = socket.create_connection(('127.0.0.1',self.port),timeout=self.timeout)
        self.sock = self._context.wrap_socket(sock,server_hostname='kbs')


def run(config, preview, call, operation, path):
    binding = sha(canonical([preview,call,operation,path]))
    query = urlencode(dict(preview=preview,call=call,binding=binding))
    conn = LocalKBS('kbs',config['port'],timeout=8,context=ssl.create_default_context(cafile=config['ca']))
    cookie = ''
    def exchange(method, endpoint, body=None, suffix=''):
        headers = {'Content-Type':'application/json','User-Agent':'cabinet-sample-session/1'}
        if cookie:
            headers['Cookie'] = cookie
        conn.request(method,'/kbs/v0/'+endpoint+'?'+query+suffix,
                     canonical(body) if body is not None else None,headers)
        response = conn.getresponse()
        return response.status, response.getheader('Set-Cookie'), response.read()
    try:
        try:
            status, session, body = exchange('POST','auth',dict(version='0.4.0',tee='sample',**{'extra-params':{}}))
        except (ConnectionRefusedError,ConnectionResetError,http.client.RemoteDisconnected):
            return None, {'decision':'unavailable'}
        if status != 200:
            raise ValueError(f'auth HTTP {status}')
        start = time.monotonic_ns()
        jar = SimpleCookie(session)
        if len(jar)!=1:
            raise ValueError('invalid KBS session')
        cookie = next(iter(jar.values())).OutputString(attrs=[])
        nonce = json.loads(body)['nonce']
        session_ref = '&session='+sha(cookie.encode())+'&challenge='+sha(nonce.encode())
        private = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        numbers = private.public_key().public_numbers()
        jwk = dict(kty='RSA',alg='RSA-OAEP-256',n=integer(numbers.n),e=integer(numbers.e))
        runtime = {'nonce':nonce,'tee-pubkey':jwk}
        report = hashlib.sha384(canonical(dict(runtime,**{'additional-evidence':''}))).digest()
        evidence = dict(svn=binding,report_data=base64.b64encode(report).decode())
        status, _, body = exchange('POST','attest',{'runtime-data':runtime,
            'tee-evidence':{'primary_evidence':evidence,'additional_evidence':''}},suffix=session_ref)
        if status != 200:
            raise ValueError(f'attest HTTP {status}')
        token = json.loads(body)['token']
        header, payload, signature = token.split('.')
        sig = decode(signature)
        if json.loads(decode(header))['alg'] != 'ES256' or len(sig)!=64:
            raise ValueError('unexpected EAR signature')
        cert = x509.load_pem_x509_certificate(Path(config['as_cert']).read_bytes())
        cert.public_key().verify(utils.encode_dss_signature(int.from_bytes(sig[:32],'big'),
            int.from_bytes(sig[32:],'big')), (header+'.'+payload).encode(),ec.ECDSA(hashes.SHA256()))
        claims = json.loads(decode(payload))
        if not claims['iat'] <= time.time() < claims['exp']:
            raise ValueError('AS token outside its validity interval')
        cpu = claims['submods']['cpu0']
        signed = cpu['ear_verifier_claims']['runtime_data']
        attester = cpu['ear_attester_claims']
        if (signed['nonce'] != nonce or signed['tee-pubkey'] != jwk or attester['tee'] != 'sample'
                or attester['claims']['svn'] != binding or attester['report_data'] != evidence['report_data']):
            raise ValueError('AS session binding mismatch')
        token_hash = sha(token.encode())
        signing_hash = sha((header+'.'+payload).encode())
        status, _, body = exchange('GET','resource/'+path,suffix=session_ref+'&ear='+token_hash+'&proof='+sha(bytes.fromhex(signing_hash)+sig))
        end = time.monotonic_ns()
        key = None
        if status == 200:
            jwe = json.loads(body)
            protected = jwe['protected']
            if json.loads(decode(protected)) != {'alg':'RSA-OAEP-256','enc':'A256GCM'}:
                raise ValueError('unexpected JWE header')
            cek = private.decrypt(decode(jwe['encrypted_key']),padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
            key = AESGCM(cek).decrypt(decode(jwe['iv']),decode(jwe['ciphertext'])+decode(jwe['tag']),protected.encode())
            if len(key)!=32:
                raise ValueError('unexpected resource size')
        elif status != 403:
            raise ValueError(f'resource HTTP {status}')
        return key, dict(as_signing_digest=signing_hash,as_signature=signature,binding=binding,ear_sha256=token_hash,session_sha256=sha(cookie.encode()),
                         nonce_sha256=sha(nonce.encode()),begin_ns=start,end_ns=end,
                         decision='approved' if key is not None else 'denied')
    finally:
        conn.close()

"""Private S3-compatible ciphertext storage. No browser URLs or fallback disk store."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from media_attachments import MediaUnavailable


class S3Store:
    def __init__(self, config_file=None):
        try:
            from boto3 import client
            from botocore.config import Config
            self.config_file=Path(config_file or os.environ['SENSE_CABINET_OBJECT_CONFIG']).resolve()
            config = json.loads(self.config_file.read_text())
            self.config=config
            endpoint = urlsplit(config['endpoint'])
            if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path not in ('','/'):
                raise ValueError('HTTPS endpoint required')
            if 'ca_file' in config and (not isinstance(config['ca_file'],str) or not config['ca_file'] or not Path(config['ca_file']).is_file()):
                raise ValueError('A readable CA file is required')
            self.bucket = config['bucket']
            if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', self.bucket): raise ValueError('Invalid bucket')
            self.prefix = config['prefix']
            if not re.fullmatch(r'[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*', self.prefix) or len(self.prefix)>128: raise ValueError('Invalid prefix')
            self.client = client('s3', endpoint_url=config['endpoint'], region_name=config.get('region','us-east-1'),
                aws_access_key_id=Path(config['access_key_file']).read_text().strip(),
                aws_secret_access_key=Path(config['secret_key_file']).read_text().strip(),
                verify=config.get('ca_file',True),
                config=Config(signature_version='s3v4', connect_timeout=5, read_timeout=15,
                              retries={'max_attempts':2}, proxies={}, s3={'addressing_style':'path'}))
            if config.get('ca_file',True) is False: raise ValueError('TLS verification required')
        except (ImportError, KeyError, ValueError, TypeError, OSError):
            raise MediaUnavailable('独立对象存储配置或 boto3 依赖不可用；禁止回退本地明文存储') from None

    def key(self, key):
        if not re.fullmatch(r'[0-9a-f]{64}',key): raise ValueError('Invalid object key')
        return self.prefix.rstrip('/')+'/'+key

    def put(self,key,data):
        try:
            # A fresh random key per attempt: failed commits leave only unreachable ciphertext.
            self.client.put_object(Bucket=self.bucket, Key=self.key(key), Body=data,
                                   ContentType='application/octet-stream', IfNoneMatch='*')
        except Exception:
            raise MediaUnavailable('对象存储写入失败；可重试当前分块') from None

    def get(self,key,size):
        try:
            response=self.client.get_object(Bucket=self.bucket,Key=self.key(key))
            body=response['Body']
            try:
                if response['ContentLength']!=size: raise ValueError()
                data=body.read(size+1)
                if len(data)!=size: raise ValueError()
                return data
            finally: body.close()
        except Exception:
            raise MediaUnavailable('分块对象缺失、长度异常或存储不可用；拒绝读取') from None


def configured_store():
    return S3Store()

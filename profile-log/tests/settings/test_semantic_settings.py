import pytest
from agentmesh_log import settings


ENDPOINT = 'https://embed.krisb8.com'
CREDENTIALS = 'keychain:org.agentmesh.embed'


def test_section():
    value = {'semantic': {'mode': 'hybrid', 'model': 'bge-m3:latest',
                         'embed_labels': ['general', 'internal'], 'min_score': 0.45}}
    assert settings.validate(value) == value


@pytest.mark.parametrize('section', [
    {}, {'mode': 'keyword'}, {'embed_labels': ['secret']},
    {'min_score': 0}, {'min_score': 1}, {'model': 'org/model:v1'},
    {'model': 'a' * 256},
], ids=['empty', 'kw', 'secret', 'zero', 'one', 'model', 'max'])
def test_valid(section):
    value = {'semantic': section}
    assert settings.validate(value) == value


@pytest.mark.parametrize('bad', [
    {'mode': 'semantic'}, {'min_score': 1.5}, {'embed_labels': []},
    {'embed_labels': ['public']}, {'model': 'http://x'}, {'extra': 1},
    None, [], {'mode': True}, {'model': 1}, {'model': ''},
    {'model': '-model'}, {'model': 'a' * 257}, {'model': 'a b'},
    {'embed_labels': 'general'}, {'embed_labels': ['general', 'general']},
    {'min_score': -0.1}, {'min_score': True}, {'min_score': '0.5'},
    {'min_score': float('nan')}, {'min_score': float('inf')},
], ids=['mode', 'high', 'empty', 'label', 'url', 'extra', 'null', 'list',
        'bool', 'num', 'blank', 'start', 'long', 'space', 'str', 'dupe',
        'low', 'flag', 'score', 'nan', 'inf'])
def test_bad(bad):
    with pytest.raises(ValueError, match='^invalid_settings$'):
        settings.validate({'semantic': bad})


@pytest.mark.parametrize('ref', [CREDENTIALS, 'wincred:org.agentmesh.embed'],
                         ids=['mac', 'win'])
def test_local(ref):
    value = {'semantic': {'endpoint': ENDPOINT, 'credentials': ref}}
    assert settings.validate(value, local=True) == value
    with pytest.raises(ValueError, match='^invalid_settings$'):
        settings.validate(value)


@pytest.mark.parametrize('section', [
    {'endpoint': ENDPOINT}, {'credentials': CREDENTIALS},
], ids=['url', 'ref'])
def test_pair(section):
    with pytest.raises(ValueError, match='^invalid_settings$'):
        settings.validate({'semantic': section}, local=True)


@pytest.mark.parametrize('url', [
    'http://embed.example.com', 'https://Embed.example.com',
    'https://localhost', 'https://embed.example.com:443',
    'https://embed.example.com/', 'https://embed.example.com/api',
    'https://user@embed.example.com', 'https://embed.example.com?q=x',
    'https://embed.example.com#x', 'https://-embed.example.com',
    'https://embed-.example.com', 'https://embed..example.com',
    'https://embed_example.com', 'https://embed.example.com.',
    'https://embed.example.com\n', None, 1,
], ids=['http', 'upper', 'host', 'port', 'slash', 'path', 'user', 'query',
        'frag', 'lead', 'trail', 'dots', 'under', 'dot', 'nl', 'null', 'num'])
def test_url(url):
    with pytest.raises(ValueError, match='^invalid_settings$'):
        settings.validate({'semantic': {'endpoint': url, 'credentials': CREDENTIALS}},
                          local=True)


@pytest.mark.parametrize('ref', [
    'plaintext-token', 'env:TOKEN', 'keychain:', 'wincred:',
    'Keychain:org.agentmesh.embed', 'keychain:org/agentmesh',
    'keychain:org_agentmesh', 'keychain:org agentmesh',
    'keychain:org.agentmesh\n', None, 1,
], ids=['raw', 'env', 'mac', 'win', 'upper', 'slash', 'under', 'space',
        'nl', 'null', 'num'])
def test_ref(ref):
    with pytest.raises(ValueError, match='^invalid_settings$'):
        settings.validate({'semantic': {'endpoint': ENDPOINT, 'credentials': ref}},
                          local=True)

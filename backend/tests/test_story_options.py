import json
import asyncio
import tempfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.plugins import STORY_OPTIONS_PROMPT
from app.story_options import has_complete_options, supplement_options, read_completion_stream, extract_story_options


def test_story_options_switch_controls_prompt_and_excludes_pet(monkeypatch):
    uploaded = []

    async def respond(request):
        body = json.loads(request.content)
        uploaded.append(body)
        if body['messages'][0]['content'].startswith('你是普通剧情分支补全器'):
            return httpx.Response(200, json={'choices':[{'message':{'content':'{"applicable":true,"options":["散步","读书"]}'}}]})
        content = '园丁走进花园。' if any(message['content'] == '测试补全' for message in body['messages']) else "普通剧情\n『剧情发展』\n1.[正常]散步\n2.[正常]读书"
        chunk = json.dumps({"choices":[{"delta":{"content":content},"finish_reason":"stop"}]}, ensure_ascii=False)
        return httpx.Response(200, content=f'data: {chunk}\ndata: [DONE]\n')

    original = httpx.AsyncClient
    monkeypatch.setattr("app.main.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    with tempfile.TemporaryDirectory() as directory:
        monkeypatch.setattr(database, "DATA_DIR", database.Path(directory))
        monkeypatch.setattr(database, "DB_PATH", database.DATA_DIR / "story.db")
        with TestClient(app) as client:
            plugins = {item['id']: item for item in client.get('/api/plugins').json()}
            assert plugins['story_options']['enabled'] is False
            settings = client.get('/api/settings').json()
            settings['api_key'] = 'mock'
            client.put('/api/settings', json=settings)
            character = client.post('/api/characters', json={'name': '园丁'}).json()
            conversation = client.post('/api/conversations', json={'character_id': character['id']}).json()
            route = f"/api/conversations/{conversation['id']}/chat"

            def send(pet=False):
                assert client.post(route, json={'content':'继续花园的故事', 'pet_chat_mode':pet}).status_code == 200
                return [message['content'] for message in uploaded[-1]['messages'] if message['role'] == 'system']

            assert STORY_OPTIONS_PROMPT not in send()
            assert client.put('/api/plugins/story_options/state', json={'enabled':True}).status_code == 200
            assert send().count(STORY_OPTIONS_PROMPT) == 1
            supplemented = client.post(route, json={'content':'测试补全'})
            assert supplemented.status_code == 200
            events = [json.loads(line) for line in supplemented.text.splitlines()]
            assert any(event.get('phase') == 'story_options' for event in events)
            replacement = next(event['replace'] for event in events if 'replace' in event)
            assert replacement.startswith('园丁走进花园。') and has_complete_options(replacement)
            assert client.get(f"/api/conversations/{conversation['id']}/messages").json()[-1]['content'] == replacement
            assert STORY_OPTIONS_PROMPT not in send(pet=True)
            database.init_db()
            assert next(item for item in client.get('/api/plugins').json() if item['id'] == 'story_options')['enabled']
            client.put('/api/plugins/story_options/state', json={'enabled':False})
            assert STORY_OPTIONS_PROMPT not in send()
            assert len(uploaded) == 6  # Exactly one extra call only for the missing block.


def test_option_validation():
    assert has_complete_options('正文\n『剧情发展』\n1.[正常]散步\n2.[正常]读书')
    assert has_complete_options('『剧情发展』\n1.[正常]散步')
    assert has_complete_options('『剧情发展』\n1.[正常]散步\n3.[其他]读书')
    assert not has_complete_options('```\n『剧情发展』\n1.[正常]散步\n2.[正常]读书\n```')
    assert has_complete_options('```\n角色状态：花园\n```\n『剧情发展』\n1.[正常]散步\n2.[未知]原文\n4.[其他]读书\n</Status_block>')
    assert [item['label'] for item in extract_story_options('『剧情发展』\n1.[普通]散步\n2.[探索]查看地图')] == ['普通', '探索']
    assert extract_story_options('『剧情发展』\n1.[普通]<script>内容</script>') == []
    assert extract_story_options('『剧情发展』\n1.[色情]不转换') == []


def test_supplement_retries_invalid_count_and_preserves_body(monkeypatch):
    calls = []
    async def respond(request):
        calls.append(json.loads(request.content))
        result = {'applicable': True, 'options': ['散步'] if len(calls) == 1 else ['散步', '整理书架']}
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps(result, ensure_ascii=False)}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr('app.story_options.httpx.AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    setting = {'base_url':'https://example.test/v1', 'model':'mock'}
    extra = asyncio.run(supplement_options(setting, {}, '继续', '园丁站在花园里。'))
    assert len(calls) == 2
    assert asyncio.run(supplement_options(setting, {}, '继续', '『剧情发展』\n1.[正常]散步\n4.[其他]整理书架')) == ''
    assert len(calls) == 2
    assert has_complete_options('园丁站在花园里。' + extra)
    assert '园丁站在花园里。' not in extra
    assert asyncio.run(supplement_options(setting, {}, '继续', '正文' + extra)) == ''
    assert len(calls) == 2


def test_inapplicable_or_service_error_does_not_invent_choices(monkeypatch):
    original = httpx.AsyncClient
    for status, result in [(200, {'choices':[{'message':{'content':'{"applicable":false,"options":[]}'}}]}), (503, {})]:
        async def respond(request):
            return httpx.Response(status, json=result)
        monkeypatch.setattr('app.story_options.httpx.AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
        assert asyncio.run(supplement_options({'base_url':'https://example.test/v1','model':'mock'}, {}, '什么是电感？', '电感是电路元件。')) == ('' if status == 200 else None)


def test_streamed_supplement_collects_fragments_and_ignores_reasoning(monkeypatch):
    async def respond(request):
        assert json.loads(request.content)['stream'] is True
        frames = [
            {'choices':[{'delta':{'reasoning_content':'内部推理'},'finish_reason':None}]},
            {'choices':[{'delta':{'content':'{"applicable":true,"options":['},'finish_reason':None}]},
            {'choices':[{'delta':{'content':'"散步","读书"]}'},'finish_reason':'stop'}]},
            {'choices':[], 'usage':{'total_tokens':30}},
        ]
        body = ': heartbeat\n\n' + ''.join('data: ' + json.dumps(frame, ensure_ascii=False) + '\n\n' for frame in frames) + 'data: [DONE]\n\n'
        return httpx.Response(200, headers={'content-type':'text/event-stream'}, content=body)
    original = httpx.AsyncClient
    monkeypatch.setattr('app.story_options.httpx.AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    extra = asyncio.run(supplement_options({'base_url':'https://example.test/v1','model':'mock'}, {}, '继续', '园丁在花园里。'))
    assert has_complete_options(extra)
    assert '内部推理' not in extra


@pytest.mark.parametrize('reason', [None, 'length'])
def test_incomplete_stream_is_not_accepted(reason):
    frame = {'choices':[{'delta':{'content':'{"applicable":true,"options":["散步","读书"]}'},'finish_reason':reason}]}
    response = httpx.Response(200, request=httpx.Request('POST','https://example.test'), content='data: '+json.dumps(frame)+'\n\n', headers={'content-type':'text/event-stream'})
    with pytest.raises(ValueError, match='incomplete'):
        asyncio.run(read_completion_stream(response))

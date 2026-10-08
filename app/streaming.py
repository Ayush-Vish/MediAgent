"""Provider SSE decoding and per-request draft delivery."""
from contextvars import ContextVar
import json
import re

import httpx

stream_sink = ContextVar('stream_sink', default=None)


def preview(raw):
    from app.providers import clean_llm_output
    from app.safety import reply_allowed
    # Never expose an unfinished reasoning block or a partial opening tag.
    text = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<think>.*$', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]*$', '', text)
    if len(text) < 16 or re.match(r"(?:Here's a thinking|Here is a thinking|Thinking Process)", text, re.I):
        return ''
    cleaned = clean_llm_output(text) or ''
    return cleaned if reply_allowed(cleaned) else ''


def provider_stream(url, headers, format, **kwargs):
    sink = stream_sink.get()
    with httpx.stream('POST', url, headers=headers, **kwargs) as response:
        if not response.is_success:
            response.read()
            return response
        raw = ''
        finished = False
        last_preview = None
        if sink:
            sink('draft', {'text': ''})
        data_lines = []
        def packets():
            for line in response.iter_lines():
                if not line:
                    if data_lines:
                        yield '\n'.join(data_lines)
                        data_lines.clear()
                elif line.startswith('data:'):
                    data_lines.append(line[5:].lstrip())
            if data_lines:
                yield '\n'.join(data_lines)
        for packet in packets():
            if packet == '[DONE]':
                finished = True
                break
            payload = json.loads(packet)
            if payload.get('error'):
                raise ValueError('Provider stream failed')
            if format == 'gemini':
                candidate = (payload.get('candidates') or [{}])[0]
                delta = ''.join(part.get('text', '') for part in candidate.get('content', {}).get('parts', [])
                                if not part.get('thought'))
                if candidate.get('finishReason') not in (None, 'STOP', 'MAX_TOKENS'):
                    raise ValueError('Provider blocked the streamed answer')
                finished = finished or bool(candidate.get('finishReason'))
            else:
                choice = (payload.get('choices') or [{}])[0]
                if choice.get('finish_reason') == 'content_filter':
                    raise ValueError('Provider blocked the streamed answer')
                delta = choice.get('delta', {}).get('content') or ''
                finished = finished or choice.get('finish_reason') is not None
            raw += delta
            if len(raw) > 16000:
                raise ValueError('Provider response exceeds stream limit')
            visible = preview(raw)
            if sink and visible != last_preview:
                sink('draft', {'text': visible})
                last_preview = visible
        if not finished:
            raise ValueError('Provider stream ended before completion')
        body = {'candidates': [{'content': {'parts': [{'text': raw}]}}]} if format == 'gemini' else {
            'choices': [{'message': {'content': raw}}]}
        return httpx.Response(response.status_code, request=response.request, json=body)


def generation_post(provider, url, **kwargs):
    if stream_sink.get() is not None:
        format = 'gemini' if provider.name == 'gemini' else 'compatible'
        if format == 'gemini':
            url = url.replace(':generateContent', ':streamGenerateContent') + '?alt=sse'
        else:
            kwargs['json'] = {**kwargs['json'], 'stream': True}
        kwargs['stream_format'] = format
    return provider._post(url, **kwargs)

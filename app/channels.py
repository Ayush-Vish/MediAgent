"""Verified webhook adapters; no unsolicited outbound messages.

Saved delivery state prevents normal retries from regenerating/resending replies.
A crash after provider acceptance but before marking sent can still duplicate a
delivery; provider APIs do not provide an exactly-once transaction with our DB.
"""
import hashlib
import hmac
from threading import RLock

import httpx

from app.service import session_key


def digest(settings, value):
    return hmac.new(settings.channel_hash_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def channel_text(answer):
    links = '\n'.join(f'[{s["id"]}] {s["title"]}: {s["url"]}' for s in answer['sources'])
    return (answer['text'] + ('\n\n' + links if links else '') + '\n\nIndependent demo. /review: request demo review; /status: read reply; /forget: clear chat.')[:3900]


class Channels:
    def __init__(self, settings, store, service):
        self.settings, self.store, self.service = settings, store, service
        self.delivery_lock = RLock()

    def process(self, channel, event_id, sender, message):
        identity = digest(self.settings, channel + ':' + sender)
        event_key = 'event:' + digest(self.settings, channel + ':' + event_id)
        with self.delivery_lock:
            event = self.store.get(event_key)
            if event and event['status'] == 'sent':
                return
            if not event:
                token = self.service.create_session(identity)
                if not self.store.consume('quota:channel:' + identity, 20, 60):
                    # Acknowledge excess updates without paying for an outbound reply.
                    self.store.put(event_key, 'event', {'status': 'sent', 'session': session_key(token)})
                    return
                if len(message) > 2000:
                    text = 'Please shorten your message to 2,000 characters. If this is urgent, seek emergency care instead of waiting for chat.'
                elif message.strip() == '/forget':
                    self.service.forget(token)
                    for old_event in self.store.list('event'):
                        if old_event.get('session') == session_key(token):
                            self.store.remove(old_event['id'])
                    text = 'Your conversation and demo review records have been cleared.'
                elif message.strip() == '/review':
                    try:
                        self.service.handoff(token)
                        text = 'A demo review was requested. This is not monitored by the hospital. Send /status to check for a reply.'
                    except LookupError:
                        text = 'Ask a question first, then send /review.'
                elif message.strip() == '/status':
                    reviews = self.service.history(token)['reviews']
                    text = '\n\n'.join(r['reply'] or 'Demo review pending.' for r in reviews) or 'No demo reviews requested.'
                else:
                    try:
                        text = channel_text(self.service.chat(token, message, event_key))
                    except OverflowError:
                        text = 'Please wait a minute before sending more questions.'
                text = text[:3900]
                event = {'status': 'pending', 'text': text, 'session': session_key(token)}
                self.store.put(event_key, 'event', event)
            self.send(channel, sender, event['text'])
            # Don't retain outbound content after successful delivery.
            self.store.put(event_key, 'event', {'status': 'sent', 'session': event['session']})

    def send(self, channel, recipient, text):
        if channel == 'telegram':
            url = f'https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage'
            response = httpx.post(url, json={'chat_id': recipient, 'text': text}, timeout=15)
            response.raise_for_status()
            if not response.json().get('ok'):
                raise RuntimeError('Telegram did not accept the reply')
        else:
            s = self.settings
            response = httpx.post(f'https://graph.facebook.com/{s.whatsapp_graph_version}/{s.whatsapp_phone_id}/messages',
                headers={'Authorization': 'Bearer ' + s.whatsapp_access_token}, timeout=15,
                json={'messaging_product': 'whatsapp', 'to': recipient, 'type': 'text', 'text': {'body': text}})
            response.raise_for_status()

    def telegram(self, update):
        message = update.get('message', {})
        chat = message.get('chat', {})
        if chat.get('type') != 'private' or not isinstance(message.get('text'), str):
            return
        if not isinstance(update.get('update_id'), int) or not isinstance(chat.get('id'), int):
            raise ValueError('Malformed Telegram update')
        self.process('telegram', str(update['update_id']), str(chat['id']), message['text'])

    def whatsapp(self, payload):
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                if value.get('metadata', {}).get('phone_number_id') != self.settings.whatsapp_phone_id:
                    continue
                for message in value.get('messages', []):
                    if message.get('type') != 'text':
                        continue
                    sender, event_id = message.get('from'), message.get('id')
                    body = message.get('text', {}).get('body')
                    if not all(isinstance(x, str) for x in (sender, event_id, body)):
                        raise ValueError('Malformed WhatsApp message')
                    self.process('whatsapp', event_id, sender, body)

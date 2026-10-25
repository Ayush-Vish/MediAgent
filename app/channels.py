"""Verified webhook adapters; no unsolicited outbound messages.

Saved delivery state prevents normal retries from regenerating/resending replies.
A crash after provider acceptance but before marking sent can still duplicate a
delivery; provider APIs do not provide an exactly-once transaction with our DB.
"""
import base64
import hashlib
import hmac
import os
import time
from threading import RLock

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.service import session_key


TELEGRAM_PRIVACY_NOTICE = (
    'Privacy notice: if you continue, your messages and MediAgent replies will be stored so authorised hospital staff '
    'can review the conversation and reply here. Telegram also processes messages under its own policy. Do not use '
    'this bot for emergencies or send unnecessary identifiers. Send /accept to consent, or /privacy to read this again.'
)


def digest(settings, value):
    return hmac.new(settings.channel_hash_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def channel_text(answer):
    links = '\n'.join(f'[{s["id"]}] {s["title"]}: {s["url"]}' for s in answer['sources'])
    commands = '\n\nIndependent demo. /review: request staff review; /status: read reply; /forget: delete chat.'
    return (answer['text'] + ('\n\n' + links if links else '') + commands)[:3900]


class Channels:
    def __init__(self, settings, store, service):
        self.settings, self.store, self.service = settings, store, service
        self.delivery_lock = RLock()

    def _recipient_cipher(self):
        # Domain separation lets one deployment secret safely derive independent
        # lookup (HMAC) and encryption keys. Rotating it invalidates old bindings.
        key = hashlib.sha256(b'mediagent-channel-recipient\0' + self.settings.channel_hash_secret.encode()).digest()
        return AESGCM(key)

    def _encrypt_recipient(self, channel, recipient):
        nonce = os.urandom(12)
        encrypted = self._recipient_cipher().encrypt(nonce, recipient.encode(), channel.encode())
        return base64.urlsafe_b64encode(nonce + encrypted).decode()

    def _decrypt_recipient(self, channel, encrypted):
        raw = base64.urlsafe_b64decode(encrypted.encode())
        return self._recipient_cipher().decrypt(raw[:12], raw[12:], channel.encode()).decode()

    def _telegram_binding_key(self, identity):
        return 'channel-binding:' + identity

    def _telegram_user(self, identity, recipient):
        binding_key = self._telegram_binding_key(identity)
        binding = self.store.get(binding_key)
        if binding:
            return self.store.get(binding['user_id']), binding

        user_id = 'user:telegram:' + identity
        user = {
            'id': user_id,
            'email': f'{identity[:24]}@telegram.invalid',
            'name': f'Telegram patient {identity[:6].upper()}',
            'phone': '', 'age': None, 'gender': '',
            'emergency_contact': '', 'emergency_phone': '',
            'created_at': time.time(), 'channel': 'telegram',
        }
        binding = {
            'channel': 'telegram', 'user_id': user_id,
            'recipient': self._encrypt_recipient('telegram', recipient),
            'created_at': time.time(),
        }
        self.store.put(user_id, 'user', user, permanent=True)
        self.store.put(binding_key, 'channel_binding', binding, permanent=True)
        self.store.put('channel-user:' + user_id, 'channel_user', {'binding_key': binding_key}, permanent=True)
        self.service.patient_session(user)
        return user, binding

    def _forget_telegram_user(self, identity, user):
        mapping_key = 'patient_session:' + user['id']
        mapping = self.store.get(mapping_key)
        conversation = session_key(mapping['token']) if mapping else None
        message_ids = {
            item['id'] for item in self.store.list('patient_message')
            if conversation and item.get('conversation_id') == conversation
        }
        if mapping:
            self.service.forget(mapping['token'], user=user)
        for symptom in self.store.list('symptom_event'):
            if symptom.get('user_id') == user['id']:
                self.store.remove(symptom['id'])
        for receipt in self.store.list('doctor_receipt'):
            if receipt.get('conversation_id') == conversation:
                self.store.remove(receipt['id'])
        for delivery in self.store.list('delivery'):
            if delivery.get('message_id') in message_ids:
                self.store.remove(delivery['id'])
        for channel_event in self.store.list('event'):
            if channel_event.get('session') == conversation:
                self.store.remove(channel_event['id'])
        self.store.remove(mapping_key)
        self.store.remove('channel-user:' + user['id'])
        self.store.remove(self._telegram_binding_key(identity))
        self.store.remove(user['id'])

    def _process_telegram(self, event_key, identity, sender, message):
        binding = self.store.get(self._telegram_binding_key(identity))
        user = self.store.get(binding['user_id']) if binding else None
        command = message.strip().lower()

        if command in {'/start', '/privacy'}:
            suffix = '\n\nYour private chat is already connected.' if user else ''
            return TELEGRAM_PRIVACY_NOTICE + suffix, None
        if not user:
            if command != '/accept':
                return 'Please review the privacy notice with /privacy, then send /accept before chatting.', None
            user, binding = self._telegram_user(identity, sender)
            return ('Consent recorded. You can now ask MediAgent a question. Authorised hospital staff can review '
                    'this chat and reply here. Send /forget at any time to delete the retained conversation.'), None

        token = self.service.patient_session(user)
        conversation = session_key(token)
        if command == '/forget':
            self._forget_telegram_user(identity, user)
            return 'Your retained Telegram conversation, patient alias, and symptom records have been deleted.', None
        if command == '/review':
            try:
                self.service.handoff(token)
                text = 'A staff review was requested. A doctor or authorised staff member can reply in this chat.'
                return text, conversation
            except LookupError:
                return 'Ask a question first, then send /review.', conversation
        if command == '/status':
            reviews = self.service.history(token, user=user)['reviews']
            text = '\n\n'.join(r['reply'] or 'Staff review pending.' for r in reviews) or 'No staff reviews requested.'
            return text, conversation
        try:
            answer = self.service.chat(token, message, event_key, user=user)
            return channel_text(answer), conversation
        except OverflowError:
            return 'Please wait a minute before sending more questions.', conversation

    def process(self, channel, event_id, sender, message):
        identity = digest(self.settings, channel + ':' + sender)
        event_key = 'event:' + digest(self.settings, channel + ':' + event_id)
        with self.delivery_lock:
            event = self.store.get(event_key)
            if event and event['status'] == 'sent':
                return
            if not event:
                if not self.store.consume('quota:channel:' + identity, 20, 60):
                    # Acknowledge excess updates without paying for an outbound reply.
                    self.store.put(event_key, 'event', {'status': 'sent', 'session': None})
                    return
                if len(message) > 2000:
                    text = 'Please shorten your message to 2,000 characters. If this is urgent, seek emergency care instead of waiting for chat.'
                    conversation = None
                elif channel == 'telegram':
                    text, conversation = self._process_telegram(event_key, identity, sender, message)
                else:
                    token = self.service.create_session(identity)
                    conversation = session_key(token)
                    if message.strip() == '/forget':
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
                event = {'status': 'pending', 'text': text, 'session': conversation}
                self.store.put(event_key, 'event', event)
            self.send(channel, sender, event['text'])
            # Don't retain outbound content after successful delivery.
            self.store.put(event_key, 'event', {'status': 'sent', 'session': event['session']})

    def deliver_staff_message(self, message):
        pointer = self.store.get('channel-user:' + message['user_id'])
        if not pointer:
            return False
        binding = self.store.get(pointer['binding_key'])
        if not binding:
            return False
        delivery_key = 'delivery:' + digest(self.settings, message['id'])
        with self.delivery_lock:
            delivery = self.store.get(delivery_key)
            if delivery and delivery.get('status') == 'sent':
                return True
            self.store.put(delivery_key, 'delivery', {'status': 'pending', 'message_id': message['id']})
            recipient = self._decrypt_recipient(binding['channel'], binding['recipient'])
            self.send(binding['channel'], recipient, 'Doctor / staff:\n' + message['text'])
            self.store.put(delivery_key, 'delivery', {'status': 'sent', 'message_id': message['id']})
            return True

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

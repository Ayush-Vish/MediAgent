from datetime import date

import pytest

from app.schemas import SourceInput


@pytest.mark.parametrize('query', ['I am getting vomit', 'vomiting', 'vomited', 'emesis'])
def test_vomiting_topic_beats_incidental_mentions(app, query):
    knowledge = app.state.knowledge
    for title, text, url in [
        ('Nausea and Vomiting', 'Vomiting is also called emesis. This page explains nausea and vomiting.', 'vomiting'),
        ('Nausea and Vomiting — chunk 2', 'Vomiting vomiting. More information about vomiting and nausea.', 'vomiting'),
        ('Blood Thinners', 'Vomit that is brown or bright red is mentioned among bleeding signs.', 'thinners'),
        ('Whooping Cough', 'Severe coughing may make a person vomit. This page describes whooping cough.', 'cough'),
    ]:
        knowledge.save(SourceInput(title=title, text=text, url=f'https://example.org/{url}',
                                   category='health', checked_at=date.today()), actor='test', approved=True)
    hits, _ = knowledge.search(query, 'health')
    assert hits[0]['title'] == 'Nausea and Vomiting'
    assert all(hit['url'] == 'https://example.org/vomiting' for hit in hits)


def test_blood_modifier_is_preserved(app):
    knowledge = app.state.knowledge
    for title in ['Vomiting', 'Blood in Vomit']:
        knowledge.save(SourceInput(title=title, text='Public educational information about blood and vomit.',
                                   url='https://example.org/info', category='health', checked_at=date.today()),
                       actor='test', approved=True)
    hits, _ = knowledge.search('blood in vomit', 'health')
    assert hits[0]['title'] == 'Blood in Vomit'


def test_hybrid_can_add_semantic_only_approved_hits(app):
    knowledge = app.state.knowledge
    key = knowledge.save(SourceInput(title='Nausea overview', text='Educational overview of nausea and vomiting.',
                                     url='https://example.org/nausea', category='health', checked_at=date.today()),
                         actor='test', approved=True)
    class Index:
        def search(self, query, category):
            return ['source:withdrawn', key]
    knowledge._hybrid = Index()
    knowledge.settings.hybrid_enabled = True
    hits, mode = knowledge.search('queasy', 'health')
    assert mode == 'hybrid'
    assert [hit['id'] for hit in hits] == [key]

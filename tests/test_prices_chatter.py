"""Old config.yaml price lists get new models; model chatter is not shown as a translation."""

from subwizard.config import TranslatorConfig
from subwizard.translator.prompt import is_chatter


def test_old_price_list_gets_new_models():
    # config.yaml written before Claude was added: only Gemini prices.
    tr = TranslatorConfig(prices={"gemini-3.5-flash-lite": {"input": 0.5, "output": 3.0}})
    assert tr.prices["gemini-3.5-flash-lite"].input == 0.5  # the user's value wins
    assert tr.prices["claude-haiku-5-5"].output == 0.50


def test_chatter_detected():
    reply = ('Bu satır "YhG" yazısından ibaret olduğu için bir anlam taşımıyor, çevrilecek bir '
             "cümle yok. Eğer bu bir tepki, kısaltma ya da karakter adıysa, orijinal metni ya "
             "da bağlamını paylaşırsan çeviriyi yapabilirim.")
    assert is_chatter("YhG", reply)


def test_long_real_translation_not_chatter():
    source = 'Relax, homie. You\'re in no position to say "wow" to anybody.'
    reply = 'Sakin ol, kanka. Şu an kimseye "vay be" deyip laf sokacak durumda değilsin.'
    assert not is_chatter(source, reply)
    assert not is_chatter("Okay.", "Tamam.")

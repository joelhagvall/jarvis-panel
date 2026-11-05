#!/usr/bin/env python3
"""Sentence splitting and stream assembly for the streamed voice (ha/custom_components/jarvis_agent/speech.py).
Run: python3 -m unittest tests/test_speech.py"""
import asyncio
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "speech", Path(__file__).resolve().parent.parent / "ha" / "custom_components" / "jarvis_agent" / "speech.py")
speech = importlib.util.module_from_spec(spec)
spec.loader.exec_module(speech)


def split(text, size):
    """Feed text in pieces of `size` characters, as the agent's stream does."""
    async def chunks():
        for i in range(0, len(text), size):
            yield text[i:i + size]

    async def collect():
        return [s async for s in speech.sentences(chunks())]
    return asyncio.run(collect())


class SentenceTest(unittest.TestCase):
    def test_splits_on_sentence_ends_in_any_piece_size(self):
        text = "Klart! Köp kvarg ligger i Att göra, i dag kl. 17. I morgon har du standup halv tio."
        want = ["Klart!", "Köp kvarg ligger i Att göra, i dag kl. 17.", "I morgon har du standup halv tio."]
        for size in (1, 3, 7, 200):
            self.assertEqual(split(text, size), want, size)

    def test_abbreviations_and_times_do_not_split(self):
        text = "Imorgon har du kl. 10.20 en påminnelse, t.ex. tvätten. Sedan gym kl. 15.00."
        self.assertEqual(split(text, 2), ["Imorgon har du kl. 10.20 en påminnelse, t.ex. tvätten.",
                                          "Sedan gym kl. 15.00."])

    def test_lowercase_or_number_after_period_is_not_a_new_sentence(self):
        self.assertEqual(split("Det är ca. fem grader. 15 minuter kvar.", 4), ["Det är ca. fem grader. 15 minuter kvar."])

    def test_first_sentence_marker_waits_for_swedish_and_english_abbreviations(self):
        for text in ("Vi ses kl. ", "Talk to Dr.", "For example, e.g.", "Klockan är 10."):
            self.assertFalse(speech.first_sentence_ready(text),text)
        for text in ("Det är klart.", "It is ready. ", "Vill du veta mer?", "First. Second"):
            self.assertTrue(speech.first_sentence_ready(text),text)

    def test_line_break_ends_a_sentence_at_once(self):
        """The sentence Jarvis says before its tools is spoken while they run, not when the answer comes."""
        async def run():
            heard = asyncio.Event()

            async def chunks():
                yield "Jag kollar flyget"
                yield " och vädret.\n"
                await asyncio.wait_for(heard.wait(), 1)
                yield "Klart. Planet är"
                yield " ett SAS-plan."
            out = []
            async for sentence in speech.sentences(chunks()):
                out.append(sentence)
                heard.set()
            return out
        self.assertEqual(asyncio.run(run()), ["Jag kollar flyget och vädret.", "Klart.", "Planet är ett SAS-plan."])
        self.assertEqual(split("Jag kollar\nKlart.", 3), ["Jag kollar", "Klart."])   # without a period too

    def test_a_complete_sentence_starts_during_a_model_pause(self):
        async def run():
            started = asyncio.Event()
            async def chunks():
                yield "Första meningen. "
                await asyncio.wait_for(started.wait(), 1)
                yield "Andra meningen."
            out = []
            async for sentence in speech.sentences(chunks()):
                out.append(sentence)
                started.set()
            return out
        self.assertEqual(asyncio.run(run()), ["Första meningen.", "Andra meningen."])

    def test_abbreviations_and_numeric_tails_do_not_flush_on_idle(self):
        async def run(prefix, suffix):
            async def chunks():
                yield prefix
                await asyncio.sleep(speech.SENTENCE_IDLE_S * 1.5)
                yield suffix
            return [s async for s in speech.sentences(chunks())]
        for prefix, suffix in (("Vi ses kl. ", "10.20."), ("Klockan är 10.", "20.")):
            self.assertEqual(asyncio.run(run(prefix, suffix)), [(prefix + suffix).strip()])

    def test_cancelling_a_stream_closes_its_pending_upstream_read(self):
        async def run():
            closed = asyncio.Event()
            async def chunks():
                try:
                    yield "Första meningen. "
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            generator = speech.sentences(chunks())
            self.assertEqual(await anext(generator), "Första meningen.")
            await generator.aclose()
            self.assertTrue(closed.is_set())
        asyncio.run(run())

    def test_silence_and_header(self):
        fmt = (16000, 1, 2)
        self.assertEqual(len(speech.silence(fmt)), int(16000 * speech.PAUSE_S) * 2)
        self.assertEqual(speech.stream_header(fmt)[:4], b"RIFF")


class ClauseTest(unittest.TestCase):
    """The first sentence's opening clause spoken on its own (FIRST_CLAUSE_WORDS), off unless Joel turns it on."""
    TEXT = "På fredag har du två saker, standup klockan halv tio och lunch med Anna. Sedan är du ledig, tror jag."

    def test_off_by_default(self):
        self.assertEqual(speech.FIRST_CLAUSE_WORDS, 0)
        self.assertEqual(split(self.TEXT, 5), ["På fredag har du två saker, standup klockan halv tio och lunch med Anna.",
                                               "Sedan är du ledig, tror jag."])

    def test_first_clause_is_spoken_early_when_on(self):
        with patch.object(speech, "FIRST_CLAUSE_WORDS", 4):
            for size in (1, 7, 300):
                self.assertEqual(split(self.TEXT, size), ["På fredag har du två saker,",
                                                          "standup klockan halv tio och lunch med Anna.",
                                                          "Sedan är du ledig, tror jag."], size)
            # too few words before the comma, or a sentence end in the text already: no clause of its own
            self.assertEqual(split("Klart, det är fixat. Nästa, mening här.", 3), ["Klart, det är fixat.", "Nästa, mening här."])
            self.assertEqual(split("Du har möten kl. 10, 11 och 12 på fredag. Sedan lunch.", 3),
                             ["Du har möten kl. 10, 11 och 12 på fredag.", "Sedan lunch."])


class StreamTest(unittest.TestCase):
    """speech.WavStream: the header, the pauses, and the silence that lets the panel start a short first sentence."""
    FMT = (16000, 1, 2)

    def test_short_first_sentence_is_padded_and_the_pause_after_it_shrinks(self):
        stream = speech.WavStream(prime_s=1.0)
        first = b"\x01\x00" * 8000                                              # 0.5 s
        chunks = stream.sentence(self.FMT, first, "Klart!")
        self.assertEqual(chunks[0][:4], b"RIFF")
        self.assertEqual(chunks[1], first)
        self.assertEqual(len(chunks[2]), 16000)                                 # 0.5 s of silence: the panel starts at 1 s
        second = b"\x02\x00" * 16000
        self.assertEqual(stream.sentence(self.FMT, second, "Nästa mening."), [second])      # the pad covered the pause
        third = b"\x03\x00" * 16000
        self.assertEqual(stream.sentence(self.FMT, third, "Och en till."), [speech.silence(self.FMT), third])

    def test_a_first_sentence_of_a_second_or_more_is_not_padded(self):
        stream = speech.WavStream(prime_s=1.0)
        first = b"\x01\x00" * 24000
        self.assertEqual(stream.sentence(self.FMT, first, "På fredag har du två saker."), [speech.stream_header(self.FMT), first])

    def test_a_pad_shorter_than_the_pause_leaves_the_rest_of_it(self):
        stream = speech.WavStream(prime_s=1.0)
        stream.sentence(self.FMT, b"\x01\x00" * 15000, "God morgon!")          # 0.9375 s, padded by 0.0625 s
        chunks = stream.sentence(self.FMT, b"\x02\x00" * 100, "Idag har du en sak.")
        self.assertEqual(len(chunks[0]), int(16000 * (speech.PAUSE_S - 0.0625)) * 2)

    def test_padding_can_be_turned_off(self):
        stream = speech.WavStream(prime_s=0)
        self.assertEqual(len(stream.sentence(self.FMT, b"\x01\x00" * 800, "Ja.")), 2)

    def test_a_clause_gets_a_shorter_pause(self):
        stream = speech.WavStream(prime_s=0)
        stream.sentence(self.FMT, b"\x01\x00" * 16000, "På fredag har du två saker,")
        chunks = stream.sentence(self.FMT, b"\x02\x00" * 16000, "standup och lunch.")
        self.assertEqual(len(chunks[0]), int(16000 * speech.CLAUSE_PAUSE_S) * 2)

    def test_top_up_fills_the_panels_two_reads_once_and_shortens_the_pause(self):
        # 2026-10-30: HA's ffmpeg holds a stream until it has 64 KiB, and the panel then needs 48 KiB to start.
        stream = speech.WavStream(prime_s=1.0)
        stream.sentence(self.FMT, b"\x01\x00" * 16000, "Jag mår bra, tack.")         # header + 1 s
        pad = stream.top_up()
        self.assertEqual(len(pad), speech.PANEL_START_BYTES - 44 - 32000)
        self.assertEqual(stream.sent, speech.PANEL_START_BYTES)
        self.assertIsNone(stream.top_up())                                       # once
        second = b"\x02\x00" * 1600
        self.assertEqual(stream.sentence(self.FMT, second, "Och du?"), [second])  # the top-up covered the pause
        long_first = speech.WavStream(prime_s=1.0)
        long_first.sentence(self.FMT, b"\x01\x00" * 36000, "En lång första mening här.")
        self.assertIsNone(long_first.top_up())                                   # 2.25 s gets through by itself

    def test_flush_pushes_a_sentence_end_out_while_nothing_follows(self):
        # Joel, 2026-11-01: "Självklart, jag tittar på tvättstugan." stopped at "tvättstu" while the laundry site
        # worked; its end waited in ffmpeg and the panel's 24 KiB read until more bytes came.
        stream = speech.WavStream()
        self.assertIsNone(stream.flush())                                        # nothing said yet
        stream.sentence(self.FMT, b"\x01\x00" * 37000, "Självklart, jag tittar på tvättstugan.")
        pad = stream.flush()
        self.assertGreaterEqual(len(pad), speech.FLUSH_BYTES)
        self.assertEqual(set(pad), {0})
        self.assertIsNone(stream.flush())                                        # once per sentence
        answer = b"\x02\x00" * 1600
        self.assertEqual(stream.sentence(self.FMT, answer, "Klart."), [answer])  # the flush was the pause
        self.assertIsNotNone(stream.flush())                                     # and again after the next one

    def test_keepalive_and_end(self):
        stream = speech.WavStream()
        self.assertIsNone(stream.keepalive())
        self.assertEqual(stream.end()[:4], b"RIFF")
        stream.sentence(self.FMT, b"\x01\x00" * 32000, "Hej.")
        self.assertEqual(len(stream.keepalive()), int(16000 * 0.05) * 2)
        self.assertIsNone(stream.end())


if __name__ == "__main__":
    unittest.main()

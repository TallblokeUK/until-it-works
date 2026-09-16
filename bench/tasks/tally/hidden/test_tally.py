"""The hidden tests for the tally task: every rule the task states, nothing it does not.

The agents never see this file. It runs against the finished branch, so it must import
the work rather than assume anything about how it is organised inside.
"""
import unittest

import tally


class CountWords(unittest.TestCase):
    def test_counts_are_case_insensitive_and_lower_case(self):
        self.assertEqual(tally.count_words("The the THE cat"), {"the": 3, "cat": 1})

    def test_punctuation_separates_words(self):
        self.assertEqual(tally.count_words("one,two;three.four\nfive\tsix"),
                         {"one": 1, "two": 1, "three": 1, "four": 1, "five": 1, "six": 1})

    def test_apostrophes_and_hyphens_hold_a_word_together(self):
        self.assertEqual(tally.count_words("don't stop the well-known song"),
                         {"don't": 1, "stop": 1, "the": 1, "well-known": 1, "song": 1})

    def test_curly_apostrophes_are_the_same_word_as_straight_ones(self):
        self.assertEqual(tally.count_words("don’t don't"), {"don't": 2})

    def test_edges_are_trimmed(self):
        self.assertEqual(tally.count_words("'hello' well- -up ''"), {"hello": 1, "well": 1, "up": 1})

    def test_digits_count_as_words(self):
        self.assertEqual(tally.count_words("python3 42 3-4"), {"python3": 1, "42": 1, "3-4": 1})

    def test_nothing_at_all(self):
        self.assertEqual(tally.count_words(""), {})
        self.assertEqual(tally.count_words("--- ''' ... "), {})


class TopWords(unittest.TestCase):
    def test_most_common_first(self):
        self.assertEqual(tally.top_words("a a a b b c", 2), [("a", 3), ("b", 2)])

    def test_ties_are_alphabetical(self):
        self.assertEqual(tally.top_words("pear apple pear apple fig fig", 3),
                         [("apple", 2), ("fig", 2), ("pear", 2)])

    def test_ties_are_alphabetical_even_when_cut_short(self):
        self.assertEqual(tally.top_words("zebra apple mango", 1), [("apple", 1)])

    def test_n_bigger_than_the_text(self):
        self.assertEqual(tally.top_words("one two", 10), [("one", 1), ("two", 1)])

    def test_n_of_zero_or_less(self):
        self.assertEqual(tally.top_words("one two", 0), [])
        self.assertEqual(tally.top_words("one two", -3), [])

    def test_empty_text(self):
        self.assertEqual(tally.top_words("", 5), [])
        self.assertEqual(tally.top_words("!!! ---", 5), [])

    def test_a_longer_piece_of_text(self):
        text = ("The quick brown fox jumps over the lazy dog. The DOG doesn't mind; the dog's "
                "well-known patience is well-known indeed.")
        self.assertEqual(tally.top_words(text, 4), [("the", 4), ("dog", 2), ("well-known", 2), ("brown", 1)])
        self.assertEqual(tally.count_words(text)["dog's"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=1)

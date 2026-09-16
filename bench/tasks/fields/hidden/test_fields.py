"""The hidden tests for the fields task: every rule the task states, nothing it does not.

The agents never see this file. Each test names the rule it comes from, so a failure says
which rule was misread rather than only that something is wrong.
"""
import unittest

from fields import read_rows


class TheEasyPart(unittest.TestCase):
    def test_plain_rows(self):
        self.assertEqual(read_rows("a,b,c\nd,e,f"), [["a", "b", "c"], ["d", "e", "f"]])

    def test_empty_fields_are_kept(self):
        self.assertEqual(read_rows("a,,b"), [["a", "", "b"]])
        self.assertEqual(read_rows(",a,"), [["", "a", ""]])

    def test_empty_text(self):
        self.assertEqual(read_rows(""), [])

    def test_rows_may_be_ragged(self):
        self.assertEqual(read_rows("a,b\nc\nd,e,f"), [["a", "b"], ["c"], ["d", "e", "f"]])


class LineBreaks(unittest.TestCase):
    def test_all_three_kinds(self):
        for ending in ("\n", "\r\n", "\r"):
            self.assertEqual(read_rows(f"a,b{ending}c,d"), [["a", "b"], ["c", "d"]], repr(ending))

    def test_a_trailing_break_does_not_add_a_row(self):
        for ending in ("\n", "\r\n", "\r"):
            self.assertEqual(read_rows(f"a,b{ending}"), [["a", "b"]], repr(ending))

    def test_a_blank_line_is_a_row_with_one_empty_field(self):
        self.assertEqual(read_rows("a\n\nb"), [["a"], [""], ["b"]])


class Quotes(unittest.TestCase):
    def test_quotes_are_not_part_of_the_value(self):
        self.assertEqual(read_rows('"a","b"'), [["a", "b"]])

    def test_commas_inside_quotes_are_not_separators(self):
        self.assertEqual(read_rows('"a,b",c'), [["a,b", "c"]])

    def test_line_breaks_inside_quotes_stay_in_the_value(self):
        self.assertEqual(read_rows('"one\ntwo",x'), [["one\ntwo", "x"]])

    def test_windows_and_old_mac_breaks_inside_quotes_become_one_newline(self):
        self.assertEqual(read_rows('"one\r\ntwo"'), [["one\ntwo"]])
        self.assertEqual(read_rows('"one\rtwo"'), [["one\ntwo"]])

    def test_two_quotes_mean_one(self):
        self.assertEqual(read_rows('"she said ""hi"""'), [['she said "hi"']])
        self.assertEqual(read_rows('"""",x'), [['"', "x"]])

    def test_a_quote_only_opens_a_field_at_its_start(self):
        self.assertEqual(read_rows('a,b"c,d'), [["a", 'b"c', "d"]])
        self.assertEqual(read_rows('a, "b",c'), [["a", ' "b"', "c"]])

    def test_text_after_a_closing_quote_is_kept(self):
        self.assertEqual(read_rows('"ab"cd,e'), [["abcd", "e"]])

    def test_a_quote_that_is_never_closed_runs_to_the_end(self):
        self.assertEqual(read_rows('a,"b,c\nd'), [["a", "b,c\nd"]])

    def test_spaces_are_kept(self):
        self.assertEqual(read_rows(" a , b "), [[" a ", " b "]])
        self.assertEqual(read_rows('" a ",b'), [[" a ", "b"]])

    def test_an_empty_quoted_field(self):
        self.assertEqual(read_rows('"",a'), [["", "a"]])


class AllTogether(unittest.TestCase):
    def test_a_file_a_spreadsheet_might_produce(self):
        text = ('name,note,amount\r\n'
                '"Smith, John","said ""thanks""",10.50\r\n'
                'Jones,"two\r\nlines",0\r\n'
                ',,\r\n')
        self.assertEqual(read_rows(text), [
            ["name", "note", "amount"],
            ["Smith, John", 'said "thanks"', "10.50"],
            ["Jones", "two\nlines", "0"],
            ["", "", ""],
        ])

    def test_reading_twice_gives_the_same_answer(self):
        text = 'a,"b\nc",d\n"e""f",g\n'
        self.assertEqual(read_rows(text), read_rows(text))


if __name__ == "__main__":
    unittest.main(verbosity=1)

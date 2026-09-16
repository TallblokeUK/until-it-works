Create fields.py in the project root, with one function: read_rows(text).

It reads a whole file of comma-separated rows and returns a list of rows, where each row
is a list of strings. It is the reading half of the format a spreadsheet exports, and the
rules that make it awkward are the point of the task.

The rules:

- A row ends at a line break. A line break is \n, \r\n, or \r on its own, and all three
  behave the same. The text may or may not end with one; either way the last row is
  returned once, and a trailing line break does not add an empty row at the end.
- A field is the text between commas. Empty fields are kept: "a,,b" is three fields, and
  the middle one is the empty string.
- A blank line is a row with one empty field.
- A field may be wrapped in double quotes. The quotes themselves are not part of the value.
- Inside quotes, commas and line breaks are part of the value, not separators. A line break
  inside quotes keeps its own characters as they are, except that \r\n and \r both become
  a single \n in the value.
- Inside quotes, two double quotes in a row mean one double quote in the value: "she said
  ""hi""" is the value: she said "hi"
- Spaces are kept exactly as they are, on both sides of a field. A quote only opens a
  quoted field when it is the first character of the field; a quote later in an unquoted
  field is an ordinary character, so a,b"c,d is three fields and the middle one is b"c
- Text after a closing quote, before the next comma or line break, is kept as it is:
  "ab"cd is the value: abcd
- A quoted field that is never closed runs to the end of the text, and what it has so far
  is its value.
- Text that is completely empty gives an empty list of rows.

Rows may have different numbers of fields; that is not an error, and nothing is padded.

Write it in plain Python 3 with nothing installed beyond the standard library, and do not
use the csv module: the point is the reading itself. Add your own tests; the project has
none yet.

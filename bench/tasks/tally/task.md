Create tally.py in the project root, with two functions and nothing else that the project needs.

count_words(text) returns a dictionary of every word in text and how many times it appears.

top_words(text, n) returns a list of the n most common words, as (word, count) pairs, most common first.

What counts as a word, for both functions:

- Words are separated by anything that is not a letter, a digit, an apostrophe or a hyphen.
- A word is compared without case: "The" and "the" are the same word, and the word in the
  result is always lower case.
- An apostrophe inside a word keeps the word together: "don't" is one word. The straight
  apostrophe ' and the curly one ’ both count, and the word in the result always uses the
  straight one, so "don’t" and "don't" are the same word.
- A hyphen inside a word keeps the word together: "well-known" is one word.
- Apostrophes and hyphens at the start or end of a word are not part of it: "'hello'" is
  hello, and "well-" is well.
- Digits are part of a word: "python3" is one word, and "42" is a word on its own.
- Anything left with no letters or digits in it is not a word at all.

What top_words must do beyond that:

- When two words appear the same number of times, the one that comes first alphabetically
  comes first in the list.
- If n is more than the number of different words, return all of them.
- If n is 0 or less, return an empty list.
- text that is empty, or has no words in it, gives an empty list (and count_words gives an
  empty dictionary).

Write it in plain Python 3 with nothing installed beyond the standard library. Add your own
tests; the project has none yet.

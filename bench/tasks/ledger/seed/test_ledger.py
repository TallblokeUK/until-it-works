import unittest

from ledger import Invoice, apply_payment, statement


def invoices():
    return [Invoice("INV-1", 100.00, "2026-01-05"), Invoice("INV-2", 50.00, "2026-02-01")]


class Payments(unittest.TestCase):
    def test_a_payment_settles_the_oldest_invoice(self):
        items = invoices()
        left = apply_payment(items, 100.00)
        self.assertEqual(left, 0)
        self.assertTrue(items[0].settled)
        self.assertFalse(items[1].settled)

    def test_paying_everything_leaves_nothing(self):
        items = invoices()
        self.assertEqual(apply_payment(items, 150.00), 0)
        self.assertTrue(all(i.settled for i in items))

    def test_more_than_everything_leaves_a_credit(self):
        items = invoices()
        self.assertEqual(apply_payment(items, 200.00), 50.00)

    def test_statement_shows_what_is_left(self):
        items = invoices()
        apply_payment(items, 100.00)
        self.assertIn("total outstanding: 50.00", statement(items))


if __name__ == "__main__":
    unittest.main()

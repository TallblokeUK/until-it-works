"""The hidden tests for the ledger task: every rule the task states, nothing it does not.

The agents never see this file. It uses the module the way anything else would: invoices,
apply_payment and statement, with money read as pounds and pence.
"""
import io
import unittest

from ledger import Invoice, apply_payment, statement


def money(value):
    """Pounds as a whole number of pennies, so nothing here depends on how they store it."""
    return int(round(float(value) * 100))


class PartPayments(unittest.TestCase):
    def test_the_complaint_in_the_task(self):
        invoice = Invoice("INV-1", 100.00, "2026-01-05")
        left = apply_payment([invoice], 30.00)
        self.assertEqual(money(left), 0)
        self.assertEqual(money(invoice.outstanding), money(70.00))
        self.assertFalse(invoice.settled)

    def test_a_payment_spanning_invoices(self):
        a = Invoice("INV-1", 100.00, "2026-01-05")
        b = Invoice("INV-2", 50.00, "2026-02-01")
        left = apply_payment([a, b], 120.00)
        self.assertEqual(money(left), 0)
        self.assertTrue(a.settled)
        self.assertEqual(money(b.outstanding), money(30.00))

    def test_oldest_first_and_the_number_breaks_a_tie(self):
        first = Invoice("INV-1", 10.00, "2026-03-01")
        same_day = Invoice("INV-2", 10.00, "2026-03-01")
        later = Invoice("INV-3", 10.00, "2026-04-01")
        apply_payment([later, same_day, first], 15.00)
        self.assertTrue(first.settled)
        self.assertEqual(money(same_day.outstanding), money(5.00))
        self.assertEqual(money(later.outstanding), money(10.00))

    def test_nothing_is_lost_or_invented(self):
        items = [Invoice("INV-1", 33.33, "2026-01-01"), Invoice("INV-2", 66.67, "2026-01-02"),
                 Invoice("INV-3", 0.01, "2026-01-03")]
        for payment in (0.01, 7.77, 33.33, 99.99, 100.00, 250.00):
            fresh = [Invoice(i.number, i.amount, i.issued) for i in items]
            left = apply_payment(fresh, payment)
            taken = sum(money(i.paid) for i in fresh)
            self.assertEqual(taken + money(left), money(payment), f"payment of {payment}")
            self.assertTrue(all(money(i.paid) <= money(i.amount) for i in fresh), f"payment of {payment}")

    def test_pennies_do_not_drift(self):
        items = [Invoice(f"INV-{n}", 0.10, f"2026-01-0{n}") for n in (1, 2, 3)]
        left = apply_payment(items, 0.30)
        self.assertEqual(money(left), 0)
        self.assertTrue(all(i.settled for i in items), [i.outstanding for i in items])
        self.assertEqual(money(sum(i.paid for i in items)), 30)

    def test_a_long_run_of_small_payments_settles_exactly(self):
        invoice = Invoice("INV-1", 1.00, "2026-01-01")
        for _ in range(10):
            self.assertEqual(money(apply_payment([invoice], 0.10)), 0)
        self.assertTrue(invoice.settled)
        self.assertEqual(money(invoice.outstanding), 0)

    def test_a_payment_of_zero_or_less_changes_nothing(self):
        invoice = Invoice("INV-1", 100.00, "2026-01-05")
        self.assertEqual(money(apply_payment([invoice], 0)), 0)
        self.assertEqual(money(apply_payment([invoice], -25.00)), 0)
        self.assertEqual(money(invoice.outstanding), money(100.00))

    def test_credit_comes_back_only_once_everything_is_settled(self):
        items = [Invoice("INV-1", 20.00, "2026-01-01"), Invoice("INV-2", 30.00, "2026-01-02")]
        self.assertEqual(money(apply_payment(items, 75.50)), money(25.50))
        self.assertTrue(all(i.settled for i in items))


class TheRestStillWorks(unittest.TestCase):
    def test_statement_reads_as_pounds_and_pence(self):
        items = [Invoice("INV-1", 100.00, "2026-01-05"), Invoice("INV-2", 50.00, "2026-02-01")]
        apply_payment(items, 30.00)
        text = statement(items)
        self.assertIn("total outstanding: 120.00", text)
        self.assertIn("INV-1", text.splitlines()[0])

    def test_the_seed_tests_are_still_there_and_still_pass(self):
        import test_ledger                                    # the tests the task said to keep passing
        suite = unittest.defaultTestLoader.loadTestsFromModule(test_ledger)
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        self.assertEqual(suite.countTestCases(), 4, "the original tests were removed or rewritten")
        self.assertTrue(result.wasSuccessful())


if __name__ == "__main__":
    unittest.main(verbosity=1)

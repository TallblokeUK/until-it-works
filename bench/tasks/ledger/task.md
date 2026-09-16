Customers are complaining about their statements after a part payment.

A customer with one invoice of £100 paid £30 off it. The statement then showed £85 still
owing, and the payment was recorded as leaving £15 of credit. Both figures are wrong: the
invoice should show £70 owing and no credit at all.

Fix apply_payment in ledger.py so that:

- A payment is put towards the invoices oldest first (an invoice's own number breaks a tie
  between two issued on the same day), and each invoice takes as much of the payment as it
  can until it is settled.
- Only what is left after every invoice is settled comes back as credit. Money never
  appears or disappears: what the invoices take, plus the credit returned, always adds up
  to exactly what was paid.
- Amounts are money, so they are exact to the penny. Paying three invoices of £0.10 with
  £0.30 settles all three and leaves nothing, with no fractions of a penny anywhere.
- A payment of zero or less changes nothing and returns 0.

Keep the existing tests in test_ledger.py passing, and keep how the rest of the file is
used from outside the same: Invoice.amount, Invoice.paid, Invoice.outstanding,
Invoice.settled and statement() all still work the way they do now, and outstanding and
the statement still read as pounds and pence.

Plain Python 3, nothing installed beyond the standard library.

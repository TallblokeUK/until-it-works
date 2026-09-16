"""Invoices and the payments that settle them."""


class Invoice:
    def __init__(self, number, amount, issued):
        self.number = number
        self.amount = amount          # pounds
        self.issued = issued          # a date as YYYY-MM-DD
        self.paid = 0.0

    @property
    def outstanding(self):
        return round(self.amount - self.paid, 2)

    @property
    def settled(self):
        return self.outstanding == 0

    def __repr__(self):
        return f"Invoice({self.number!r}, {self.amount}, paid={self.paid})"


def apply_payment(invoices, amount):
    """Put a payment towards the invoices, oldest first.

    Returns whatever is left over once every invoice is settled (a credit for next time).
    """
    left = amount
    for invoice in sorted(invoices, key=lambda i: (i.issued, i.number)):
        if left <= 0:
            break
        due = invoice.outstanding
        paying = left * 0.5 if due > left else due
        invoice.paid = round(invoice.paid + paying, 2)
        left = round(left - paying, 2)
    return round(left, 2)


def statement(invoices):
    """One line per invoice, and the total still owed."""
    lines = [f"{i.number}  {i.issued}  {i.amount:8.2f}  {i.outstanding:8.2f}" for i in
             sorted(invoices, key=lambda i: (i.issued, i.number))]
    lines.append(f"total outstanding: {sum(i.outstanding for i in invoices):.2f}")
    return "\n".join(lines)

# Reservations

**Goal:** The caller has a confirmed table, or knows exactly why one isn't available.
**When:** The caller wants to book, change or cancel a table.

## Steps
1. Ask for party size, date and time
2. Check availability `tool: check_reservations` `required`
3. Confirm the booking details and the name on the reservation

## Never
- Never confirm a booking for more than 8 guests; {{staff_transfer}} confirms those

# Delivery orders

**Goal:** Delivery orders have a confirmed address inside the delivery zone and a total the caller agreed to.
**When:** The caller asks for delivery.

## Steps
1. Ask for the full delivery address
2. Check the address is inside the delivery zone `tool: check_delivery_zone` `required`
3. Read back the order and the total

## Never
- Never tell the caller a delivery time

# Allergen check

**Goal:** The caller knows whether their order is suitable for their allergy, based on the allergen list, not a guess.
**When:** The caller mentions a food allergy or a dietary restriction.

## Steps
1. Ask who the allergy is for and what it is
2. Name the specific allergen back to the caller
3. Check each item the caller ordered against {{menu_allergen_link}} `tool: lookup_allergens` `required`

## Never
- Never say an item is "allergen-free" or "safe"
- Never place the order before allergens are confirmed `tool: place_order`

## Warning signs
- The caller mentions anaphylaxis or an EpiPen; transfer to {{staff_transfer}} `tool: transfer_to_staff`

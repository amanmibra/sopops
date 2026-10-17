Several agents shared the same text except for a few values, so the text now has slots, one per place where the agents differ. Name each slot as a sopc variable: snake_case, lowercase letters, digits and underscores, starting with a letter, saying what the value is, not what it is now: `restaurant_name`, `menu_link`, `staff_transfer`, `upsell_limit`, `opening_hours`. The same kind of value in two slots gets the same name only if every agent has the same value in both.

Return one name per slot, in the order given.

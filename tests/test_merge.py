"""The three-way line merge (`sopops.merge`): edits on different lines merge, neighbouring lines included; the same
lines changed two ways, or two insertions at one place, conflict; deletions; a change made the same way on both sides;
and `contains`, which finds an edit in a file that has others too. The merged file not compiling is in test_review."""

from sopops.merge import Conflict, contains, merge

BASE = "name: Allergen check\nsteps:\n  - Ask about allergies\n  - Name the allergen back\n  - Check each item\nnever:\n  - Never say safe\n"


def edit(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new)


def test_edits_to_different_lines_merge():
    ours = edit(BASE, "Ask about allergies", "Ask who has the allergy")
    theirs = edit(BASE, "Never say safe", "Never say safe or allergen-free")
    got = merge(BASE, ours, theirs)
    assert got.clean and got.text == "name: Allergen check\nsteps:\n  - Ask who has the allergy\n  - Name the allergen back\n  - Check each item\nnever:\n  - Never say safe or allergen-free\n"


def test_edits_to_neighbouring_lines_merge():
    ours = edit(BASE, "Name the allergen back", "Name the allergen back, slowly")
    theirs = edit(BASE, "Check each item", "Check each item with the lookup")
    got = merge(BASE, ours, theirs)
    assert got.clean and "  - Name the allergen back, slowly\n  - Check each item with the lookup\n" in got.text
    # an added line right after a line the other side changed goes after it
    theirs = edit(BASE, "  - Check each item\n", "  - Check each item\n  - Read the result back\n")
    ours = edit(BASE, "Check each item", "Check every item")
    got = merge(BASE, ours, theirs)
    assert got.clean and "  - Check every item\n  - Read the result back\nnever:" in got.text


def test_the_same_line_changed_two_ways_conflicts():
    ours = edit(BASE, "Never say safe", "Never say safe, ever")
    theirs = edit(BASE, "Never say safe", "Never say safe unless checked")
    got = merge(BASE, ours, theirs)
    assert not got.clean and got.text is None
    assert got.conflicts == [Conflict(line=7, base="  - Never say safe\n", ours="  - Never say safe, ever\n", theirs="  - Never say safe unless checked\n")]


def test_overlapping_ranges_conflict_and_line_numbers_are_in_the_current_file():
    ours = edit(BASE, "name: Allergen check\n", "name: Allergen check\ndescription: Safe orders\n")  # one line more above
    ours = edit(ours, "  - Name the allergen back\n  - Check each item\n", "  - Name it back and check each item\n")
    theirs = edit(BASE, "  - Check each item\n", "  - Check each item against the list\n")
    got = merge(BASE, ours, theirs)
    (c,) = got.conflicts
    assert c.line == 5 and c.base == "  - Name the allergen back\n  - Check each item\n" and c.ours == "  - Name it back and check each item\n"
    assert c.theirs == "  - Name the allergen back\n  - Check each item against the list\n"


def test_two_insertions_at_one_place_conflict():
    ours = edit(BASE, "steps:\n", "steps:\n  - Greet the caller\n")
    theirs = edit(BASE, "steps:\n", "steps:\n  - Ask for the order\n")
    (c,) = merge(BASE, ours, theirs).conflicts
    assert (c.line, c.base, c.ours, c.theirs) == (3, "", "  - Greet the caller\n", "  - Ask for the order\n")


def test_deletions():
    gone = edit(BASE, "  - Name the allergen back\n", "")
    # a deleted line and an edit elsewhere merge
    got = merge(BASE, gone, edit(BASE, "Never say safe", "Never say safe or free"))
    assert got.clean and "Name the allergen" not in got.text and "Never say safe or free" in got.text
    # a deleted line and an edit to it conflict
    (c,) = merge(BASE, gone, edit(BASE, "Name the allergen back", "Name the allergen back twice")).conflicts
    assert (c.base, c.ours, c.theirs) == ("  - Name the allergen back\n", "", "  - Name the allergen back twice\n")
    # both deleting it is one deletion
    got = merge(BASE, gone, gone)
    assert got.clean and got.text == gone


def test_the_same_change_on_both_sides_is_taken_once():
    both = edit(BASE, "Never say safe", "Never say safe or free")
    ours = edit(both, "Ask about allergies", "Ask who has the allergy")
    got = merge(BASE, ours, both)
    assert got.clean and got.text == ours
    assert merge(BASE, BASE, both).text == both and merge(BASE, both, BASE).text == both


def test_contains():
    fix = edit(BASE, "Never say safe", "Never say safe or free")
    assert contains(BASE, fix, fix)
    assert contains(BASE, fix, edit(fix, "Ask about allergies", "Ask who has the allergy"))  # merged with another edit
    assert not contains(BASE, fix, BASE)
    assert not contains(BASE, fix, edit(BASE, "Never say safe", "Never say safe, ever"))

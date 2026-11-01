"""The dice sets: which holds are a Hardway set, and the name a Custom hold is logged under."""

from dice_stats import HARDWAY_SETUPS, HOLDS, SET_HOLDS, rolling_faces, set_family, set_name


def test_hardway_holds_roll_only_hardways():
    assert HARDWAY_SETUPS == ["2-3", "2-4", "3-2", "3-5", "4-2", "4-5", "5-3", "5-4"]
    assert len(HOLDS) == 24
    for hold in HARDWAY_SETUPS:
        assert sorted(rolling_faces(hold)) == [2, 3, 4, 5]


def test_custom_holds_take_the_named_set_they_are():
    assert set_name("Custom L3-2 R3-2") == "Hardway 3-2"
    assert set_name("Custom L6-2 R1-5") == "All Sevens L6-2 R1-5"    # 6-1, 2-5, 1-6, 5-2
    assert set_name("Custom L6-2 R6-2") == "Straight Sixes 6-2"      # 6-6, 2-2, 1-1, 5-5
    assert set_name("Custom L6-5 R6-5") == "Straight Sixes 6-5"      # the same pairs, turning the other way
    assert set_name("Custom L5-1 R5-1") == "Custom L5-1 R5-1"        # the same pairs too, but fives on top
    assert set_name("Custom L6-2 R6-5") == "Custom L6-2 R6-5"        # one die turned round: 6-6, 2-5, 1-1, 5-2
    assert set_name("Custom L3-2 R3-1") == "3-V L3-2 R3-1"           # 3-V turning the other way
    assert set_name("Custom L3-6 R3-5") == "Custom L3-6 R3-5"        # 3-V swapped round isn't a set
    assert set_name("Custom L2-3 R2-6") == "2-A L2-3 R2-6"
    assert set_name("Custom L6-4 R6-2") == "Cross-Sixes L6-4 R6-2"
    assert set_name("Custom L3-2 R2-3") == "Custom L3-2 R2-3"        # held differently, so not a Hardway set
    assert set_name("Custom L6-1 R6-2") == "Custom L6-1 R6-2"        # 6-1 isn't a hold: no face is both up and down
    assert set_name(" 3-V ") == "3-V"


def test_every_set_lists_its_holds():
    assert SET_HOLDS["Straight Sixes"] == [("6-2", "6-2"), ("6-5", "6-5")]
    assert SET_HOLDS["Parallel Sixes"] == [("6-3", "6-3"), ("6-4", "6-4")]
    assert sorted(SET_HOLDS["3-V"]) == [("3-2", "3-1"), ("3-5", "3-6")]
    assert sorted(SET_HOLDS["3-A"]) == [("3-1", "3-5"), ("3-6", "3-2")]
    assert sorted(SET_HOLDS["2-V"]) == [("2-1", "2-4"), ("2-6", "2-3")]
    assert sorted(SET_HOLDS["2-A"]) == [("2-3", "2-6"), ("2-4", "2-1")]           # 2-V's holds swapped round
    assert sorted(SET_HOLDS["Cross-Sixes"]) == [("6-2", "6-3"), ("6-2", "6-4"), ("6-3", "6-2"), ("6-3", "6-5"),
                                                 ("6-4", "6-2"), ("6-4", "6-5"), ("6-5", "6-3"), ("6-5", "6-4")]
    assert len(SET_HOLDS["Hardway"]) == 8 and len(SET_HOLDS["All Sevens"]) == 24
    every = [hold for holds in SET_HOLDS.values() for hold in holds]
    assert len(every) == len(set(every))                             # no hold is two sets
    for name, holds in SET_HOLDS.items():                            # and each is logged under its own set
        for hold in holds:
            assert set_family(set_name(f"Custom L{hold[0]} R{hold[1]}")) == name


def test_set_family_is_what_the_list_offers():
    assert set_family("Hardway 3-2") == "Hardway"
    assert set_family("Custom L6-2 R6-2") == "Custom"
    assert set_family("All Sevens") == "All Sevens"
    assert set_family("All Sevens L6-2 R1-5") == "All Sevens"
    assert set_family("Straight Sixes 6-5") == "Straight Sixes"
    assert set_family("3-V") == "3-V"

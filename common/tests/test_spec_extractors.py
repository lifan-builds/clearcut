from decimal import Decimal as D

import pytest
from deal_common.catalog.spec_extractors import Pack, extract_fit


@pytest.mark.parametrize(
    "kind, rows, expected",
    [
        # 01: Two independent waist sources agree; preserve radius reference length.
        (
            "ski",
            [
                ("Sidecut (tip-waist-tail) (mm)", "132–98–120"),
                ("Waist width", "9.8 cm"),
                ("Turn radius", "17,5 m @ 177 cm"),
            ],
            {"ski_waist_mm": "98", "turn_radius_m": ("17.5", "177")},
        ),
        # 02: FreshPowder title candidate and selected ski length are not specs.
        (
            "ski",
            [("Title", "2022 Nordica Santa Ana 93 Women's"), ("Length", "179 cm")],
            {},
        ),
        # 03: Valid conflicting waist observations; multi-radius isn't one radius.
        (
            "ski",
            [
                ("Waist (mm)", "93"),
                ("Sidecut (tip-waist-tail)", "131/95/119 mm"),
                ("Turn radius", "14 / 17 / 20 m"),
            ],
            {},
        ),
        # 04: Missing waist unit; don't reinterpret a surface area as meters.
        (
            "ski",
            [
                ("Waist", "93"),
                ("Turn radius", "17 m²"),
                ("Dimensions", "188 × 12 × 4 cm"),
            ],
            {},
        ),
        # 05: Explicit boot specs. The last's reference MP is NOT the selected MP.
        (
            "ski_boot",
            [
                ("Mondopoint (mm)", "235"),
                ("Flex index", "115"),
                ("Last width", "98 mm at MP 26.5"),
            ],
            {
                "mondopoint_cm": "23.5",
                "boot_flex_index": "115",
                "boot_last_mm": ("98", "26.5"),
            },
        ),
        # 06: LV is not 98 mm, model token isn't verified flex, size system unknown.
        (
            "ski_boot",
            [("Title", "K2 Anthem 115 LV Women's 2023"), ("Size", "23.5")],
            {},
        ),
        # 07: Explicit system marker in an option; contradictory last units rejected.
        (
            "ski_boot",
            [("Size", "MP23,5"), ("Last (mm)", "9.8 cm")],
            {"mondopoint_cm": "23.5"},
        ),
        # 08: Don't pick a size from a shared shell, a flex from a range, or a
        #     last width from an outsole length.
        (
            "ski_boot",
            [
                ("MP", "26/26.5"),
                ("Flex", "110–120"),
                ("Boot sole length", "315 mm"),
                ("Last", "315 mm"),
            ],
            {},
        ),
        # 09: Wrong category even if a field name looks right.
        (
            "snowboard_boot",
            [("Title", "Salomon Kiana Dual BOA"), ("Size", "7.5"), ("Flex", "70")],
            {},
        ),
        # 10: Advertised binding interval, not the brake width or model number.
        (
            "ski_binding",
            [("Title", "Attack 14 GW 95 mm"), ("DIN range", "4–14")],
            {"binding_din_range": ("4", "14")},
        ),
        # 11: Junior binding with decimal comma; don't round to an adult range.
        (
            "ski_binding",
            [("Release value range", "0,75 to 4,5 DIN")],
            {"binding_din_range": ("0.75", "4.5")},
        ),
        # 12: Boot-interface standard and model maximum aren't a DIN range.
        ("ski_binding", [("DIN", "DIN ISO 5355"), ("Max DIN", "13")], {}),
        # 13: Don't sort a reversed range or fall back to a personal setting.
        ("ski_binding", [("DIN range", "12-4"), ("Recommended DIN", "7")], {}),
        # 14: Trademark, nonbreaking hyphen, layers before brand.
        (
            "shell_jacket",
            [("Main shell construction", "3L GORE‑TEX® PRO")],
            {"shell_membrane": "GORE-TEX Pro", "shell_layers": "3"},
        ),
        # 15: Brand before layers. "Pro" in the garment name isn't a series.
        (
            "shell_pants",
            [
                ("Title", "Patrol Pro Pant"),
                ("Main shell construction", "Gore-Tex 2-layer"),
            ],
            {"shell_membrane": "GORE-TEX", "shell_layers": "2"},
        ),
        # 16: 2.5L is not 2L; no membrane brand was supplied.
        (
            "shell_jacket",
            [("Main shell construction", "2.5L")],
            {"shell_layers": "2.5"},
        ),
        # 17: Pro alone doesn't prove layers; pocket capacity isn't construction.
        (
            "shell_jacket",
            [("Main shell membrane", "GTX Pro"), ("Pocket volume", "3 L")],
            {"shell_membrane": "GORE-TEX Pro"},
        ),
        # 18: Can't make a single body-shell spec from a hybrid panel description.
        (
            "shell_jacket",
            [("Main shell construction", "3L GTX Pro shoulders / 2L body")],
            {},
        ),
        # 19: Unexpected explicit pairing: quarantine, never silently upgrade to 3L.
        (
            "shell_jacket",
            [
                ("Main shell membrane", "GORE-TEX Pro"),
                ("Main shell construction", "2 layers"),
            ],
            {},
        ),
        # 20: A valid heading unit supports a bare radius. Don't assume unlabelled
        #     sidecut order, even if the middle number looks exactly right.
        (
            "ski",
            [("Turn radius (m)", "16"), ("Sidecut", "127-93-115 mm")],
            {"turn_radius_m": ("16", None)},
        ),
    ],
)
def test_snow_specs(kind, rows, expected):
    result = extract_fit("freshpowder", {"kind": kind, "rows": rows})
    assert result["values"] == expected
    assert set(result["evidence"]) == set(expected)
    assert all(result["evidence"].values())


def row(name, fields, values, *, kind="complete_bike", issues=None):
    return pytest.param(kind, fields, values, issues or {}, id=name)


BIKE_CASES = [
    # 01: Mixed advertised build; 27.5 is the wheel, Medium is the frame.
    row(
        "01-vaast-xt-slx",
        [
            ("title", "Vaast E/1 XT/SLX e-bike | 27.5-inch wheels"),
            ("size_option", "Medium"),
        ],
        {"tiers": ("XT",), "wheel": "27.5", "size": "M"},
    ),
    # 02: Same model family, different build. No XT inheritance from #01.
    row(
        "02-vaast-enviolo",
        [
            ("title", "Vaast E/1 Enviolo e-bike"),
            ("wheel_size", "27.5"),
            ("frame_size", "Medium"),
        ],
        {"wheel": "27.5", "size": "M"},
    ),
    # 03: Material in an anchored model title; 700c stays 700c.
    row(
        "03-time-carbon-disc",
        [
            ("title", "TIME ADH Alpe d’Huez Carbon Disc frameset"),
            ("wheel_size", "700c"),
            ("size_option", "XL"),
        ],
        {"material": "Carbon", "wheel": "700c", "size": "XL"},
        kind="frameset",
    ),
    # 04: A dimensioned column may supply cm. Di2 is not a groupset tier.
    row(
        "04-bmc-performance-fit",
        [
            ("title", "BMC Impec Di2, Performance Fit frameset"),
            ("frame_size_cm", "55"),
        ],
        {"size": "55 cm"},
        kind="frameset",
    ),
    # 05: Shock dimensions are not travel; S1 is not a wheel size.
    row(
        "05-stumpjumper-shock",
        [
            (
                "title",
                (
                    "2022 Specialized Stumpjumper Frameset | FOX FLOAT DPS "
                    "Performance | 190 x 45 mm"
                ),
            ),
            ("size_option", "S1"),
            ("wheel_size", "29er"),
        ],
        {"size": "S1", "wheel": "29"},
        kind="frameset",
    ),
    # 06: Proprietary frame code; slash-separated wheels are not a mullet.
    row(
        "06-de-rosa-compact-code",
        [
            ("title", "De Rosa 888 Super King Electronic frameset"),
            ("size_option", "46s"),
            ("wheel_size", "29 / 27.5"),
        ],
        {"size": "46s"},
        kind="frameset",
        issues={"wheel": "unsupported_or_ambiguous"},
    ),
    # 07: Two travel values, one frame size, one wheel size.
    row(
        "07-alloy-gx-trail",
        [
            ("frame_material", "6061-T6 aluminum"),
            ("drivetrain", "SRAM GX Eagle"),
            ("title", "29-inch wheels | Fork travel: 150 mm | Rear travel: 140 mm"),
            ("size_option", "M / L"),
        ],
        {
            "material": "Alloy",
            "tiers": ("GX",),
            "wheel": "29",
            "fork_mm": 150,
            "rear_mm": 140,
            "size": "M/L",
        },
    ),
    # 08: Role-qualified pair; an inch measurement in a typed frame-size field.
    row(
        "08-role-qualified-mullet",
        [
            ("wheel_size", "29″ front / 27.5″ rear"),
            ("frame_size", '17.5"'),
            ("frame_material", "Ti"),
            ("drivetrain", "SRAM X0 Eagle AXS"),
        ],
        {"wheel": "Mullet", "size": "17.5 in", "material": "Ti", "tiers": ("X0",)},
    ),
    # 09: Explicit Mullet is sufficient. A generic naked 29 supplies no size.
    row(
        "09-xx-explicit-mullet",
        [
            ("wheel_size", "Mullet"),
            ("drivetrain", "SRAM XX T-Type"),
            ("size_option", "29"),
        ],
        {"wheel": "Mullet", "tiers": ("XX",)},
    ),
    # 10: Established format alias and a whole-token XTR (not XT).
    row(
        "10-steel-xtr-650b",
        [
            ("frame_material", "4130 chromoly"),
            ("wheel_size", "650B"),
            ("title", "Shimano XTR drivetrain"),
            ("size_option", "Small"),
        ],
        {"material": "Steel", "wheel": "27.5", "tiers": ("XTR",), "size": "S"},
    ),
    # 11: A carbon fork is not a carbon frame. 105 is a tier only in context.
    row(
        "11-105-carbon-fork",
        [
            ("title", "Carbon fork | Groupset: Shimano 105 Di2 | Frame size: 55 cm"),
            ("wheel_size", "700 C"),
        ],
        {"tiers": ("105",), "size": "55 cm", "wheel": "700c"},
    ),
    # 12: Bare generic 27.5 is neither a frame size nor a wheel observation.
    row(
        "12-ultegra-generic-numeric-size",
        [
            ("drivetrain", "Shimano Ultegra Di2"),
            ("size_option", "27.5"),
        ],
        {"tiers": ("Ultegra",)},
    ),
    # 13: Force is recognized in a build clause; brake rotor is not travel.
    row(
        "13-force-rotor",
        [
            ("title", "SRAM Force eTap AXS groupset | 160 mm front rotor"),
            ("frame_material", "carbon fibre"),
        ],
        {"tiers": ("Force",), "material": "Carbon"},
    ),
    # 14: Red as drivetrain label versus Red as color.
    row(
        "14-sram-red",
        [
            ("title", "Race Red | SRAM RED AXS groupset | Frame size: XS"),
        ],
        {"tiers": ("Red",), "size": "XS"},
    ),
    # 15: Negative-only example. No substring, color, or numeric tier guessing.
    row(
        "15-red-force-tiagra-false-positives",
        [
            ("title", "Force Edition | Race Red | Shimano Tiagra | 105 mm stem"),
            ("size_option", '29"'),
        ],
        {},
    ),
    # 16: No inference from compatibility, dropper travel, or geometry.
    row(
        "16-not-suspension-travel",
        [
            (
                "title",
                (
                    "Fits forks up to 170 mm | 150 mm dropper travel | "
                    "44 mm fork offset | 55 cm effective top tube"
                ),
            ),
        ],
        {},
        kind="frameset",
    ),
    # 17: Preserve fractional travel; no invented axle and no 45s -> 45 cm.
    row(
        "17-travel-without-axle",
        [
            ("suspension_travel", "152.4 mm"),
            ("frame_size", "45s"),
        ],
        {"travel_mm_unspecified": D("152.4"), "size": "45s"},
    ),
    # 18: Contradictions suppress only their own attributes.
    row(
        "18-conflicting-selected-specs",
        [
            ("wheel_size", "29"),
            ("title", "27.5-inch wheels"),
            ("frame_size", "M"),
            ("size_option", "M/L"),
            ("rear_travel", "120 mm"),
        ],
        {"rear_mm": 120},
        issues={"wheel": "conflict", "size": "conflict"},
    ),
    # 19: Reject ambiguous/unsupported explicit specs, even with a title hint.
    row(
        "19-ranges-mixed-material-and-xo",
        [
            ("fork_travel", "130–150 mm"),
            ("frame_material", "Carbon / Alloy"),
            ("wheel_size", "29 or 27.5"),
            ("title", "29-inch wheels"),
            ("drivetrain", "SRAM XO"),
            ("size_option", "L - 630wh Battery"),
            ("facet_frame_size", "Large"),
        ],
        {},
        issues={
            k: "unsupported_or_ambiguous"
            for k in ("fork_mm", "material", "wheel", "tiers")
        },
    ),
    # 20: Even perfectly formed specs on a component cannot create a bike row.
    row(
        "20-component-scope",
        [
            ("title", "Carbon frame | 29-inch wheels | Fork travel: 160 mm"),
            ("frame_size", "Large"),
            ("drivetrain", "Shimano XT"),
        ],
        {},
        kind="replacement_fork",
    ),
]


@pytest.mark.parametrize("kind,fields,expected,issues", BIKE_CASES)
def test_bike_specs(kind, fields, expected, issues):
    result = extract_fit("bigring", {"kind": kind, "fields": fields})
    assert result["values"] == expected
    assert result["issues"] == issues

    for attr, value in result["values"].items():
        assert result["evidence"][attr]
        assert all(h.raw.strip() and h.value == value for h in result["evidence"][attr])

    # Offer extraction must not depend on retailer field order.
    reversed_result = extract_fit("bigring", {"kind": kind, "fields": reversed(fields)})
    assert reversed_result["values"] == expected
    assert reversed_result["issues"] == issues


U = None
BARE = ("bare", 0, ())
UNKNOWN = ("unknown", U, ())
CONFLICT = ("conflict", U, ())


def kit(n, *packs):
    return ("battery_kit", n, tuple(packs))


def p(n, ah=None):
    return Pack(n, () if ah is None else ((ah, None),))


# brand, selected title, selected package field,
# platform, tool volts, brushless, package expectation, interface
POWER_CASES = [
    # 01: Z isn't the evidence; the explicit package label is.
    (
        "Makita",
        "XVJ04Z 18V LXT Brushless Jig Saw",
        "Bare tool",
        "makita.lxt",
        18,
        True,
        BARE,
        U,
    ),
    # 02: two required in series and two included are INDEPENDENT facts.
    (
        "Makita",
        "36V (18V LXT X2) BL Motor Saw with (2) 5.0Ah batteries + charger",
        "",
        "makita.lxt",
        36,
        True,
        kit(2, p(2, "5")),
        U,
    ),
    # 03: XGT alone isn't a motor designation; metric chuck stays metric.
    (
        "Makita",
        "40V MAX XGT Drill 13 mm keyless chuck with 4 Ah battery",
        "",
        "makita.xgt",
        40,
        U,
        kit(1, p(1, "4")),
        ("chuck", "13", "mm"),
    ),
    # 04: required pack slots are not included packs; blade isn't a chuck.
    (
        "Makita",
        "80V MAX (2×40V MAX) XGT 14-inch Power Cutter",
        "",
        "makita.xgt",
        80,
        U,
        UNKNOWN,
        U,
    ),
    # 05: B suffix and generic 20 V establish neither bare nor MAX.
    (
        "DeWalt",
        "DCS334B 20 V Cordless Brushless Jig Saw",
        "Battery/charger inclusion not specified",
        U,
        20,
        True,
        UNKNOWN,
        U,
    ),
    # 06: impact-wrench wording binds the fraction to the square drive.
    (
        "DeWalt",
        "DCF911B 20 V Cordless 1/2-in. Impact Wrench",
        "",
        U,
        20,
        U,
        UNKNOWN,
        ("square", "1/2", "in"),
    ),
    # 07: sibling SKU; must not inherit the 911's drive or vice versa.
    (
        "DeWalt",
        "DCF913B 20V Brushless 3/8-in Impact Wrench",
        "",
        U,
        20,
        True,
        UNKNOWN,
        ("square", "3/8", "in"),
    ),
    # 08: FLEXVOLT ADVANTAGE isn't a 60V tool or an included battery.
    (
        "DeWalt",
        "20V MAX FLEXVOLT ADVANTAGE Drill compatible with 60V MAX packs",
        "",
        "dewalt.20v_max",
        20,
        U,
        UNKNOWN,
        U,
    ),
    # 09: one physical pack, two voltage-specific Ah labels.
    (
        "DeWalt",
        (
            "60V MAX FLEXVOLT Saw with 1 FLEXVOLT battery "
            "(6 Ah @ 20 V / 2 Ah @ 60 V) + charger"
        ),
        "",
        "dewalt.flexvolt_60v_max",
        60,
        U,
        kit(1, Pack(1, (("6", 20), ("2", 60)))),
        U,
    ),
    # 10: saw diameter must not become a chuck/drive size.
    (
        "DeWalt",
        "DCS565B 20V MAX Brushless 6-1/2-in Circular Saw (Tool Only)",
        "",
        "dewalt.20v_max",
        20,
        True,
        BARE,
        U,
    ),
    # 11: hex driver, not a square-drive wrench. Nounless NxAh box notation.
    (
        "Milwaukee",
        "M12 FUEL ¼″ Hex Impact Driver with 2 x 2.0Ah",
        "",
        "milwaukee.m12",
        12,
        True,
        kit(2, p(2, "2")),
        ("hex", "1/4", "in"),
    ),
    # 12: two distinct included packs; don't assign 7 Ah to either pack.
    (
        "Milwaukee",
        'M18 FUEL 1/2" Square Drive Impact Wrench',
        "5 Ah battery + 2 Ah battery + bag",
        "milwaukee.m18",
        18,
        True,
        kit(2, p(1, "5"), p(1, "2")),
        ("square", "1/2", "in"),
    ),
    # 13: "fuel gauge" is not the FUEL motor line.
    (
        "Milwaukee",
        "M18 Brushed Drill, Fuel Gauge (Tool Only)",
        "",
        "milwaukee.m18",
        18,
        False,
        BARE,
        U,
    ),
    # 14: contradictory sources on THIS offer; don't silently prefer one.
    (
        "Milwaukee",
        "M18 FUEL Saw (Tool Only)",
        "5 Ah battery",
        "milwaukee.m18",
        18,
        True,
        CONFLICT,
        U,
    ),
    # 15: plural with no quantity; neither 1 nor 2 can be published.
    (
        "Bosch",
        "Professional 18V AMPShare Brushless Drill 13mm Chuck",
        "4 Ah batteries",
        "bosch.professional_18v",
        18,
        True,
        kit(U, p(U, "4")),
        ("chuck", "13", "mm"),
    ),
    # 16: generic 18V Bosch; a kit can be known even when Ah is unknown.
    ("Bosch", "18V Drill with battery and charger", "", U, 18, U, kit(1, p(1)), U),
    # 17: different Bosch family. A charger current isn't a battery capacity.
    (
        "Bosch",
        "18V POWER FOR ALL Drill",
        "2.4 A charger",
        "bosch.power_for_all_18v",
        18,
        U,
        UNKNOWN,
        U,
    ),
    # 18: EC-TEC establishes motor; "18+3" and Basic aren't voltage/box specs.
    ("Festool", "T 18+3 EC-TEC Drill Basic", "", U, U, True, UNKNOWN, U),
    # 19: reverse-order spec label and parenthesized quantity.
    (
        "Festool",
        "18 V EC-TEC Drill - Chuck Size: 13 mm",
        "(2) 4.0 Ah battery packs",
        U,
        18,
        True,
        kit(2, p(2, "4")),
        ("chuck", "13", "mm"),
    ),
    # 20: unresolved alternative configurations. Do not count both options.
    (
        "Festool",
        "18 V EC-TEC Drill Plus",
        "4 Ah battery or 5 Ah battery",
        U,
        18,
        True,
        UNKNOWN,
        U,
    ),
]


@pytest.mark.parametrize(
    "brand,title,box,platform,volts,brushless,pkg,interface",
    POWER_CASES,
    ids=[f"power-{i:02}" for i in range(1, 21)],
)
def test_selected_offer(brand, title, box, platform, volts, brushless, pkg, interface):
    r = extract_fit("benchdrop", {"brand": brand, "title": title, "package_text": box})[
        "values"
    ]
    assert (r["platform"], r["volts"], r["brushless"]) == (platform, volts, brushless)
    assert (r["package"], r["battery_count"], r["packs"]) == pkg
    assert r["interface"] == interface


def r(label, value, section=""):
    return section, label, value


@pytest.mark.parametrize(
    "kind,title,rows,want,blocked",
    [
        # 01: Marketing designation, occupancy, packed mass and trail mass
        # are four separate facts. Pounds + ounces are additive here.
        pytest.param(
            "tent",
            "Telos Freestanding Ultralight Tent",
            [
                r("Sleeping capacity", "3 people"),
                r("Packed weight", "2 lb 4 oz"),
                r("Trail weight", "0.90 kg"),
            ],
            {
                "ultralight_claim": True,
                "capacity_people": 3,
                "packed_weight_g": D("1020.6"),
                "trail_weight_g": D("900.0"),
            },
            set(),
            id="01-telos-distinct-weight-bases",
        ),
        # 02: Keep "Plus" in identity upstream; neither TR2 nor mass is a UL claim.
        pytest.param(
            "tent",
            "Alto TR2 Plus 2-Person Tent",
            [
                r("Minimum trail weight", "990 g"),
            ],
            {"capacity_people": 2, "minimum_weight_g": D("990.0")},
            set(),
            id="02-alto-explicit-occupancy",
        ),
        # 03: Neither a model suffix nor a capacity range is an exact occupancy.
        pytest.param(
            "tent",
            "Ikos Lightweight Tent TR3",
            [
                r("Capacity", "2–3 people"),
            ],
            {},
            {"capacity_people"},
            id="03-no-tr3-or-lightweight-inference",
        ),
        # 04: Unicode minus, two distinct thermal roles, and actual down FP.
        pytest.param(
            "sleeping_bag",
            "Winter bag",
            [
                r("Comfort", "−3 °C", "ISO 23537-1:2022"),
                r("Lower limit", "−9 °C", "ISO 23537-1:2022"),
                r("Extreme", "−28 °C", "ISO 23537-1:2022"),
                r("Insulation", "800-fill-power goose down"),
            ],
            {
                "comfort_temp": (D(-3), "C", "ISO 23537-1:2022"),
                "limit_temp": (D(-9), "C", "ISO 23537-1:2022"),
                "down_fill_power": (800, "stated"),
            },
            set(),
            id="04-iso-roles-and-800fp",
        ),
        # 05: A plus sign belongs to the FP claim, not to the temperature.
        pytest.param(
            "sleeping_bag",
            "Alpine bag",
            [
                r("EN 13537 comfort", "28°F"),
                r("Limit rating", "18 F (EN 13537)"),
                r("Fill power", "850+ FP"),
                r("Fill material", "Goose down"),
            ],
            {
                "comfort_temp": (D(28), "F", "EN 13537"),
                "limit_temp": (D(18), "F", "EN 13537"),
                "down_fill_power": (850, "at_least"),
            },
            set(),
            id="05-en-and-850plus",
        ),
        # 06: Preserve the quilt retailer's claim; kind excludes it from the
        # standard-rated sleeping-BAG facet.
        pytest.param(
            "quilt",
            "Trail Quilt",
            [
                r("Comfort (ISO 23537-1)", "0 °C"),
                r("Fill", "900FP duck down"),
            ],
            {
                "comfort_temp": (D(0), "C", "ISO 23537-1"),
                "down_fill_power": (900, "stated"),
            },
            set(),
            id="06-quilt-attributed-claim-and-900fp",
        ),
        # 07: The audited Kayu evidence has neither rating role nor unit.
        pytest.param(
            "sleeping_bag",
            "NEMO Kayu 30 Down Sleeping Bag",
            [
                r("Temperature rating", "30 degree"),
                r("Fill", "down"),
            ],
            {},
            set(),
            id="07-kayu-no-invented-comfort-f",
        ),
        # 08: Don't choose the warmer or colder of two contradictory comfort
        # claims. The unambiguous limit survives, with no standard inferred.
        pytest.param(
            "sleeping_bag",
            "Down bag",
            [
                r("Comfort", "20 F"),
                r("Comfort", "25 F"),
                r("Lower limit", "10 F"),
            ],
            {"limit_temp": (D(10), "F", None)},
            {"comfort_temp"},
            id="08-rating-conflict-is-field-local",
        ),
        # 09: A number in a generic fill-power field does not establish down.
        pytest.param(
            "quilt",
            "Synthetic quilt 850",
            [
                r("Fill power", "850"),
                r("Insulation type", "synthetic"),
                r("Fill weight", "12 oz"),
            ],
            {},
            {"down_fill_power"},
            id="09-synthetic-not-down-fp",
        ),
        # 10: Method and number both explicit. Normalize F 3340 spelling.
        pytest.param(
            "sleeping_pad",
            "Insulated pad",
            [
                r("R-value", "4.8 (ASTM F 3340-22)"),
            ],
            {"r_value": (D("4.8"), "ASTM F3340-22")},
            set(),
            id="10-explicit-pad-method",
        ),
        # 11: Neither "1R" nor "Ultralight" on a pad creates another facet.
        pytest.param(
            "sleeping_pad",
            "Exped Ultra 1R Mummy Sleeping Pad",
            [
                r("Size", "Long / Wide"),
                r("Construction", "Ultralight air"),
            ],
            {},
            set(),
            id="11-exped-model-name-not-a-test",
        ),
        # 12: A method heading on another row must not qualify the R-value.
        pytest.param(
            "sleeping_pad",
            "Air pad",
            [
                r("R-value", "3.2"),
                r("Notes", "see manual", "ASTM F3340-18"),
            ],
            {"r_value": (D("3.2"), None)},
            set(),
            id="12-no-method-bleed",
        ),
        # 13: Do not silently select one of two claimed test editions.
        pytest.param(
            "sleeping_pad",
            "Air pad",
            [
                r("R-value (ASTM F3340-22)", "4.2", "ASTM F3340-18"),
            ],
            {},
            {"r_value"},
            id="13-conflicting-methods",
        ),
        # 14: Liters, torso size, and torso-length interval are independent.
        pytest.param(
            "backpack",
            "Kelty Redwing 36 Women's",
            [
                r("Pack volume", "36 L"),
                r("Torso size", "M / L"),
                r("Fits torso", "16–19 in"),
            ],
            {
                "volume_l": D(36),
                "torso_size": "M/L",
                "torso_length_cm": (D("40.64"), D("48.26")),
            },
            set(),
            id="14-pack-liters-and-torso",
        ),
        # 15: Explicit pack-name suffix is usable. Generic size L is not liters
        # and is not a verified torso option.
        pytest.param(
            "backpack",
            "Topo Designs Rover Trail Pack 24L",
            [
                r("Size", "L"),
            ],
            {"volume_l": D(24)},
            set(),
            id="15-topo-unit-bearing-title",
        ),
        # 16: Do not add extension capacity or borrow pocket capacity.
        pytest.param(
            "backpack",
            "Expandable pack",
            [
                r("Volume", "50+10 L"),
                r("Front pocket volume", "5 L"),
                r("Torso size", "L"),
            ],
            {"torso_size": "L"},
            {"volume_l"},
            id="16-expandable-volume",
        ),
        # 17: Whole shell-garment weight is not fabric areal density.
        pytest.param(
            "shell_garment",
            "Rain shell — Medium",
            [
                r("Garment weight", "7.4 oz"),
                r("Shell fabric weight", "35 g/m²"),
            ],
            {"garment_weight_g": D("209.8")},
            set(),
            id="17-garment-not-gsm",
        ),
        # 18: Shell-only mass of a quilt is not its fill mass or total mass.
        pytest.param(
            "quilt",
            "Down quilt",
            [
                r("Shell weight", "2 oz"),
                r("Fill weight", "9 oz"),
            ],
            {"shell_weight_g": D("56.7")},
            set(),
            id="18-quilt-shell-only",
        ),
        # 19: Even a plausible label cannot turn oz/yd² into ounces of item.
        pytest.param(
            "shell_garment",
            "Wind shell",
            [
                r("Shell weight", "1.2 oz/yd²"),
                r("Shipping weight", "12 oz"),
                r("Face fabric", "20D nylon"),
            ],
            {},
            {"shell_weight_g"},
            id="19-reject-areal-and-shipping-weight",
        ),
        # 20: Catch a common adapter error: one selected pack given two sizes'
        # capacities. No first-row-wins, last-row-wins, or largest-size-wins.
        pytest.param(
            "backpack",
            "Variable-volume pack",
            [
                r("Pack capacity", "48 L"),
                r("Pack capacity", "52 L"),
            ],
            {},
            {"volume_l"},
            id="20-mixed-variant-conflict",
        ),
    ],
)
def test_highline_specs(kind, title, rows, want, blocked):
    got = extract_fit("highline", {"kind": kind, "title": title, "rows": rows})
    assert got["values"] == want
    assert set(got["issues"]) == blocked
    assert got["evidence"].keys() == want.keys()
    assert all(got["evidence"].values())
    assert all(got["issues"].values())


# id, title, selected spec cells, exact_title, kind, values, issues
FOIL_CASES = [
    (
        "01-hand-wing-superscript",
        "Handheld Wing",
        ("4.5 m²",),
        False,
        "hand_wing",
        {"wing_area_m2": D("4.5")},
        (),
    ),
    (
        "02-selected-not-parent-range",
        "Wing Sail 4 / 5 / 6 m²",
        ("5 m^2",),
        False,
        "hand_wing",
        {"wing_area_m2": D(5)},
        (),
    ),
    (
        "03-rpx-kite-is-not-hand-wing",
        "Slingshot RPX V1 Kite",
        ("4.5m",),
        False,
        "kite",
        {},
        (),
    ),
    (
        "04-linear-metres-not-square-metres",
        "Hand Wing",
        ("4.5m",),
        False,
        "hand_wing",
        {},
        ("unparsed:selected[0]",),
    ),
    (
        "05-front-area-class-and-numeric-ratio",
        "Foil Front Wing",
        ("1200 cm² | HA | AR 8.2",),
        False,
        "front_wing",
        {
            "front_wing_area_cm2": D(1200),
            "aspect_class": "HA",
            "aspect_ratio": D("8.2"),
        },
        (),
    ),
    (
        "06-model-digits-are-not-area",
        "X1200 Front Wing",
        ("medium-aspect",),
        True,
        "front_wing",
        {"aspect_class": "MA"},
        (),
    ),
    (
        "07-front-low-aspect-exact-title",
        "Front Wing: LA 950 cm^2",
        (),
        True,
        "front_wing",
        {"aspect_class": "LA", "front_wing_area_cm2": D(950)},
        (),
    ),
    (
        "08-model-code-is-not-two-specs",
        "Front Wing HA925",
        (),
        True,
        "front_wing",
        {},
        (),
    ),
    (
        "09-audited-wing-pack-is-not-complete",
        "Mkii Foil Kit Wing Pack",
        ("X2100 | V285",),
        False,
        "component_pack",
        {},
        (),
    ),
    (
        "10-kit-alone-is-insufficient",
        "Hydrofoil Kit",
        ("85 cm", "1200 cm²"),
        False,
        "unknown",
        {},
        (),
    ),
    (
        "11-complete-but-no-unscoped-component-specs",
        "Complete Hydrofoil Kit",
        ("85 cm", "1200 cm²"),
        False,
        "complete_foil",
        {},
        (),
    ),
    (
        "12-incomplete-qualifier-beats-complete-copy",
        "Complete Foil Kit with mast sold separately",
        (),
        False,
        "unknown",
        {},
        (),
    ),
    (
        "13-mast-not-compatible-board",
        "Carbon Foil Mast for Foil Board",
        ("850 mm",),
        True,
        "mast",
        {"mast_material": "carbon", "mast_length_cm": D(85)},
        (),
    ),
    (
        "14-fuselage-material-does-not-overwrite-mast",
        "Foil Mast with carbon fuselage",
        ("0.75 m | aluminium",),
        True,
        "mast",
        {"mast_length_cm": D(75), "mast_material": "alloy"},
        (),
    ),
    (
        "15-plate-dimensions-not-mast-length",
        "Foil Mast",
        ("90 x 160 mm",),
        False,
        "mast",
        {},
        ("unparsed:selected[0]",),
    ),
    (
        "16-conflicting-material-preserves-independent-length",
        "Carbon Foil Mast",
        ("75 cm", "alloy"),
        True,
        "mast",
        {"mast_length_cm": D(75)},
        ("conflict:mast_material",),
    ),
    (
        "17-houston-80-l-variant",
        "Slingshot Wing Craft V2",
        ('80 L | 5\'0" x 24"',),
        False,
        "board",
        {"board_volume_l": D(80)},
        (),
    ),
    (
        "18-force-70-l-variant",
        "Slingshot Wing Craft V2",
        ("70L",),
        False,
        "board",
        {"board_volume_l": D(70)},
        (),
    ),
    (
        "19-range-not-selected-board-volume",
        "Wing Foil Board",
        ("70–80 L",),
        False,
        "board",
        {},
        ("unparsed:selected[0]",),
    ),
    (
        "20-front-wing-cover-is-an-accessory",
        "Front Wing Cover",
        ("1200 cm²",),
        False,
        "accessory",
        {},
        (),
    ),
]


@pytest.mark.parametrize(
    "case,title,selected,exact,kind,values,issues",
    FOIL_CASES,
    ids=[c[0] for c in FOIL_CASES],
)
def test_foil_specs(case, title, selected, exact, kind, values, issues):
    got = extract_fit(
        "tideline", {"title": title, "selected": selected, "exact_title": exact}
    )
    assert (got["kind"], got["values"], got["issues"]) == (kind, values, issues)
    assert got["values"].keys() <= got["evidence"].keys()

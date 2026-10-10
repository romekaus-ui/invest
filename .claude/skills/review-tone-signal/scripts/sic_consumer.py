#!/usr/bin/env python3
"""Consumer-product SIC codes, from SEC's official Division of Corporation
Finance SIC list.

Selection rule, stated before any company was looked at: include a code when a
typical registrant under it makes or sells a PHYSICAL CONSUMER PRODUCT that
could plausibly be bought on Amazon and reviewed by the buyer.

Excluded on that rule: semiconductors and electronic components, industrial
and construction machinery, primary metals, aerospace and defence, petroleum,
building materials, and wholesale distribution (B2B by construction).

Communications equipment (3661/3663/3669) is KEPT despite being mostly
enterprise, because it also contains consumer audio and accessory makers
(Turtle Beach sits in 3669). The enterprise names are removed downstream by
eligibility criterion 4, which leaves a documented reason per company rather
than a silent omission here.
"""

FOOD_BEVERAGE = ["2000", "2011", "2013", "2015", "2020", "2024", "2030", "2033",
                 "2040", "2050", "2052", "2060", "2070", "2080", "2086", "2090",
                 "2092"]

PERSONAL_CARE_HOUSEHOLD_CHEM = ["2833", "2834", "2840", "2842", "2844"]

TEXTILES_APPAREL = ["2200", "2211", "2221", "2250", "2253", "2273", "2300",
                    "2320", "2330", "2340", "2390"]

FURNITURE = ["2510", "2511", "2590"]

PAPER_PRINT_CONSUMER = ["2670", "2673", "2731", "2741", "2771", "2780"]

RUBBER_LEATHER_FOOTWEAR = ["3011", "3021", "3060", "3100", "3140"]

GLASS_POTTERY_HOUSEWARES = ["3220", "3221", "3231", "3260"]

METAL_HOUSEWARES_HARDWARE = ["3411", "3420", "3430", "3433", "3490"]

LAWN_GARDEN = ["3523", "3524"]

APPLIANCES_HVAC = ["3585", "3590", "3630", "3634"]

ELECTRICAL_CONSUMER = ["3600", "3640", "3651", "3652", "3690", "3695"]

COMPUTERS_PERIPHERALS = ["3570", "3571", "3572", "3575", "3577", "3578", "3579"]

COMMUNICATIONS = ["3661", "3663", "3669"]

VEHICLES_ACCESSORIES = ["3711", "3714", "3716", "3751", "3790"]

OPTICAL_MEDICAL_CONSUMER = ["3827", "3841", "3842", "3845", "3851", "3861"]

JEWELRY_TOYS_SPORTING_MISC = ["3910", "3911", "3931", "3942", "3944", "3949",
                              "3950", "3960", "3990", "3991"]

RETAIL = ["5200", "5211", "5311", "5331", "5399", "5400", "5411", "5412",
          "5500", "5531", "5600", "5621", "5651", "5661", "5700", "5712",
          "5731", "5734", "5735", "5900", "5912", "5940", "5944", "5945",
          "5960", "5961", "5990"]

GROUPS = {
    "food_beverage": FOOD_BEVERAGE,
    "personal_care": PERSONAL_CARE_HOUSEHOLD_CHEM,
    "apparel": TEXTILES_APPAREL,
    "furniture": FURNITURE,
    "paper_print": PAPER_PRINT_CONSUMER,
    "footwear_leather": RUBBER_LEATHER_FOOTWEAR,
    "housewares_glass": GLASS_POTTERY_HOUSEWARES,
    "hardware_metal": METAL_HOUSEWARES_HARDWARE,
    "lawn_garden": LAWN_GARDEN,
    "appliances": APPLIANCES_HVAC,
    "consumer_electrical": ELECTRICAL_CONSUMER,
    "computers": COMPUTERS_PERIPHERALS,
    "communications": COMMUNICATIONS,
    "vehicles_accessories": VEHICLES_ACCESSORIES,
    "optical_medical": OPTICAL_MEDICAL_CONSUMER,
    "jewelry_toys_sporting": JEWELRY_TOYS_SPORTING_MISC,
    "retail": RETAIL,
}

ALL = sorted({c for codes in GROUPS.values() for c in codes})

# Groups most likely to clear eligibility criterion 1 (Amazon channel share).
# Advisory only - it narrows a run, it never scores a company.
CORE = sorted({c for g in ("appliances", "consumer_electrical", "housewares_glass",
                           "hardware_metal", "jewelry_toys_sporting", "computers",
                           "furniture", "lawn_garden", "footwear_leather",
                           "personal_care", "optical_medical")
               for c in GROUPS[g]})

if __name__ == "__main__":
    print("%d consumer SIC codes in %d groups (CORE subset: %d)"
          % (len(ALL), len(GROUPS), len(CORE)))
    for name, codes in sorted(GROUPS.items()):
        print("  %-22s %2d  %s" % (name, len(codes), " ".join(codes)))

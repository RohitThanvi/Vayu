"""One season mapping for BOTH the eligibility derivation and the ground-truth builder (they previously disagreed, which dropped 42% of national rice area).

DES / Kaggle 'Crop Production in India' season labels -> Vayu seasons:
  Kharif, Autumn, Winter -> kharif   (Autumn / Winter are state-specific names for monsoon-sown rice such as aus and aman)
  Rabi                   -> rabi
  Summer                 -> zaid
  Whole Year             -> unassigned (excluded)
"""
SEASON_MAP = {"kharif": "kharif", "autumn": "kharif", "winter": "kharif", "rabi": "rabi", "summer": "zaid"}

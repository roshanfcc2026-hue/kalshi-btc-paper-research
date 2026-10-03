"""Alpha signals. Each is a separate source with NAME, VERSION, REASON, PARAMS, FEATURES, PROB_FEATURE, compute(snap).
S6 (perp basis) is not included: Layer 1 does not collect futures data and free-API access could not be verified."""
from . import s1_vol_distance, s2_exchange_imbalance, s3_momentum, s4_lead_lag, s5_kalshi_book

ALL = (s1_vol_distance, s2_exchange_imbalance, s3_momentum, s4_lead_lag, s5_kalshi_book)

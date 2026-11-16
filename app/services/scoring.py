PRIOR_WEIGHT = 50
DEFAULT_PRIOR = 3.8
RATING_SHARE = 0.7


def bayesian_rating(rating, reviews, prior_mean=DEFAULT_PRIOR, prior_weight=PRIOR_WEIGHT):
    if not rating or not reviews:
        return prior_mean

    return (reviews * rating + prior_weight * prior_mean) / (reviews + prior_weight)


def rank_listings(listings):
    prices = [item.price for item in listings if item.price]
    lowest = min(prices) if prices else None
    highest = max(prices) if prices else None

    for item in listings:
        rating_part = bayesian_rating(item.rating, item.review_count) / 5

        if item.price is None or lowest is None:
            price_part = 0.0
        elif highest == lowest:
            price_part = 1.0
        else:
            price_part = (highest - item.price) / (highest - lowest)

        item.score = round(
            100 * (RATING_SHARE * rating_part + (1 - RATING_SHARE) * price_part),
            1,
        )

    return sorted(
        listings,
        key=lambda item: (-(item.score or 0), item.price if item.price is not None else float("inf")),
    )

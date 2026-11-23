"""One-sentence, plain-English summary of a forecast.

Built only from the model's own factor effects (the same numbers the
"Why the model thinks this" section shows). No language model is involved
and nothing is said that the numbers do not support: a factor is mentioned
only if resetting it to league average would move the win probability by a
stated minimum."""
from __future__ import annotations

PHRASES = {
    "home": "home ice",
    "xga": "chance quality",
    "xg5": "five-on-five play",
    "goals": "goal difference",
    "elo": "recent results",
    "goalie": "goaltending",
    "rest": "rest and travel",
    "special": "special teams",
    "finishing": "finishing",
    "lineup_toi": "a healthier lineup",
    "lineup_gs": "a healthier lineup",
}

MENTION = 0.005        # a supporting factor must be worth at least half a point
COUNTER = 0.010        # an opposing factor must be worth at least a full point
TOSS_UP = 0.53


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{items[0]} and {items[1]}"


def headline(drivers: list[dict] | None, home: str, away: str, p_home: float) -> str | None:
    """`drivers` are the forecast's factor effects; `home_win_prob_effect`
    is each factor's effect on the home team's win probability."""
    if not drivers:
        return None
    fav_home = p_home >= 0.5
    fav, dog = (home, away) if fav_home else (away, home)
    p_fav = p_home if fav_home else 1.0 - p_home
    effects = []
    for d in drivers:
        phrase = PHRASES.get(d.get("key"))
        if phrase is None:           # the league scoring environment is context, not a matchup factor
            continue
        e = d.get("home_win_prob_effect", 0.0)
        effects.append((e if fav_home else -e, phrase))
    effects.sort(reverse=True)
    support: list[str] = []
    for e, phrase in effects:
        if e >= MENTION and phrase not in support:
            support.append(phrase)
    support = support[:2]
    against = [(e, phrase) for e, phrase in effects if e <= -COUNTER]
    counter = against[-1][1] if against else None

    if p_fav < TOSS_UP:
        text = f"Close to even. {support[0].capitalize()} tilts it slightly toward {fav}." if support else "Close to even: nothing separates these teams."
    else:
        lead = f"{fav} strongly favoured" if p_fav >= 0.70 else f"{fav} favoured" if p_fav >= 0.60 else f"Slight edge to {fav}"
        text = f"{lead}, mainly on {_join(support)}." if support else f"{lead}."
    if counter:
        text += f" {dog}'s best argument is {counter}."
    return text

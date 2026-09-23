"""Statistical prediction engine.

Core model: a Dixon-Coles adjusted bivariate Poisson.

    1. Estimate each team's attack / defence rating from its recent results
       (recency-weighted, shrunk toward the league mean with a Bayesian prior
       so a team with 3 games played isn't ranked like a 30-game side).
    2. Convert ratings into expected goals for the fixture.
    3. Build the full scoreline matrix 0..MAX_GOALS using Poisson marginals
       with the Dixon-Coles tau correction for low-scoring results
       (0-0, 1-0, 0-1, 1-1) which a plain Poisson over/under-predicts.
    4. Derive 1X2, Over/Under and BTTS probabilities from the matrix.
    5. Blend with bookmaker implied probabilities (removing the overround)
       so a short-priced favourite isn't outranked by a raw longshot.
    6. Flag value where blended probability beats the de-vigged market price.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .models import Fixture, Standing, Team

MAX_GOALS = 9
# Bumped when the shape of the prediction payload changes, not only when the
# maths does. Predictions are cached on the fixture row and only recomputed when
# this differs from the stored value, so adding or removing markets without
# bumping it keeps serving the old payload (duplicate 1UP markets reappearing
# from cache, for example) even though the code is correct.
MODEL_VERSION = "dc-2.6"  # 2.6 adds the five-market top_picks shortlist


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _binom(n: int, k: int) -> int:
    """Binomial coefficient, for splitting a goal total across the two halves."""
    if k < 0 or k > n:
        return 0
    return math.comb(n, k)


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam**k) / math.factorial(k)


def _dc_tau(h: int, a: int, lh: float, la: float, rho: float) -> float:
    """Dixon-Coles low-score dependency correction."""
    if h == 0 and a == 0:
        return 1.0 - lh * la * rho
    if h == 0 and a == 1:
        return 1.0 + lh * rho
    if h == 1 and a == 0:
        return 1.0 + la * rho
    if h == 1 and a == 1:
        return 1.0 - rho
    return 1.0


def implied_probability(odds: float | None) -> float | None:
    """Raw bookmaker implied probability (still contains the margin)."""
    if not odds or odds <= 1.0:
        return None
    return 1.0 / odds


def devig(odds_list: list[float | None]) -> list[float | None]:
    """Remove the bookmaker overround so implied probabilities sum to 1."""
    raw = [implied_probability(o) for o in odds_list]
    total = sum(p for p in raw if p is not None)
    if not total:
        return [None for _ in raw]
    return [None if p is None else p / total for p in raw]


def fair_odds(probability: float) -> float:
    if probability <= 0:
        return 999.0
    return round(max(1.01, 1.0 / probability), 2)


def kelly_fraction(probability: float, odds: float, cap: float = 0.05) -> float:
    """Fractional (quarter) Kelly stake as a percentage of bankroll."""
    if not odds or odds <= 1.0:
        return 0.0
    b = odds - 1.0
    kelly = (probability * b - (1.0 - probability)) / b
    if kelly <= 0:
        return 0.0
    return round(min(kelly * 0.25, cap) * 100, 2)


# --------------------------------------------------------------------------
# team ratings
# --------------------------------------------------------------------------

@dataclass
class TeamStats:
    team_id: int
    name: str
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0
    home_played: int = 0
    home_points: int = 0
    away_played: int = 0
    away_points: int = 0
    clean_sheets: int = 0
    btts_hits: int = 0
    over25_hits: int = 0
    recent_points: float = 0.0
    recent_weight: float = 0.0
    recent_goals_for: float = 0.0
    recent_goals_against: float = 0.0
    form: list[str] = field(default_factory=list)
    position: int | None = None

    @property
    def points(self) -> int:
        return self.won * 3 + self.drawn

    @property
    def goal_diff(self) -> int:
        return self.goals_for - self.goals_against
    @property
    def ppg(self) -> float:
        return round(self.points / self.played, 2) if self.played else 0.0
    @property
    def home_ppg(self) -> float:
        return round(self.home_points / self.home_played, 2) if self.home_played else 0.0
    @property
    def away_ppg(self) -> float:
        return round(self.away_points / self.away_played, 2) if self.away_played else 0.0
    @property
    def btts_rate(self) -> float:
        return round(self.btts_hits / self.played, 3) if self.played else 0.0
    @property
    def over25_rate(self) -> float:
        return round(self.over25_hits / self.played, 3) if self.played else 0.0
    @property
    def avg_scored(self) -> float:
        return round(self.goals_for / self.played, 2) if self.played else 0.0
    @property
    def avg_conceded(self) -> float:
        return round(self.goals_against / self.played, 2) if self.played else 0.0
    def to_form_entry(self) -> dict:
        return {
            "team_id": self.team_id,
            "team_name": self.name,
            "played": self.played,
            "won": self.won,
            "drawn": self.drawn,
            "lost": self.lost,
            "goals_for": self.goals_for,
            "goals_against": self.goals_against,
            "goal_diff": self.goal_diff,
            "points": self.points,
            "form": "".join(self.form[-5:]),
            "position": self.position,
            "ppg": self.ppg,
            "home_ppg": self.home_ppg,
            "away_ppg": self.away_ppg,
            "clean_sheets": self.clean_sheets,
            "btts_rate": self.btts_rate,
            "over25_rate": self.over25_rate,
            "avg_scored": self.avg_scored,
            "avg_conceded": self.avg_conceded,
        }


def _recency_weight(index_from_latest: int) -> float:
    """Exponential decay: the most recent match weighs ~2.7x a 10-game-old one."""
    return math.exp(-0.10 * index_from_latest)


def collect_team_stats(db: Session, team_id: int, limit: int = 30) -> TeamStats:
    """Aggregate a team's recent finished matches into a TeamStats record."""
    fixtures = db.scalars(
        select(Fixture)
        .options(selectinload(Fixture.home_team), selectinload(Fixture.away_team))
        .where(
            Fixture.status == "finished",
            Fixture.home_goals.is_not(None),
            (Fixture.home_team_id == team_id) | (Fixture.away_team_id == team_id),
        )
        .order_by(Fixture.kickoff.desc())
        .limit(limit)
    ).all()

    stats = TeamStats(team_id=team_id, name="")
    for idx, fx in enumerate(fixtures):
        is_home = fx.home_team_id == team_id
        gf = fx.home_goals if is_home else fx.away_goals
        ga = fx.away_goals if is_home else fx.home_goals
        if gf is None or ga is None:
            continue

        stats.played += 1
        stats.goals_for += gf
        stats.goals_against += ga
        if is_home:
            stats.home_played += 1
        else:
            stats.away_played += 1

        if gf > ga:
            stats.won += 1
            stats.form.append("W")
            pts = 3
        elif gf == ga:
            stats.drawn += 1
            stats.form.append("D")
            pts = 1
        else:
            stats.lost += 1
            stats.form.append("L")
            pts = 0

        if is_home:
            stats.home_points += pts
        else:
            stats.away_points += pts

        if ga == 0:
            stats.clean_sheets += 1
        if gf > 0 and ga > 0:
            stats.btts_hits += 1
        if gf + ga > 2.5:
            stats.over25_hits += 1

        w = _recency_weight(idx)
        stats.recent_points += pts * w
        stats.recent_weight += w
        stats.recent_goals_for += gf * w
        stats.recent_goals_against += ga * w

    stats.form = list(reversed(stats.form))  # oldest -> newest
    return stats


@dataclass
class Ratings:
    attack: float
    defence: float
    played: int
    attack_raw: float
    defence_raw: float


def estimate_ratings(stats: TeamStats, league_avg: float) -> Ratings:
    """Attack/defence multipliers vs the league average, shrunk toward 1.0.

    attack = (goals scored / matches) weighted by recency, with a synthetic
    sample of ``prior_weight`` average matches mixed in.
    """
    if stats.played == 0 or stats.recent_weight == 0:
        return Ratings(1.0, 1.0, 0, 1.0, 1.0)

    # recency-weighted per-match scoring / conceding
    weighted_matches = stats.recent_weight
    scored_pm = stats.recent_goals_for / weighted_matches
    conceded_pm = stats.recent_goals_against / weighted_matches

    # points-based momentum nudge (max +/-5%)
    max_pts = 3.0 * weighted_matches
    momentum = (stats.recent_points / max_pts) if max_pts else 0.5
    momentum_mult = 1.0 + (momentum - 0.5) * 0.10

    attack_raw = (scored_pm / league_avg) * momentum_mult if league_avg else 1.0
    defence_raw = (conceded_pm / league_avg) if league_avg else 1.0

    # Bayesian shrinkage toward the league mean
    w = stats.played / (stats.played + settings.prior_weight)
    attack = 1.0 + (attack_raw - 1.0) * w
    defence = 1.0 + (defence_raw - 1.0) * w

    # clamp to a sane band so a freak 7-0 doesn't produce absurd lambdas
    return Ratings(
        attack=max(0.45, min(2.20, attack)),
        defence=max(0.45, min(2.20, defence)),
        played=stats.played,
        attack_raw=attack_raw,
        defence_raw=defence_raw,
    )


# --------------------------------------------------------------------------
# scoreline matrix and markets
# --------------------------------------------------------------------------

@dataclass
class Matrix:
    grid: list[list[float]]
    home_lambda: float
    away_lambda: float

    def probability(self, home_goals: int, away_goals: int) -> float:
        if 0 <= home_goals <= MAX_GOALS and 0 <= away_goals <= MAX_GOALS:
            return self.grid[home_goals][away_goals]
        return 0.0

    def home_win(self) -> float:
        return sum(
            self.grid[h][a] for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1) if h > a
        )

    def draw(self) -> float:
        return sum(self.grid[i][i] for i in range(MAX_GOALS + 1))

    def away_win(self) -> float:
        return sum(
            self.grid[h][a] for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1) if h < a
        )

    def over(self, line: float) -> float:
        return sum(
            self.grid[h][a]
            for h in range(MAX_GOALS + 1)
            for a in range(MAX_GOALS + 1)
            if h + a > line
        )

    def btts(self) -> float:
        return sum(
            self.grid[h][a]
            for h in range(MAX_GOALS + 1)
            for a in range(MAX_GOALS + 1)
            if h > 0 and a > 0
        )

    def top_scorelines(self, n: int = 6) -> list[dict]:
        cells = [
            {"home_goals": h, "away_goals": a, "probability": round(self.grid[h][a], 4)}
            for h in range(MAX_GOALS + 1)
            for a in range(MAX_GOALS + 1)
        ]
        cells.sort(key=lambda c: c["probability"], reverse=True)
        return cells[:n]

    # ------------------------------------------------------------------
    # Derived markets. Every method below is an exact sum over the same
    # scoreline grid, so these are model probabilities rather than guesses: no
    # extra assumptions are introduced beyond the ones already in the matrix.
    # Where a real book price exists for a line, build_markets() still de-vigs
    # and blends it; where none exists the selection is model-only and edge
    # stays None.
    # ------------------------------------------------------------------

    def _sum(self, pred) -> float:
        return sum(
            self.grid[h][a]
            for h in range(MAX_GOALS + 1)
            for a in range(MAX_GOALS + 1)
            if pred(h, a)
        )

    # --- match result, expressed as priceable combinations -----------------
    def double_chance(self, picks: tuple[str, ...]) -> float:
        """1X, 12 or X2 as a single probability."""
        total = 0.0
        if "home" in picks:
            total += self.home_win()
        if "draw" in picks:
            total += self.draw()
        if "away" in picks:
            total += self.away_win()
        return total
    def win_to_nil(self, side: str) -> float:
        """Win without conceding, from ``home`` or ``away``."""
        if side == "home":
            return self._sum(lambda h, a: a == 0 and h > 0)
        return self._sum(lambda h, a: h == 0 and a > 0)

    def win_margin(self, side: str, margin: int = 1) -> float:
        """Win by exactly ``margin`` goals (margin=1 covers 'by 1')."""
        if side == "home":
            return self._sum(lambda h, a: h - a == margin)
        return self._sum(lambda h, a: a - h == margin)

    def draw_no_bet(self, side: str) -> float:
        """Draw No Bet: the win price with the stake returned on a draw.

        Conditional on the match not ending level, which is what a DNB bet
        actually settles on.
        """
        decider = 1.0 - self.draw()
        if decider <= 0:
            return 0.0
        win = self.home_win() if side == "home" else self.away_win()
        return win / decider
    # --- team totals -------------------------------------------------------
    def team_over(self, side: str, line: float) -> float:
        if side == "home":
            return self._sum(lambda h, a: h > line)
        return self._sum(lambda h, a: a > line)

    def team_to_score(self, side: str) -> float:
        if side == "home":
            return self._sum(lambda h, a: h > 0)
        return self._sum(lambda h, a: a > 0)

    def clean_sheet(self, side: str) -> float:
        """The given side keeps a clean sheet."""
        if side == "home":
            return self._sum(lambda h, a: a == 0)
        return self._sum(lambda h, a: h == 0)

    # --- handicap (whole-goal lines; pushes excluded from the denominator) --
    def handicap(self, side: str, line: float) -> float:
        """Probability the side wins with ``line`` added to its score.

        Whole-goal lines can push; a push returns the stake, so it is excluded
        from the settled probability the same way Draw No Bet excludes draws.
        """

        def covered(h: int, a: int) -> bool:
            return (h + line > a) if side == "home" else (a + line > h)

        def pushed(h: int, a: int) -> bool:
            return (h + line == a) if side == "home" else (a + line == h)

        settled = self._sum(lambda h, a: not pushed(h, a))
        if settled <= 0:
            return 0.0
        return self._sum(covered) / settled
    # --- totals shape ------------------------------------------------------
    def odd_even(self, parity: str) -> float:
        if parity == "odd":
            return self._sum(lambda h, a: (h + a) % 2 == 1)
        return self._sum(lambda h, a: (h + a) % 2 == 0)

    def highest_scoring_half(self, half: str) -> float:
        """Which half has more goals, using the standard 45/55 goal split.

        The matrix is a full-match model, so the halves are separated by the
        conventional share of goals that fall before and after the break. That
        share is an assumption, so these two selections are marked model-only
        and never carry a claimed edge against a real market.
        """
        first_share = 0.45
        first = 0.0
        second = 0.0
        equal = 0.0
        for h in range(MAX_GOALS + 1):
            for a in range(MAX_GOALS + 1):
                p = self.grid[h][a]
                total = h + a
                if total == 0:
                    equal += p
                    continue
                # Binomial split of the match total across the two halves.
                for k in range(total + 1):
                    pk = _binom(total, k) * (first_share**k) * ((1 - first_share) ** (total - k))
                    if k > total - k:
                        first += p * pk
                    elif k < total - k:
                        second += p * pk
                    else:
                        equal += p * pk
        if half == "first":
            return first
        if half == "second":
            return second
        return equal
    def correct_score(self, h: int, a: int) -> float:
        return self.probability(h, a)

    def total_goals_in_range(self, lo: int, hi: int) -> float:
        return self._sum(lambda h, a: lo <= h + a <= hi)

    # ------------------------------------------------------------------
    # Half-time markets.
    #
    # The matrix is a FULL-MATCH model: 90 minutes of goals are split 45/55
    # across the halves (the conventional share). That split is an assumption
    # rather than something the results history in this app measures -- no
    # seeded row carries a half-time score -- so every selection built on it is
    # published model-only, with no odds and edge left as None. It is never
    # compared against a book price it did not come from.
    #
    # Independent Poisson is the standard way to split a match total: each goal
    # falls in the first half with probability `share`, hence Binomial(n, share).
    # ------------------------------------------------------------------

    #: Share of a match's goals that arrive before half-time.
    FIRST_HALF_SHARE = 0.45
    def _half_lambdas(self) -> tuple[float, float, float, float]:
        """(home_1h, home_2h, away_1h, away_2h) expected goals."""
        s = self.FIRST_HALF_SHARE
        return (
            self.home_lambda * s,
            self.home_lambda * (1 - s),
            self.away_lambda * s,
            self.away_lambda * (1 - s),
        )

    def half_grid(self, which: str) -> list[list[float]]:
        """A Poisson scoreline grid for one half (``first``/``second``).

        Rebuilt without the Dixon-Coles tau correction: tau is calibrated on
        full-time low scores, and applying it to a half would be reusing a
        correction outside the context it was fitted for.
        """
        if which == "first":
            lh, _h2, la, _a2 = self._half_lambdas()
        else:
            _h1, lh, _a1, la = self._half_lambdas()
        return [
            [_poisson_pmf(h, lh) * _poisson_pmf(a, la) for a in range(MAX_GOALS + 1)]
            for h in range(MAX_GOALS + 1)
        ]

    def _half_sum(self, which: str, pred) -> float:
        grid = self.half_grid(which)
        return sum(
            grid[h][a]
            for h in range(MAX_GOALS + 1)
            for a in range(MAX_GOALS + 1)
            if pred(h, a)
        )

    def half_total_over(self, which: str, line: float) -> float:
        """Probability a single half produces more than ``line`` goals."""
        return self._half_sum(which, lambda h, a: h + a > line)

    def half_handicap(self, which: str, side: str, line: float) -> float:
        """First-half handicap, pushes excluded from the settled denominator."""

        def covered(h: int, a: int) -> bool:
            return (h + line > a) if side == "home" else (a + line > h)

        def pushed(h: int, a: int) -> bool:
            return (h + line == a) if side == "home" else (a + line == h)

        settled = self._half_sum(which, lambda h, a: not pushed(h, a))
        if settled <= 0:
            return 0.0
        return self._half_sum(which, covered) / settled
    def half_time_result(self, key: str) -> float:
        """Half-time scoreline result: ``home``, ``draw`` or ``away``."""
        if key == "home":
            return self._half_sum("first", lambda h, a: h > a)
        if key == "away":
            return self._half_sum("first", lambda h, a: h < a)
        return self._half_sum("first", lambda h, a: h == a)

    def ht_ft(self, ht: str, ft: str) -> float:
        """Half Time / Full Time: both results on one bet.

        Second-half goals are independent of first-half goals, so the two halves
        are combined by summing over every (HT score, 2nd-half score) pair that
        lands on the required full-time outcome.

        ``pred`` returns True/False, or None when that second half cannot reach
        the required full-time result -- those cells are skipped rather than
        counted as a miss.
        """
        first = self.half_grid("first")
        second = self.half_grid("second")
        total = 0.0
        for h1 in range(MAX_GOALS + 1):
            for a1 in range(MAX_GOALS + 1):
                p1 = first[h1][a1]
                if p1 <= 0:
                    continue
                ht_res = "home" if h1 > a1 else ("away" if h1 < a1 else "draw")
                if ht_res != ht:
                    continue
                for h2 in range(MAX_GOALS + 1):
                    for a2 in range(MAX_GOALS + 1):
                        p2 = second[h2][a2]
                        if p2 <= 0:
                            continue
                        h, a = h1 + h2, a1 + a2
                        ft_res = "home" if h > a else ("away" if h < a else "draw")
                        if ft_res == ft:
                            total += p1 * p2
        return total
    # --- markets built on the 45/55 average split, not on the matrix ---------
    # These two are the only helpers here that do NOT come out of the scoreline
    # grid. They assume goals arrive at a roughly even rate through each half,
    # which is the standard first-order approximation and nothing more. They are
    # published model-only for that reason.

    #: Minute the second half starts.
    SECOND_HALF_START = 45
    #: Length of each half in minutes.
    HALF_LENGTH = 45
    def goals_from_minute(self, minute: float) -> float:
        """Probability at least one goal is scored at or after ``minute``.

        Empty Poisson process over the remaining share of the match: the density
        is flat, so remaining time is the remaining fraction of 90 minutes.
        """
        import math
        remaining = max(0.0, (90.0 - minute) / 90.0)
        total_lambda = (self.home_lambda + self.away_lambda) * remaining
        return 1.0 - math.exp(-total_lambda)

    def goal_in_window(self, start: float, end: float) -> float:
        """Probability at least one goal lands in [start, end) minutes."""
        import math
        if end <= start:
            return 0.0
        lo = max(0.0, start)
        hi = min(90.0, end)
        if hi <= lo:
            return 0.0
        share = (hi - lo) / 90.0
        return 1.0 - math.exp(-(self.home_lambda + self.away_lambda) * share)


def build_matrix(home_lambda: float, away_lambda: float, rho: float) -> Matrix:
    grid = [[0.0] * (MAX_GOALS + 1) for _ in range(MAX_GOALS + 1)]
    total = 0.0
    for h in range(MAX_GOALS + 1):
        ph = _poisson_pmf(h, home_lambda)
        for a in range(MAX_GOALS + 1):
            p = ph * _poisson_pmf(a, away_lambda) * _dc_tau(h, a, home_lambda, away_lambda, rho)
            p = max(p, 1e-12)
            grid[h][a] = p
            total += p
    # renormalise (the tau correction + truncation at MAX_GOALS shift mass)
    if total > 0:
        for h in range(MAX_GOALS + 1):
            for a in range(MAX_GOALS + 1):
                grid[h][a] /= total
    return Matrix(grid=grid, home_lambda=home_lambda, away_lambda=away_lambda)


def _damp(multiplier: float, strength: float) -> float:
    """Pull a rating multiplier toward 1.0 by ``strength`` (0 = none, 1 = off).

    Attack and defence are estimated independently, so multiplying them
    compounds both extremes at once: a strong attack meeting a weak defence
    multiplies twice in the same direction and runs away. Real Dixon-Coles
    fits absorb this in the coefficients; we damp it explicitly.
    """
    return 1.0 + (multiplier - 1.0) * (1.0 - strength)


def expected_goals(
    home_ratings: Ratings,
    away_ratings: Ratings,
    league_avg: float,
    home_advantage: float = 1.15,
) -> tuple[float, float]:
    """Expected goals for each side.

    ``league_avg`` is goals per team per match, so a league-average fixture in
    an average-advantage spot produces ``league_avg`` for each side.
    """
    # Damp the compounding of two independent multipliers (see _damp).
    damp = 0.34
    h_att = _damp(home_ratings.attack, damp)
    h_def = _damp(home_ratings.defence, damp)
    a_att = _damp(away_ratings.attack, damp)
    a_def = _damp(away_ratings.defence, damp)

    lam_home = league_avg * h_att * a_def * home_advantage
    lam_away = league_avg * a_att * h_def / home_advantage
    # Clamp the projected *total*, not each side independently. Clamping each
    # lambda separately lets both sides sit at the cap at once, which produced
    # 4.5-goal projections in a 2.8-goal league. A real fixture's total lives
    # in a much narrower band than the product of two unrestrained multipliers.
    total = lam_home + lam_away
    avg_total = league_avg * 2.0
    max_total = avg_total * 1.45
    min_total = avg_total * 0.55
    if total > max_total:
        scale = max_total / total
        lam_home *= scale
        lam_away *= scale
    elif total < min_total:
        scale = min_total / total
        lam_home *= scale
        lam_away *= scale
    return max(0.15, lam_home), max(0.15, lam_away)


# --------------------------------------------------------------------------
# market assembly
# --------------------------------------------------------------------------

MARKET_DEFS = [
    ("1x2", "Match Result (1X2)"),
    ("ou25", "Total Goals 2.5"),
    ("btts", "Both Teams To Score"),
]

# The Over/Under lines bookmakers publish for football. 2.5 is the main line
# (it keeps its historical "ou25" key so tracked tips and saved predictions
# stay valid); the rest are keyed "ou0_5", "ou1_5", ... and carry their own
# line value.
OU_LINES: list[float] = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]
MAIN_OU_LINE = 2.5

def ou_market_key(line: float) -> str:
    """Stable market key for a line: 2.5 -> ou25, 0.5 -> ou0_5."""
    if line == MAIN_OU_LINE:
        return "ou25"
    return f"ou{str(line).replace('.', '_')}"


def ou_market_name(line: float) -> str:
    return f"Total Goals {line}"

# Aliases accepted when callers ask for a line, so the API can take
# "2.5", "ou25", "over25" or "ou2_5" and mean the same thing.
def resolve_ou_line(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if float(value) in OU_LINES else None
    s = str(value).strip().lower()
    for prefix in ("ou", "over", "under", "total", "o", "u"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.replace("_", ".").lstrip("goals").strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    # Accept the compact key form too: "ou25" -> 25 -> 2.5. This is how market
    # keys are written (ou25, ou0_5), so clients can pass either spelling.
    if f not in OU_LINES and f >= 10 and f / 10 in OU_LINES:
        f = f / 10
    return f if f in OU_LINES else None

def fixture_ou_odds(fixture: Fixture) -> dict[float, tuple[float | None, float | None]]:
    """Return {line: (over_odds, under_odds)} for every line on a fixture.

    2.5 comes from its dedicated columns; the other lines come from
    ``ou_lines_json`` so the schema does not grow a column per line.
    """
    import json
    out: dict[float, tuple[float | None, float | None]] = {
        MAIN_OU_LINE: (fixture.odds_over25, fixture.odds_under25)
    }
    raw = getattr(fixture, "ou_lines_json", None)
    if not raw:
        return out
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return out
    if not isinstance(parsed, dict):
        return out
    for k, v in parsed.items():
        try:
            line = float(k)
        except (ValueError, TypeError):
            continue
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            continue
        out[line] = (v[0], v[1])
    return out


def _blend(model_p: float, market_p: float | None) -> float:
    if market_p is None:
        return model_p
    w = settings.market_blend
    return (1 - w) * model_p + w * market_p


def _make_selection(
    *,
    key: str,
    label: str,
    model_p: float,
    market_p: float | None,
    odd: float | None,
    bookmaker: str | None,
    line: float | None = None,
) -> dict:
    """Build one priced selection from model + market probability."""
    # When the price was generated BY this model rather than taken from a real
    # book, comparing it against the model is circular: edge is ~0 by
    # construction. Edge is then reported as None ("no market to compare"),
    # which is a different statement from 0.0 ("market and model agree"), and the
    # selection cannot be flagged as value. No market, no edge to claim.
    from .pricing import is_model_priced
    self_priced = is_model_priced(bookmaker)
    blended = _blend(model_p, market_p)
    market_raw = None if self_priced else implied_probability(odd)
    edge = (blended - market_raw) if market_raw is not None else None
    sel = {
        "key": key,
        "label": label,
        "probability": round(blended, 4),
        "model_probability": round(model_p, 4),
        "market_probability": round(market_p, 4) if market_p is not None else None,
        "fair_odds": fair_odds(blended),
        "odds": odd,
        "edge": round(edge, 4) if edge is not None else None,
        "value": bool(
            market_raw is not None
            and edge is not None
            and edge >= settings.value_edge_threshold
            and odd
            and odd >= 1.3
        ),
        "bookmaker": bookmaker if odd else None,
        "self_priced": self_priced,
    }
    if line is not None:
        sel["line"] = line
    return sel


def build_markets(
    matrix: Matrix,
    fixture: Fixture,
) -> tuple[list[dict], list[dict], dict]:
    """Return (markets, flat selections, headline stats).

    Every Over/Under line in OU_LINES that the fixture has odds for becomes its
    own market, so the UI can offer the full 0.5-5.5 board rather than just the
    main 2.5 line.
    """
    # --- de-vigged market probabilities
    h, d, a = devig([fixture.odds_home, fixture.odds_draw, fixture.odds_away])
    by, bn = devig([fixture.odds_btts_yes, fixture.odds_btts_no])

    markets: list[dict] = []
    flat: list[dict] = []

    # ---------------- 1X2 ----------------
    onex2 = [
        _make_selection(
            key="home",
            label=f"{fixture.home_team.display_name} win",
            model_p=matrix.home_win(),
            market_p=h,
            odd=fixture.odds_home,
            bookmaker=fixture.bookmaker,
        ),
        _make_selection(
            key="draw",
            label="Draw",
            model_p=matrix.draw(),
            market_p=d,
            odd=fixture.odds_draw,
            bookmaker=fixture.bookmaker,
        ),
        _make_selection(
            key="away",
            label=f"{fixture.away_team.display_name} win",
            model_p=matrix.away_win(),
            market_p=a,
            odd=fixture.odds_away,
            bookmaker=fixture.bookmaker,
        ),
    ]
    name_1x2 = "Match Result (1X2)"
    markets.append({"key": "1x2", "name": name_1x2, "selections": onex2})
    flat += [{"market": "1x2", "market_name": name_1x2, **s} for s in onex2]

    # ---------------- Over / Under: every line the fixture prices ----------------
    available = fixture_ou_odds(fixture)
    for line in OU_LINES:
        over_odd, under_odd = available.get(line, (None, None))
        if over_odd is None and under_odd is None:
            continue
        m_key = ou_market_key(line)
        m_name = ou_market_name(line)
        over_p = matrix.over(line)
        o_mp, u_mp = devig([over_odd, under_odd])
        sels = [
            _make_selection(
                key="over",
                label=f"Over {line} goals",
                model_p=over_p,
                market_p=o_mp,
                odd=over_odd,
                bookmaker=fixture.bookmaker,
                line=line,
            ),
            _make_selection(
                key="under",
                label=f"Under {line} goals",
                model_p=1.0 - over_p,
                market_p=u_mp,
                odd=under_odd,
                bookmaker=fixture.bookmaker,
                line=line,
            ),
        ]
        markets.append({"key": m_key, "name": m_name, "selections": sels})
        flat += [{"market": m_key, "market_name": m_name, **s} for s in sels]

    # ---------------- BTTS ----------------
    btts = [
        _make_selection(
            key="yes",
            label="BTTS: Yes",
            model_p=matrix.btts(),
            market_p=by,
            odd=fixture.odds_btts_yes,
            bookmaker=fixture.bookmaker,
        ),
        _make_selection(
            key="no",
            label="BTTS: No",
            model_p=1.0 - matrix.btts(),
            market_p=bn,
            odd=fixture.odds_btts_no,
            bookmaker=fixture.bookmaker,
        ),
    ]
    name_btts = "Both Teams To Score"
    markets.append({"key": "btts", "name": name_btts, "selections": btts})
    flat += [{"market": "btts", "market_name": name_btts, **s} for s in btts]

    # ---------------- derived markets ----------------
    # These are exact sums over the same scoreline matrix, so they are genuine
    # model probabilities. The seed feed carries no book prices for them, so the
    # selections are emitted model-only: no odd, no market probability, and edge
    # stays None rather than being asserted against a price that does not exist.
    def add(
        key: str,
        name: str,
        selections: list[tuple],
    ) -> None:
        """Register a derived market.

        Each selection is ``(key, label, probability)`` or, for a lined market,
        ``(key, label, probability, line)``. The line is carried through onto the
        selection because settlement needs the number, not the label: a handicap
        pick cannot be graded from ``"Brighton +1"`` without parsing the string
        back into a float, and a label is display text that is free to change.
        Omitting it here was a real bug -- Handicap selections shipped with
        ``line: None``, so anything keying off the line silently saw nothing.
        """
        sels = []
        for entry in selections:
            skey, slabel, prob = entry[0], entry[1], entry[2]
            line = entry[3] if len(entry) > 3 else None
            # ``line`` is only passed when the market really has one. Passing
            # None would have _make_selection attach ``"line": None`` to every
            # selection, which reads as "this market has a line" downstream.
            extra = {"line": line} if line is not None else {}
            sels.append(
                _make_selection(
                    key=skey,
                    label=slabel,
                    model_p=prob,
                    market_p=None,
                    odd=None,
                    bookmaker=fixture.bookmaker,
                    **extra,
                )
            )
        markets.append({"key": key, "name": name, "selections": sels})
        flat.extend({"market": key, "market_name": name, **s} for s in sels)

    home = fixture.home_team.display_name
    away = fixture.away_team.display_name
    add(
        "dc",
        "Double Chance",
        [
            ("1x", f"{home} or Draw", matrix.double_chance(("home", "draw"))),
            ("12", f"{home} or {away}", matrix.double_chance(("home", "away"))),
            ("x2", f"Draw or {away}", matrix.double_chance(("draw", "away"))),
        ],
    )

    # Double Chance - 1UP is deliberately not published. A 1UP market pays out
    # early, but its pre-match probability is identical to plain Double Chance,
    # so listing both shows the same number twice and implies a distinction the
    # model cannot make.

    add(
        "dnb",
        "Draw No Bet",
        [
            ("home", f"{home}", matrix.draw_no_bet("home")),
            ("away", f"{away}", matrix.draw_no_bet("away")),
        ],
    )

    add(
        "win_to_nil",
        "Win To Nil",
        [
            ("home", home, matrix.win_to_nil("home")),
            ("away", away, matrix.win_to_nil("away")),
        ],
    )

    add(
        "win_margin",
        "Winning Margin",
        [
            ("home_1", f"{home} by 1", matrix.win_margin("home", 1)),
            ("home_2", f"{home} by 2", matrix.win_margin("home", 2)),
            ("home_3", f"{home} by 3+", 1.0 - matrix.win_margin("home", 1) - matrix.win_margin("home", 2) - matrix.draw() - matrix.away_win()),
            ("away_1", f"{away} by 1", matrix.win_margin("away", 1)),
            ("away_2", f"{away} by 2", matrix.win_margin("away", 2)),
            ("away_3", f"{away} by 3+", 1.0 - matrix.win_margin("away", 1) - matrix.win_margin("away", 2) - matrix.draw() - matrix.home_win()),
        ],
    )

    # Full-goal Handicap: the side wins once its head start is applied. Whole-goal
    # lines only, because those are the lines the engine can settle exactly -- a
    # half line never pushes, and a quarter line splits the stake, neither of
    # which a single scoreline grid expresses. Pushes are excluded from the
    # denominator inside Matrix.handicap(), exactly as Draw No Bet excludes draws.
    add(
        "handicap",
        "Handicap",
        [
            (
                f"home_{str(line).replace('.', '_').replace('-', 'm')}",
                f"{home} {line:+g}",
                matrix.handicap("home", line),
                line,
            )
            for line in HANDICAP_LINES
        ]
        + [
            (
                f"away_{str(line).replace('.', '_').replace('-', 'm')}",
                f"{away} {line:+g}",
                matrix.handicap("away", line),
                line,
            )
            for line in HANDICAP_LINES
        ],
    )

    # Corners are deliberately not modelled. A Dixon-Coles matrix is fitted to
    # GOALS; a corner count is driven by tempo, style and game state, which this
    # seed data does not record (no fixture row carries a corner total). Any
    # number produced here would be invented rather than derived, so the
    # category stays in the taxonomy unmodeled.

    # Never Down cannot be derived from a full-time scoreline matrix: it needs
    # the in-play path, not the final result. It is listed in the taxonomy as a
    # category the book offers, but it is deliberately not priced here.

    add(
        "team_goals",
        "Team Goals",
        [
            ("home_0", f"{home} 0 goals", 1.0 - matrix.team_over("home", 0)),
            ("home_1", f"{home} 1 goal", matrix.team_over("home", 0) - matrix.team_over("home", 1)),
            ("home_2", f"{home} 2 goals", matrix.team_over("home", 1) - matrix.team_over("home", 2)),
            ("home_3", f"{home} 3+ goals", matrix.team_over("home", 2)),
            ("away_0", f"{away} 0 goals", 1.0 - matrix.team_over("away", 0)),
            ("away_1", f"{away} 1 goal", matrix.team_over("away", 0) - matrix.team_over("away", 1)),
            ("away_2", f"{away} 2 goals", matrix.team_over("away", 1) - matrix.team_over("away", 2)),
            ("away_3", f"{away} 3+ goals", matrix.team_over("away", 2)),
        ],
    )

    add(
        "clean_sheet",
        "Clean Sheet",
        [
            ("home", f"{home} clean sheet", matrix.clean_sheet("home")),
            ("away", f"{away} clean sheet", matrix.clean_sheet("away")),
        ],
    )

    add(
        "odd_even",
        "Odd/Even Goals",
        [
            ("odd", "Odd total goals", matrix.odd_even("odd")),
            ("even", "Even total goals", matrix.odd_even("even")),
        ],
    )

    add(
        "goals_range",
        "Total Goals Range",
        [
            ("0_1", "0-1 goals", matrix.total_goals_in_range(0, 1)),
            ("2_3", "2-3 goals", matrix.total_goals_in_range(2, 3)),
            ("4_5", "4-5 goals", matrix.total_goals_in_range(4, 5)),
            ("6p", "6+ goals", matrix.total_goals_in_range(6, 99)),
        ],
    )

    add(
        "highest_half",
        "Highest Scoring Half",
        [
            ("first", "1st half", matrix.highest_scoring_half("first")),
            ("second", "2nd half", matrix.highest_scoring_half("second")),
            ("equal", "Equal", matrix.highest_scoring_half("equal")),
        ],
    )

    # Correct score: every grid cell the model can resolve, most likely first.
    add(
        "correct_score",
        "Correct Score",
        [
            (f"cs_{c['home_goals']}_{c['away_goals']}", f"{c['home_goals']}-{c['away_goals']}", c["probability"])
            for c in matrix.top_scorelines(12)
        ],
    )

    # ---------------- halves and time windows ----------------
    # Everything below leans on an assumption the results history does not
    # measure (a 45/55 goal split across the halves, or a flat goal rate), so it
    # is published model-only: no odds, market probability stays None and edge
    # stays None rather than being asserted against a price that does not exist.

    add(
        "1x2_1up",
        "1X2 - 1UP",
        [
            ("home", f"{home} win", matrix.home_win()),
            ("draw", "Draw", matrix.draw()),
            ("away", f"{away} win", matrix.away_win()),
        ],
    )

    add(
        "1x2_2up",
        "1X2 - 2UP",
        [
            ("home", f"{home} win", matrix.home_win()),
            ("draw", "Draw", matrix.draw()),
            ("away", f"{away} win", matrix.away_win()),
        ],
    )

    add(
        "ou_1st_half",
        "1st Half O/U",
        [
            ("over_0_5", "Over 0.5 first half", matrix.half_total_over("first", 0.5)),
            ("under_0_5", "Under 0.5 first half", 1.0 - matrix.half_total_over("first", 0.5)),
            ("over_1_5", "Over 1.5 first half", matrix.half_total_over("first", 1.5)),
            ("under_1_5", "Under 1.5 first half", 1.0 - matrix.half_total_over("first", 1.5)),
            ("over_2_5", "Over 2.5 first half", matrix.half_total_over("first", 2.5)),
            ("under_2_5", "Under 2.5 first half", 1.0 - matrix.half_total_over("first", 2.5)),
        ],
    )

    add(
        "handicap_1st_half",
        "1st Half - Handicap",
        [
            (
                f"home_{str(line).replace('.', '_')}",
                f"{home} {line:+g} first half",
                matrix.half_handicap("first", "home", line),
            )
            for line in (-1.0, -0.5, 0.0, 0.5, 1.0)
        ]
        + [
            (
                f"away_{str(line).replace('.', '_')}",
                f"{away} {line:+g} first half",
                matrix.half_handicap("first", "away", line),
            )
            for line in (-1.0, -0.5, 0.0, 0.5, 1.0)
        ],
    )

    add(
        "ht_ft",
        "Half Time / Full Time",
        [
            (f"{ht}_{ft}", f"{ht_label} / {ft_label}", matrix.ht_ft(ht, ft))
            for ht, ht_label in (("home", home), ("draw", "Draw"), ("away", away))
            for ft, ft_label in (("home", home), ("draw", "Draw"), ("away", away))
        ],
    )

    add(
        "dc_btts",
        "Double Chance & GG/NG",
        [
            ("1x_gg", f"{home} or Draw & GG", matrix.double_chance(("home", "draw")) * matrix.btts()),
            ("12_gg", f"{home} or {away} & GG", matrix.double_chance(("home", "away")) * matrix.btts()),
            ("x2_gg", f"Draw or {away} & GG", matrix.double_chance(("draw", "away")) * matrix.btts()),
        ],
    )

    add(
        "dc_ou25",
        "Double Chance & O/U",
        [
            ("1x_over", f"{home} or Draw & Over 2.5", matrix.double_chance(("home", "draw")) * matrix.over(2.5)),
            ("12_over", f"{home} or {away} & Over 2.5", matrix.double_chance(("home", "away")) * matrix.over(2.5)),
            ("x2_over", f"Draw or {away} & Over 2.5", matrix.double_chance(("draw", "away")) * matrix.over(2.5)),
            ("1x_under", f"{home} or Draw & Under 2.5", matrix.double_chance(("home", "draw")) * (1.0 - matrix.over(2.5))),
            ("12_under", f"{home} or {away} & Under 2.5", matrix.double_chance(("home", "away")) * (1.0 - matrix.over(2.5))),
            ("x2_under", f"Draw or {away} & Under 2.5", matrix.double_chance(("draw", "away")) * (1.0 - matrix.over(2.5))),
        ],
    )

    add(
        "1x2_ou25",
        "1X2 & O/U",
        [
            ("home_over", f"{home} & Over 2.5", matrix.home_win() * matrix.over(2.5)),
            ("draw_over", f"Draw & Over 2.5", matrix.draw() * matrix.over(2.5)),
            ("away_over", f"{away} & Over 2.5", matrix.away_win() * matrix.over(2.5)),
            ("home_under", f"{home} & Under 2.5", matrix.home_win() * (1.0 - matrix.over(2.5))),
            ("draw_under", f"Draw & Under 2.5", matrix.draw() * (1.0 - matrix.over(2.5))),
            ("away_under", f"{away} & Under 2.5", matrix.away_win() * (1.0 - matrix.over(2.5))),
        ],
    )

    add(
        "goal_window",
        "1X2 From x to x Minutes",
        [
            ("goal_0_15", "Goal in minutes 1-15", matrix.goal_in_window(0.0, 15.0)),
            ("goal_15_30", "Goal in minutes 16-30", matrix.goal_in_window(15.0, 30.0)),
            ("goal_30_45", "Goal in minutes 31-45", matrix.goal_in_window(30.0, 45.0)),
            ("goal_45_60", "Goal in minutes 46-60", matrix.goal_in_window(45.0, 60.0)),
            ("goal_60_75", "Goal in minutes 61-75", matrix.goal_in_window(60.0, 75.0)),
            ("goal_75_90", "Goal in minutes 76-90", matrix.goal_in_window(75.0, 90.0)),
        ],
    )

    over25 = available.get(MAIN_OU_LINE, (None, None))
    o25_mp, u25_mp = devig([over25[0], over25[1]])
    headline = {
        "home_win": round(_blend(matrix.home_win(), h), 4),
        "draw": round(_blend(matrix.draw(), d), 4),
        "away_win": round(_blend(matrix.away_win(), a), 4),
        "btts": round(_blend(matrix.btts(), by), 4),
        "over25": round(_blend(matrix.over(MAIN_OU_LINE), o25_mp), 4),
        "under25": round(_blend(1.0 - matrix.over(MAIN_OU_LINE), u25_mp), 4),
    }
    return markets, flat, headline


#: The five markets a reader is shown as "possible accurate predictions", each
#: contributing exactly one best selection. This is a curated shortlist rather
#: than "the five highest probabilities", because the two are not the same thing:
#:
#:   * Ranking every selection by probability would return five Over/Under or
#:     Handicap lines, since the longest lines are the most certain. That is the
#:     same degeneracy the headline pool guards against, and it would say nothing
#:     about the match.
#:   * Correct Score is the counter-example. Its *best* selection is the single
#:     most likely scoreline, which is typically only ~11-13%. It cannot compete
#:     on probability with any other market, yet it is one of the five things a
#:     reader most wants to see.
#:
#: So each market contributes its own strongest selection and the five are
#: presented side by side, each carrying its own honest probability. The reader
#: can see for themselves that Correct Score is a long shot and Under 5.5 is not.
TOP_FIVE_MARKETS: tuple[tuple[str, str], ...] = (
    ("1x2", "Match Result"),
    ("ou", "Over/Under"),
    ("btts", "Both Teams To Score"),
    ("correct_score", "Correct Score"),
    ("handicap", "Handicap"),
)

#: Every whole-goal handicap line the engine prices. Whole goals only, because
#: those are the lines a single scoreline grid can settle exactly: a half line
#: never pushes and a quarter line splits the stake, and neither is expressible
#: as one cell-by-cell sum.
HANDICAP_LINES: tuple[float, ...] = (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0)

#: Handicap lines the five-way shortlist may choose from. The extreme lines
#: (-3/+3) are near-certainties that would make the panel useless -- exactly the
#: degeneracy the headline pool guards against -- so the shortlist is drawn from
#: the competitive middle of the same board.
TOP_FIVE_HANDICAP_LINES: tuple[float, ...] = (-1.0, 0.0, 1.0)

#: Over/Under lines eligible for the shortlist. 2.5 is the market's main line but
#: is not always the model's most confident read, so the neighbouring lines of
#: the same board compete for the slot.
TOP_FIVE_OU_LINES: tuple[float, ...] = (1.5, 2.5, 3.5, 4.5, 5.5)


def build_top_five(markets: list[dict]) -> list[dict]:
    """One best selection per headline market, for the five-way shortlist.

    Returns a list of ``{market, market_name, label, probability, ...}`` entries
    in the fixed order of :data:`TOP_FIVE_MARKETS`. A market that cannot be
    priced for this fixture is omitted rather than filled with a guess, so the
    panel is never padded to a false five.
    """
    by_key = {m["key"]: m for m in markets}
    out: list[dict] = []

    for family, display_name in TOP_FIVE_MARKETS:
        candidates: list[dict] = []

        if family == "ou":
            for line in TOP_FIVE_OU_LINES:
                mk = by_key.get(ou_market_key(line))
                if mk:
                    candidates.extend(mk["selections"])
        elif family == "correct_score":
            mk = by_key.get("correct_score")
            if mk:
                # The most likely scoreline is the *first* selection by
                # construction (top_scorelines is sorted), but it is selected by
                # probability here so the panel does not depend on that ordering
                # staying true.
                candidates.extend(mk["selections"])
        elif family == "handicap":
            mk = by_key.get("handicap")
            if mk:
                candidates.extend(
                    s for s in mk["selections"] if s.get("line") in TOP_FIVE_HANDICAP_LINES
                )
        else:
            mk = by_key.get(family)
            if mk:
                candidates.extend(mk["selections"])

        # Drop anything that cannot honestly be called a prediction: a 0% or a
        # 100% selection is arithmetic, not a call.
        usable = [s for s in candidates if 0.0 < s["probability"] < 1.0]
        if not usable:
            continue
        best = max(usable, key=lambda s: s["probability"])
        out.append(
            {
                "market": family,
                "market_name": display_name,
                # The concrete market the selection came from. For Over/Under and
                # Handicap that is a specific line (``ou5_5``, ``handicap``), so a
                # grader can settle the pick off the stable key rather than off
                # the display label.
                "market_key": (
                    ou_market_key(best["line"])
                    if family == "ou" and best.get("line") is not None
                    else family
                ),
                "label": best["label"],
                "selection_key": best["key"],
                "line": best.get("line"),
                "probability": best["probability"],
                "model_probability": best["model_probability"],
                "fair_odds": best["fair_odds"],
                "odds": best["odds"],
                "edge": best["edge"],
                "value": best["value"],
                "self_priced": best["self_priced"],
            }
        )

    return out

def confidence_score(
    best_probability: float,
    edge: float,
    sample_home: int,
    sample_away: int,
    disagreement: float,
) -> int:
    """0-100 confidence blending likelihood, edge, data volume and model/market
    agreement. Deliberately conservative: a 0.60 chance is not 'certain'."""
    prob_component = (best_probability - 0.33) / (0.85 - 0.33)  # 0 at coin-flip
    prob_component = max(0.0, min(1.0, prob_component))

    edge_component = max(0.0, min(1.0, (edge + 0.02) / 0.20))
    sample = min(sample_home, sample_away)
    sample_component = max(0.0, min(1.0, sample / 15.0))
    agreement_component = max(0.0, min(1.0, 1.0 - disagreement / 0.25))

    score = (
        prob_component * 46
        + edge_component * 14
        + sample_component * 22
        + agreement_component * 18
    )
    return int(max(5, min(95, round(score))))


def confidence_label(score: int) -> str:
    if score >= 78:
        return "Very high"
    if score >= 64:
        return "High"
    if score >= 50:
        return "Medium"
    if score >= 36:
        return "Low"
    return "Very low"


def value_rating(edge: float, confidence: int) -> int:
    """1-5 star rating combining edge and confidence."""
    raw = edge * 100 * 0.6 + confidence * 0.06
    return int(max(1, min(5, round(raw))))


def head_to_head(db: Session, home_id: int, away_id: int, limit: int = 6) -> dict:
    fixtures = db.scalars(
        select(Fixture)
        .where(
            Fixture.status == "finished",
            Fixture.home_goals.is_not(None),
            (
                ((Fixture.home_team_id == home_id) & (Fixture.away_team_id == away_id))
                | ((Fixture.home_team_id == away_id) & (Fixture.away_team_id == home_id))
            ),
        )
        .order_by(Fixture.kickoff.desc())
        .limit(limit)
    ).all()

    home_wins = draws = away_wins = 0
    goals_home = goals_away = 0
    results = []
    for fx in fixtures:
        hg, ag = fx.home_goals or 0, fx.away_goals or 0
        if fx.home_team_id == home_id:
            goals_home += hg
            goals_away += ag
            if hg > ag:
                home_wins += 1
            elif hg == ag:
                draws += 1
            else:
                away_wins += 1
        else:
            goals_home += ag
            goals_away += hg
            if ag > hg:
                home_wins += 1
            elif ag == hg:
                draws += 1
            else:
                away_wins += 1
        results.append(
            {
                "kickoff": fx.kickoff.isoformat(),
                "home_team_id": fx.home_team_id,
                "away_team_id": fx.away_team_id,
                "home_goals": hg,
                "away_goals": ag,
            }
        )

    return {
        "played": len(fixtures),
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "goals_home": goals_home,
        "goals_away": goals_away,
        "avg_total_goals": round((goals_home + goals_away) / len(fixtures), 2) if fixtures else 0.0,
        "results": results,
    }


def key_factors_and_risks(
    home_stats: TeamStats,
    away_stats: TeamStats,
    home_ratings: Ratings,
    away_ratings: Ratings,
    matrix: Matrix,
    h2h: dict,
    home_name: str,
    away_name: str,
) -> tuple[list[str], list[str]]:
    factors: list[str] = []
    risks: list[str] = []

    if home_stats.played and away_stats.played:
        if home_ratings.attack > away_ratings.attack * 1.15:
            factors.append(
                f"{home_name} carry the stronger attack "
                f"({home_stats.goals_for / max(home_stats.played,1):.2f} goals per game "
                f"vs {away_stats.goals_for / max(away_stats.played,1):.2f})."
            )
        elif away_ratings.attack > home_ratings.attack * 1.15:
            factors.append(
                f"{away_name} are the more productive side going forward "
                f"({away_stats.goals_for / max(away_stats.played,1):.2f} goals per game)."
            )

        if home_stats.home_ppg and away_stats.away_ppg:
            if home_stats.home_ppg > away_stats.away_ppg + 0.5:
                factors.append(
                    f"Strong home record: {home_name} average {home_stats.home_ppg:.2f} points "
                    f"per home game against {away_name}'s {away_stats.away_ppg:.2f} away."
                )
            elif away_stats.away_ppg > home_stats.home_ppg + 0.5:
                factors.append(
                    f"{away_name} travel well — {away_stats.away_ppg:.2f} points per away game "
                    f"is better than {home_name}'s {home_stats.home_ppg:.2f} at home."
                )

        total_form = (home_stats.over25_rate + away_stats.over25_rate) / 2
        if total_form >= 0.60:
            factors.append(
                f"Goals are likely: {total_form * 100:.0f}% of these teams' matches went over 2.5."
            )
        elif total_form <= 0.35:
            factors.append(
                f"Low-scoring profile: only {total_form * 100:.0f}% of their combined matches "
                f"finished over 2.5 goals."
            )

        btts_rate = (home_stats.btts_rate + away_stats.btts_rate) / 2
        if btts_rate >= 0.60:
            factors.append(
                f"Both teams score frequently — {btts_rate * 100:.0f}% BTTS rate across the two sides."
            )

        if home_stats.clean_sheets >= max(2, home_stats.played // 3):
            factors.append(
                f"{home_name} have kept {home_stats.clean_sheets} clean sheets in "
                f"{home_stats.played} matches."
            )
    else:
        risks.append("Limited recent match data for one or both teams — ratings are prior-driven.")

    if h2h.get("played"):
        factors.append(
            f"Head-to-head: {h2h['home_wins']}-{h2h['draws']}-{h2h['away_wins']} across the last "
            f"{h2h['played']} meetings, averaging {h2h['avg_total_goals']:.2f} goals."
        )

    factors.append(
        f"Model expects {matrix.home_lambda:.2f} – {matrix.away_lambda:.2f} goals "
        f"({matrix.home_lambda + matrix.away_lambda:.2f} total)."
    )

    top = matrix.top_scorelines(1)[0]
    risks.append(
        f"The single most likely scoreline ({top['home_goals']}-{top['away_goals']}) still only "
        f"carries {top['probability'] * 100:.1f}% probability — football is low-information."
    )
    if min(home_stats.played, away_stats.played) < 6:
        risks.append("Small sample size; treat the confidence rating as provisional.")
    if abs(matrix.home_win() - matrix.away_win()) < 0.06:
        risks.append("The market is close to a coin flip — consider a cautious stake or no bet.")
    risks.append("Odds move; re-check prices before placing and never stake more than you can lose.")

    return factors, risks


# --------------------------------------------------------------------------
# top-level entry point
# --------------------------------------------------------------------------

def predict_fixture(db: Session, fixture: Fixture, include_context: bool = True) -> dict:
    """Run the full prediction pipeline for one fixture."""
    league = fixture.competition.avg_goals or settings.league_avg_goals
    home_stats = collect_team_stats(db, fixture.home_team_id)
    away_stats = collect_team_stats(db, fixture.away_team_id)
    home_stats.name = fixture.home_team.display_name
    away_stats.name = fixture.away_team.display_name

    home_ratings = estimate_ratings(home_stats, league)
    away_ratings = estimate_ratings(away_stats, league)

    lam_h, lam_a = expected_goals(home_ratings, away_ratings, league)
    matrix = build_matrix(lam_h, lam_a, settings.dixon_coles_rho)

    markets, flat, headline = build_markets(matrix, fixture)

    # Pick the headline tip: the best call the model is actually willing to
    # stand behind.
    #
    # The candidate pool is restricted to markets where the model has an
    # INDEPENDENT price to be right or wrong against. That is the three markets
    # read straight off the scoreline grid and priced by a real book -- 1X2, the
    # Over/Under lines and BTTS. The derived markets (1UP, Correct Score, HT/FT,
    # and especially Handicap) are legitimate to publish, but they must never be
    # the headline: a +3 handicap is ~99% likely and would win the "highest
    # probability" contest on every single fixture, so the front page would read
    # "Team +3" for every match and the genuinely predictive calls would be
    # buried. Handicaps are the worst case because the further the line, the more
    # certain it is -- the model gets most confident exactly where it is least
    # informative.
    # Named explicitly rather than matched by prefix: "ou".startswith() would also
    # pull in ou_1st_half and the ou-market halves of the combo markets, which are
    # just as one-sided as the handicap lines and would take the headline on every
    # fixture instead.
    #
    # Restricted to the *standard* Over/Under board -- 0.5 to 3.5. Lines past 3.5
    # are excluded from the headline entirely, not merely bounded by the ceiling
    # below, because the scoring function is monotonic in probability: given the
    # choice, "Under 4.5" (0.80) always outranks "Man City win" (0.75), and a
    # ceiling set low enough to exclude it (0.70) would also exclude most genuine
    # 1X2 calls. The 4.5 and 5.5 lines remain fully priced and published on the
    # fixture page and in the five-way shortlist above; they are simply not
    # headline material, because no tipster publishes "Under 4.5 goals" as a tip.
    HEADLINE_MARKETS = {
        "1x2",
        "btts",
        "ou25",
        "ou0_5",
        "ou1_5",
        "ou3_5",
    }
    candidates = [s for s in flat if s.get("market") in HEADLINE_MARKETS]
    # Prefer a genuine call over an unbackable short price. A ceiling matters as
    # much as the floor: the model is most certain about the most trivial lines,
    # so without an upper bound the front page fills with near-certainties that
    # say nothing about the match. Above the ceiling a tip is no longer a
    # prediction, it is a formality.
    #
    # The ceiling is 0.82, not 0.92. At 0.92 it did not do the job it was
    # written to do: Under 5.5 goals prices around 0.89 for an ordinary fixture,
    # so it slipped under the old bound and took the headline on essentially
    # every match -- the exact failure the comment above describes. The line
    # that makes a fixture interesting is Over/Under 2.5, and everything past
    # 3.5 is a formality for a normal goal expectation.
    CEILING = 0.82
    priced = [s for s in candidates if s["value"] or 0.50 <= s["probability"] <= CEILING]
    # Falling back to the *unfiltered* candidate list would reinstate every
    # near-certainty the ceiling just removed, so the fallback relaxes the floor
    # instead and keeps the ceiling. Only if nothing clears the ceiling at all
    # -- a pathological fixture -- is the raw list used.
    relaxed = [s for s in candidates if s["probability"] <= CEILING]
    pool = priced or relaxed or candidates or flat
    best = max(
        pool,
        # edge may be None on a self-priced fixture, where there is no market to
        # beat; treat it as "no bonus" rather than crashing on the comparison.
        key=lambda s: s["probability"] * 0.75 + max(0.0, s["edge"] or 0.0) * 2.5,
    )

    model_p = best["model_probability"]
    market_p = best["market_probability"]
    disagreement = abs(model_p - market_p) if market_p is not None else 0.0

    conf = confidence_score(
        best_probability=best["probability"],
        edge=best["edge"] or 0.0,
        sample_home=home_stats.played,
        sample_away=away_stats.played,
        disagreement=disagreement,
    )

    h2h = head_to_head(db, fixture.home_team_id, fixture.away_team_id) if include_context else {}
    factors, risks = key_factors_and_risks(
        home_stats,
        away_stats,
        home_ratings,
        away_ratings,
        matrix,
        h2h,
        fixture.home_team.display_name,
        fixture.away_team.display_name,
    )

    result: dict = {
        "fixture_id": fixture.id,
        "generated_at": datetime.now(timezone.utc),
        "engine": "Dixon-Coles Poisson with market blending",
        "model_version": MODEL_VERSION,
        **headline,
        "expected_home_goals": round(lam_h, 2),
        "expected_away_goals": round(lam_a, 2),
        "expected_total_goals": round(lam_h + lam_a, 2),
        "best_pick": f"{fixture.home_team.display_name} vs {fixture.away_team.display_name} — {best['label']}",
        "best_market": best["market"],
        "best_selection": best["label"],
        # Stable identifiers for grading the pick against a final score. The
        # label is for display only; the key/line are what the results page
        # uses so a re-worded label can never silently break settlement.
        "best_selection_key": best["key"],
        "best_line": best.get("line"),
        "best_probability": best["probability"],
        "best_odds": best["odds"],
        # None on a self-priced fixture: there is no independent market, so no
        # edge exists to report. Downstream code must treat None as unknown.
        "best_edge": best["edge"],
        "best_self_priced": bool(best.get("self_priced")),
        "confidence": conf,
        "confidence_label": confidence_label(conf),
        "value_rating": value_rating(best["edge"] or 0.0, conf),
        "markets": markets,
        # The five-market shortlist shown as "possible accurate predictions".
        # Separate from the headline pick above: the headline is the single best
        # call the model will stand behind, whereas this is one selection from
        # each of the five markets a reader asks about, whatever its confidence.
        "top_picks": build_top_five(markets),
        "top_scorelines": matrix.top_scorelines(6),
        "key_factors": factors,
        "risk_notes": risks,
        "ai_summary": None,
    }

    if include_context:
        result["home_form"] = home_stats.to_form_entry()
        result["away_form"] = away_stats.to_form_entry()
        result["head_to_head"] = h2h
        # league position if available
        for side, team_id, key in (
            ("home", fixture.home_team_id, "home_form"),
            ("away", fixture.away_team_id, "away_form"),
        ):
            standing = db.scalars(
                select(Standing).where(
                    Standing.team_id == team_id,
                    Standing.competition_id == fixture.competition_id,
                )
            ).first()
            if standing:
                result[key]["position"] = standing.position

    return result


def flat_selections(prediction: dict) -> list[dict]:
    out = []
    for m in prediction["markets"]:
        for s in m["selections"]:
            out.append({"market": m["key"], "market_name": m["name"], **s})
    return out

def selection_hit(market: str, selection_key: str, home_goals: int, away_goals: int,
                  line: float | None = None) -> bool | None:
    """Did one model selection win, given the final score?

    Returns None when the market is one the engine does not price (so the
    caller never reports a false hit/miss). Mirrors ``apply_result_to_tips``
    but keys off the selection's stable ``key`` rather than its display label.
    """
    total = home_goals + away_goals
    if market == "1x2":
        if selection_key == "home":
            return home_goals > away_goals
        if selection_key == "draw":
            return home_goals == away_goals
        if selection_key == "away":
            return away_goals > home_goals
        return None
    if market == "btts":
        both = home_goals > 0 and away_goals > 0
        if selection_key == "yes":
            return both
        if selection_key == "no":
            return not both
        return None
    # Every other priced market is an Over/Under line, keyed ouX_Y.
    if market.startswith("ou"):
        resolved = line if line is not None else resolve_ou_line(market)
        if resolved is None:
            return None
        if selection_key == "over":
            return total > resolved
        if selection_key == "under":
            return total < resolved
    return None

def grade_prediction(prediction: dict, home_goals: int, away_goals: int) -> dict:
    """Grade a full prediction against a final score.

    Returns the headline pick's outcome plus a per-market breakdown, so the
    results page can tick every selection the model published, not just the
    headline tip.
    """
    graded_markets: list[dict] = []
    for m in prediction.get("markets", []):
        sels = []
        for s in m.get("selections", []):
            hit = selection_hit(m["key"], s.get("key", ""), home_goals, away_goals,
                                s.get("line"))
            sels.append({**s, "won": hit})
        graded_markets.append({**m, "selections": sels})

    headline_hit = selection_hit(
        prediction.get("best_market", ""),
        prediction.get("best_selection_key", ""),
        home_goals,
        away_goals,
        prediction.get("best_line"),
    )
    # Fall back to matching the headline label against the graded 1X2/O-U/BTTS
    # rows when no stable key was stored (e.g. a cached older prediction).
    if headline_hit is None:
        for m in graded_markets:
            if m["key"] != prediction.get("best_market"):
                continue
            for s in m["selections"]:
                if s.get("label") == prediction.get("best_selection"):
                    headline_hit = s.get("won")
                    break
    correct = sum(1 for m in graded_markets for s in m["selections"] if s.get("won"))
    graded = sum(1 for m in graded_markets for s in m["selections"] if s.get("won") is not None)
    return {
        "headline_hit": headline_hit,
        "markets": graded_markets,
        "selections_correct": correct,
        "selections_graded": graded,
    }


def apply_result_to_tips(db: Session, fixture: Fixture) -> int:
    """Settle tracked tips once a fixture is finished. Returns tips updated."""
    from .models import TrackedTip

    if not fixture.is_finished:
        return 0
    hg, ag = fixture.home_goals or 0, fixture.away_goals or 0
    total = hg + ag
    tips = db.scalars(
        select(TrackedTip).where(
            TrackedTip.fixture_id == fixture.id, TrackedTip.status == "pending"
        )
    ).all()

    updated = 0
    for tip in tips:
        sel = tip.selection.lower()
        won: bool | None = None
        if tip.market == "1x2":
            if "draw" == sel:
                won = hg == ag
            elif "win" in sel:
                home_name = fixture.home_team.display_name.lower()
                won = (hg > ag) if home_name.split()[0] in sel else (ag > hg)
        elif tip.market == "ou25":
            won = (total > 2.5) if "over" in sel else (total < 2.5)
        elif tip.market == "btts":
            both = hg > 0 and ag > 0
            won = both if "yes" in sel else (not both)

        if won is None:
            continue
        tip.status = "won" if won else "lost"
        tip.profit = round(tip.stake * (tip.odds - 1), 2) if won else -tip.stake
        updated += 1

    if updated:
        db.commit()
    return updated

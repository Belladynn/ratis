"""
Domain exceptions for ratis_rewards repositories.

All repository-level business exceptions live here to avoid circular imports
between repositories that need to raise and catch each other's exceptions.

Existing code can still import from the original module — each module re-exports
its own exceptions for backward compatibility.
"""


class InsufficientBalance(Exception):
    """Raised by CAB operations when the user's balance is too low."""


class InsufficientCashbackBalance(Exception):
    """Raised by cashback operations when the user's cashback balance is too low."""


class StreakNeedsRepair(Exception):
    """Raised by feed_jack when gap=1 and food_reserves=0 (use /streak/repair instead)."""


class StreakNotInRepairState(Exception):
    """Raised by repair_streak when the streak is not in needs_repair state."""


class ReserveLimitExceeded(Exception):
    """Raised when purchasing reserves would exceed max_food_reserves."""


class MilestoneNotFound(Exception):
    """Raised when a milestone_id doesn't belong to an active challenge."""


class MilestoneLocked(Exception):
    """Raised when the community challenge progress hasn't reached the milestone threshold."""


class MilestoneAlreadyClaimed(Exception):
    """Raised when the user has already claimed this milestone."""


class ChallengeExpired(Exception):
    """Raised when the challenge is past ends_at + grace_period_days (claims closed)."""


class ActiveChallengeConflict(Exception):
    """Raised when trying to activate a challenge while another is already active."""


class ChallengeNotFound(Exception):
    """Raised by admin operations when the challenge_id doesn't exist."""


class WithdrawalNotFound(Exception):
    """Raised when a cashback_withdrawals row cannot be found by ID."""


class SeasonNotFound(Exception):
    """Raised when a battlepass_seasons row cannot be found by ID."""


class ActiveSeasonConflict(Exception):
    """Raised when activating a season while another is already active (1 active at a time)."""


class MilestoneNumberConflict(Exception):
    """Raised when inserting a milestone with a milestone_number already used in the season."""


class SeasonNumberConflict(Exception):
    """Raised when creating a season with a season_number already taken."""


class MissionNotFound(Exception):
    """Raised when a missions row cannot be found by ID."""


class MissionUniquenessConflict(Exception):
    """Raised when creating/updating a mission whose (action_type, frequency, difficulty) collides."""


class RewardConfigNotFound(Exception):
    """Raised when a reward_config row cannot be found by ID."""


class RewardConfigUniquenessConflict(Exception):
    """Raised when creating/updating a reward_config whose action_type already exists."""


class StreakTierNotFound(Exception):
    """Raised when a streak_tiers row cannot be found by ID."""


class StreakTierUniquenessConflict(Exception):
    """Raised when creating/updating a streak_tier whose ``days`` value already exists."""

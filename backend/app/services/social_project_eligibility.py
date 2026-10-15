"""Social project content generation eligibility.

Correlated predicate determining whether a content project (TargetFanpage)
is eligible to generate content based on legacy Repliz connection status
or active Phone/Mixed SocialDestination routes.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, func, or_, select
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import TargetFanpage


def project_can_generate(fanpage_model=None):
    """Correlated SQLAlchemy predicate for TargetFanpage queries.

    A project can generate content when:
    - Its legacy Repliz connection is true (TargetFanpage.is_connected == True)
      AND repliz_account_id is non-null and non-empty, OR
    - It has an active Phone or Mixed SocialDestination (SocialDestination.is_active == True,
      mode in ('phone', 'mixed')).

    Note: TargetFanpage.is_active remains checked by the caller.
    """
    model = fanpage_model if fanpage_model is not None else TargetFanpage

    has_active_phone_route = exists(
        select(1)
        .where(
            SocialDestination.content_project_id == model.id,
            SocialDestination.is_active == True,
            SocialDestination.mode.in_(["phone", "mixed"]),
        )
        .correlate(model)
    )

    legacy_repliz_route = and_(
        model.is_connected == True,
        model.repliz_account_id.isnot(None),
        func.trim(model.repliz_account_id) != "",
    )

    return or_(
        legacy_repliz_route,
        has_active_phone_route,
    )

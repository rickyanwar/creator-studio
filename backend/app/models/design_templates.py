from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class DesignTemplate(Base):
    """Fabric.js canvas template with placeholder layers (Feature 2, Phase 2D).

    template_json is a Fabric.js canvas serialization; objects carry a custom
    `placeholderRole` property ("title" | "image") that the designer UI and the
    headless renderer both use to inject the article headline and photo.
    placeholder_config mirrors the spec: {"title_layer_id", "image_slot_id",
    "max_title_chars"} — derived on save for quick lookups.
    """

    __tablename__ = "design_templates"

    id = Column(Integer, primary_key=True, index=True)
    fanpage_id = Column(Integer, ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=True, index=True)  # null = shared template
    name = Column(String(128), nullable=False)
    template_json = Column(JSONB, nullable=True)       # Fabric.js canvas serialize (null until first save from editor)
    placeholder_config = Column(JSONB, nullable=True)
    canvas_width = Column(Integer, nullable=False, server_default="1080")
    canvas_height = Column(Integer, nullable=False, server_default="1080")
    is_default = Column(Boolean, default=False, nullable=False, server_default="false")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

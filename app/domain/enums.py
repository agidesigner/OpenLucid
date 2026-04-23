from enum import Enum


class MerchantType(str, Enum):
    GOODS = "goods"
    SERVICE = "service"
    HYBRID = "hybrid"


class OfferType(str, Enum):
    PRODUCT = "product"
    SERVICE = "service"
    BUNDLE = "bundle"
    SOLUTION = "solution"


class OfferModel(str, Enum):
    PHYSICAL_PRODUCT = "physical_product"
    DIGITAL_PRODUCT = "digital_product"
    LOCAL_SERVICE = "local_service"
    PROFESSIONAL_SERVICE = "professional_service"
    PACKAGE = "package"
    SOLUTION = "solution"


class ScopeType(str, Enum):
    MERCHANT = "merchant"
    OFFER = "offer"


class KnowledgeType(str, Enum):
    BRAND = "brand"
    AUDIENCE = "audience"
    SCENARIO = "scenario"
    # Usage-side pain + migration trigger. Distinct from OBJECTION (which is
    # *purchase-decision* hesitation, not usage pain).
    PAIN_POINT = "pain_point"
    SELLING_POINT = "selling_point"
    OBJECTION = "objection"
    PROOF = "proof"
    FAQ = "faq"
    GENERAL = "general"


class KnowledgeSourceType(str, Enum):
    MANUAL = "manual"
    FILE = "file"
    URL = "url"
    IMPORTED = "imported"


class AssetType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    URL = "url"
    COPY = "copy"


class AssetParseStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class AssetProcessingJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class AssetStatus(str, Enum):
    RAW = "raw"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class SliceType(str, Enum):
    CLIP = "clip"
    FRAME = "frame"
    QUOTE = "quote"
    SCENE = "scene"
    HIGHLIGHT = "highlight"


class TopicSourceMode(str, Enum):
    KB = "kb"
    EXTERNAL = "external"
    HYBRID = "hybrid"


class TopicStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class TagSource(str, Enum):
    SYSTEM = "system"
    MODEL = "model"
    USER = "user"


class MarketingObjective(str, Enum):
    REACH_GROWTH = "reach_growth"
    LEAD_GENERATION = "lead_generation"
    CONVERSION = "conversion"
    EDUCATION = "education"
    TRAFFIC_REDIRECT = "traffic_redirect"
    OTHER = "other"


class TrendStatus(str, Enum):
    UP = "up"
    FLAT = "flat"
    DOWN = "down"
    UNKNOWN = "unknown"


class KnowledgeLinkRole(str, Enum):
    CORE_MESSAGE = "core_message"
    PROOF = "proof"
    AUDIENCE_INSIGHT = "audience_insight"
    SCENARIO_ANCHOR = "scenario_anchor"
    OBJECTION = "objection"
    COMPLIANCE_NOTE = "compliance_note"
    GENERAL = "general"


class AssetLinkRole(str, Enum):
    HOOK_ASSET = "hook_asset"
    PROOF_ASSET = "proof_asset"
    TRUST_ASSET = "trust_asset"
    EXPLAINER_ASSET = "explainer_asset"
    CTA_ASSET = "cta_asset"
    GENERAL = "general"


class BrandKitStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class BrandKitAssetRole(str, Enum):
    REFERENCE_IMAGE = "reference_image"
    REFERENCE_VIDEO = "reference_video"
    LOGO = "logo"
    PRODUCT_REFERENCE = "product_reference"
    SCENE_REFERENCE = "scene_reference"
    PERSONA_REFERENCE = "persona_reference"
    STYLE_REFERENCE = "style_reference"
    NEGATIVE_REFERENCE = "negative_reference"
